"""
shared/base_api_pipeline.py
=============================
Orchestrateur générique "une API, plusieurs champs, un run" : charge les
données (fichier local / Initial Load / Incremental Load), exécute chaque
FieldProcessor configuré, assemble UN classeur de sortie (classification par
champ + anomalies numériques + Instructions), calcule le rapport de qualité,
écrit le fichier local, puis (sauf en mode fichier local) pousse vers
SharePoint, notifie par email et met à jour l'état incrémental — un fichier
JSON local (shared/state_store.py), PAS une table SQL Server : la base de
production reste strictement en lecture seule (aucune écriture/DDL autorisée).
Contient aussi les compteurs cumulatifs utilisés dans la section "stats
globales" de l'email.

Chaque API concrète (ex: e11_rdcc/pipeline.py) hérite de BaseApiPipeline et
implémente uniquement `_build_field_processors(cfg)`.

Note : `build_extraction_frame()` (colonnes demandées + leurs versions nettoyées)
n'est plus appelée par `run()` (le run automatisé ne produit plus d'extraction
ligne par ligne) mais reste disponible — c'est elle que réutilise l'outil
ad hoc `e11_rdcc/ad_hoc_extraction.py` pour les extractions sur mesure.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from shared.field_processor import CategoricalFieldProcessor, FieldProcessor, FieldResult
from shared.logging_conf import get_logger
from shared.quality_report import QualityReport, compute_quality_report
from shared.writer import empty_instructions_df, write_excel_sheets


class BaseApiPipeline:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.api_id = cfg["api_id"]
        self.logger = get_logger(self.api_id)
        self.field_processors: list[FieldProcessor] = self._build_field_processors(cfg)
        self._engine = None

    # ---- hook à implémenter par chaque API concrète -------------------------------
    def _build_field_processors(self, cfg: dict) -> list[FieldProcessor]:
        raise NotImplementedError

    # ---- chargement -----------------------------------------------------------------
    def _get_engine(self):
        if self._engine is None:
            from shared.db_connector import get_engine
            self._engine = get_engine()
        return self._engine

    def _get_state(self):
        from shared.state_store import get_run_state
        return get_run_state(self.api_id)

    def load_data(self, mode: str, override_input: Optional[str]) -> tuple[pd.DataFrame, str]:
        from shared.db_connector import load_file, load_table, load_table_delta

        if override_input:
            df = load_file(override_input, self.cfg)
            return df, "file"

        table = self.cfg["input"]["table_name"]
        dt_cr_col = self.cfg["input"].get("dt_cr_column", "dtCr")

        resolved_mode = mode
        if resolved_mode == "auto":
            state = self._get_state()
            resolved_mode = "incremental" if (state and state.last_dtcr_processed) else "initial"

        if resolved_mode == "initial":
            df = load_table(table)
        elif resolved_mode == "incremental":
            state = self._get_state()
            since = state.last_dtcr_processed if state else None
            if since is None:
                raise RuntimeError(
                    f"Mode incremental demandé mais aucun état trouvé pour {self.api_id} "
                    f"— lancer --mode initial une première fois."
                )
            df = load_table_delta(table, dt_cr_col, since)
        else:
            raise ValueError(f"Mode inconnu : {resolved_mode!r}")

        return df, resolved_mode

    # ---- traitement des champs -------------------------------------------------------
    def process_fields(self, df_raw: pd.DataFrame) -> tuple[pd.DataFrame, list]:
        """
        Retourne (df_final, results) où results est une liste de tuples
        (FieldProcessor, FieldResult), dans l'ordre de configuration.

        Aucun processor ne dépend de la colonne de sortie d'un autre (chacun lit
        le même snapshot brut) — les champs catégoriels (appels Claude, I/O-bound)
        s'exécutent donc en concurrence ; le reste (calculs locaux, pas d'I/O)
        s'exécute ensuite séquentiellement.
        """
        categorical = [p for p in self.field_processors if isinstance(p, CategoricalFieldProcessor)]
        other = [p for p in self.field_processors if p not in categorical]

        results_by_processor: dict = {}
        if categorical:
            with ThreadPoolExecutor(max_workers=len(categorical)) as ex:
                futures = {ex.submit(p.process, df_raw, self.api_id): p for p in categorical}
                for f in as_completed(futures):
                    p = futures[f]
                    results_by_processor[p] = f.result()

        for p in other:
            results_by_processor[p] = p.process(df_raw, self.api_id)

        combined = df_raw.copy()
        ordered_results = []
        for p in self.field_processors:
            r: FieldResult = results_by_processor[p]
            for c in r.df.columns:
                if c not in df_raw.columns:
                    combined[c] = r.df[c]
            ordered_results.append((p, r))

        return combined, ordered_results

    # ---- assemblage de la sortie -------------------------------------------------------
    def build_instructions_df(self, results: list):
        if not self.cfg.get("instructions", {}).get("prefill", True):
            return None
        frames = [p.instructions_rows(r.outliers_df) for p, r in results]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True)

    def assemble_output_workbook(self, results: list, instructions_df) -> dict:
        """
        UN classeur : une feuille de classification par champ catégoriel (table
        de mapping cumulative, voir CategoricalFieldProcessor) + une feuille
        d'anomalies par champ non-catégoriel (ex: Anomalies_Numeriques, lignes en
        erreur uniquement) + Instructions. Plus d'extraction ligne par ligne, plus
        de feuilles Outliers_{Field}/Rapport_Qualite dans ce fichier (simplification
        demandée — le rapport de qualité reste dans les logs et l'email).
        """
        sheets = {}
        for p, r in results:
            if r.classification_df is not None:
                name = r.sheet_names.get("classification", p.field_name)
                sheets[name] = r.classification_df
            else:
                name = r.sheet_names.get("outliers", f"Anomalies_{p.field_name}")
                cols = p.sheet_columns()
                sheets[name] = r.outliers_df[[c for c in cols if c in r.outliers_df.columns]] \
                    if cols else r.outliers_df
        sheets["Instructions"] = instructions_df if instructions_df is not None else empty_instructions_df()
        return sheets

    def build_extraction_frame(self, results: list, df_final: pd.DataFrame) -> pd.DataFrame:
        """Exclut les colonnes intermédiaires ; réinsère chaque {col_out} juste
        après son {col_in} (généralisation N-champs de l'ancien get_export_cols).
        Non utilisée par le run automatisé — réutilisée par l'outil ad hoc
        e11_rdcc/ad_hoc_extraction.py pour les extractions sur mesure."""
        exclude = set()
        insertions = {}
        for p, r in results:
            exclude.update(r.exclude_from_export)
            if isinstance(p, CategoricalFieldProcessor):
                insertions[p.col_in] = p.col_out

        base_cols = [c for c in df_final.columns if c not in exclude and c not in insertions.values()]
        ordered = []
        for c in base_cols:
            ordered.append(c)
            if c in insertions and insertions[c] in df_final.columns:
                ordered.append(insertions[c])
        for col_out in insertions.values():
            if col_out not in ordered and col_out in df_final.columns:
                ordered.append(col_out)

        return df_final[ordered]

    def compute_quality(self, results: list, df_final: pd.DataFrame,
                         started_at: datetime, finished_at: datetime, mode: str) -> QualityReport:
        categorical_fields = [
            (p.field_name, p.col_out, r.stats) for p, r in results if isinstance(p, CategoricalFieldProcessor)
        ]
        numeric_frames = [r.outliers_df for p, r in results if not isinstance(p, CategoricalFieldProcessor)]
        numeric_anomalies_df = pd.concat(numeric_frames, ignore_index=True) if numeric_frames else None

        ref_banque_col = self.cfg.get("columns", {}).get("ref_banque", "RefBanque")
        outlier_tag = self.cfg.get("outlier_tag", "OUTLIER")

        return compute_quality_report(
            api_id=self.api_id, mode=mode, started_at=started_at, finished_at=finished_at,
            df_final=df_final, categorical_fields=categorical_fields,
            numeric_anomalies_df=numeric_anomalies_df,
            ref_banque_col=ref_banque_col, outlier_tag=outlier_tag,
        )

    # ---- écriture / distribution -------------------------------------------------------
    def write_output(self, sheets: dict, run_dt: datetime) -> Path:
        import os

        # OUTPUT_BASE (.env) : chemin exact (local ou réseau) où stocker le livrable,
        # sans dépendre de SharePoint. Vide -> comportement inchangé (local_dir relatif
        # au répertoire courant). Défini -> écrit sous {OUTPUT_BASE}/{local_dir}.
        base_dir = os.getenv("OUTPUT_BASE", "")
        rel_dir = self.cfg["output"]["local_dir"]
        out_dir = Path(base_dir) / rel_dir if base_dir else Path(rel_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        return write_excel_sheets(sheets, out_dir / f"{self.api_id}_classification.xlsx")

    def upload_output(self, local_path: Path, dry_run: bool = False) -> bool:
        from shared.sharepoint_uploader import upload_file

        if dry_run:
            self.logger.info("[dry-run] SharePoint upload skipped for %s", local_path)
            return True
        return upload_file(local_path, dest_path=self.cfg["output"]["classification_path"])

    def notify(self, status: str, report: Optional[QualityReport], error: Optional[str] = None,
               exc: Optional[BaseException] = None, attachments: Optional[list] = None,
               cumulative=None, dry_run: bool = False) -> None:
        from shared.email_notifier import send_run_notification

        if not self.cfg.get("email", {}).get("enabled", True):
            return
        send_run_notification(status=status, report=report, error=error, exc=exc,
                               attachments=attachments, cumulative=cumulative, dry_run=dry_run)

    def persist_state(self, mode: str, status: str, max_dtcr, rows_processed: int,
                       outliers_this_run: int = 0):
        from shared.state_store import record_run_result

        return record_run_result(self.api_id, mode, status, max_dtcr,
                                  rows_processed, outliers_this_run)

    # ---- run complet ---------------------------------------------------------------------
    def run(self, mode: str = "auto", override_input: Optional[str] = None, dry_run: bool = False) -> dict:
        started_at = datetime.now()
        self.logger.info("=== Démarrage %s (mode=%s) ===", self.api_id, mode)

        try:
            df_raw, resolved_mode = self.load_data(mode, override_input)
            self.logger.info("%s lignes chargées (mode=%s)", len(df_raw), resolved_mode)

            df_final, results = self.process_fields(df_raw)
            finished_at = datetime.now()

            quality = self.compute_quality(results, df_final, started_at, finished_at, resolved_mode)
            for line in quality.to_log_lines():
                self.logger.info(line)

            instructions_df = self.build_instructions_df(results)
            sheets = self.assemble_output_workbook(results, instructions_df)
            local_path = self.write_output(sheets, started_at)

            offline = resolved_mode == "file"
            cumulative_state = None
            if not offline:
                dt_cr_col = self.cfg["input"].get("dt_cr_column", "dtCr")
                from shared.db_connector import get_max_dtcr
                max_dtcr = get_max_dtcr(df_raw, dt_cr_col)

                self.upload_output(local_path, dry_run=dry_run)
                cumulative_state = self.persist_state(
                    resolved_mode, "OK", max_dtcr, len(df_raw), quality.n_outlier_rows
                )
                self.notify("OK", quality, attachments=[local_path], cumulative=cumulative_state, dry_run=dry_run)

            self.logger.info("=== Terminé %s : OK ===", self.api_id)
            return {"status": "OK", "api_id": self.api_id, "mode": resolved_mode,
                    "quality": quality, "path": local_path}

        except Exception as exc:
            self.logger.exception("=== Échec %s ===", self.api_id)
            cumulative_state = None
            if not override_input:
                try:
                    fallback_mode = mode if mode in ("initial", "incremental") else "incremental"
                    cumulative_state = self.persist_state(fallback_mode, "KO", None, 0, 0)
                except Exception:
                    self.logger.exception("Échec enregistrement état KO")
            try:
                self.notify("KO", None, error=str(exc), exc=exc, cumulative=cumulative_state, dry_run=dry_run)
            except Exception:
                self.logger.exception("Échec notification KO")
            raise
