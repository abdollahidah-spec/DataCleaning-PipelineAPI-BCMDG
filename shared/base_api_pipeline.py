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
    def __init__(self, cfg: dict, config_source: str = ""):
        from dotenv import load_dotenv
        from shared.config_validate import validate_config

        # AVANT tout — certains champs lisent des variables .env dès leur
        # construction (ex: DGI_BASE_PATH pour e08_ocd/fields/nomdonneurordre.py,
        # chargé eagerly dans _build_field_processors() ci-dessous). Auparavant
        # .env n'était chargé qu'en effet de bord de l'import de shared/db_connector.py
        # — trop tard pour un champ qui a besoin d'une variable d'env avant même
        # le premier accès DB. load_dotenv() est sans risque à rappeler plusieurs
        # fois (idempotent). Pas d'override=True ici (contrairement à
        # shared/db_connector.py) : cet appel tourne à CHAQUE construction de
        # pipeline (donc à chaque test qui monkeypatch un os.environ le temps
        # d'un test) — l'override authoritatif de .env sur les credentials DB est
        # géré une seule fois, à l'import de db_connector.py.
        load_dotenv()

        validate_config(cfg, source=config_source)
        self.cfg = cfg
        self.api_id = cfg["api_id"]
        self.logger = get_logger(self.api_id)
        self.field_processors: list[FieldProcessor] = self._build_field_processors(cfg)
        self._engine = None

    # ---- hook à implémenter par chaque API concrète -------------------------------
    def _build_field_processors(self, cfg: dict) -> list[FieldProcessor]:
        raise NotImplementedError

    def preprocess(self, df_raw: pd.DataFrame) -> pd.DataFrame:
        """
        Hook optionnel : transformation appliquée au snapshot brut AVANT l'exécution
        des FieldProcessor — pour des colonnes synthétiques partagées entre plusieurs
        champs (ex: la colonne de non-activité globale d'E11, voir e11_rdcc/global_na.py
        et E11Pipeline.preprocess()). Identité par défaut.
        """
        return df_raw

    def preprocess_exclude_columns(self) -> list[str]:
        """Colonnes synthétiques ajoutées par preprocess() à exclure de toute sortie
        ligne-par-ligne (ex: extraction ad hoc) — jamais destinées à être exposées
        telles quelles. Vide par défaut."""
        return []

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
        """
        Onglet "Instructions" = HISTORIQUE, en lecture seule, des corrections
        manuelles déjà appliquées via `{api}/apply_corrections.py` (un fichier
        d'historique par API, voir shared/corrections_history.py).

        Vide au tout premier run — personne n'a encore soumis de correction — puis
        s'enrichit à chaque `apply_corrections`. Ce n'est donc PAS une liste
        pré-remplie d'outliers à corriger : les valeurs à valider se lisent dans
        les feuilles de classification (lignes `OUTLIER`) et dans le rapport PDF.
        """
        from shared.corrections_history import load_history_df

        return load_history_df(self.api_id)

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
        exclude = set(self.preprocess_exclude_columns())
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

    def _attach_cumulative_stats(self, quality: QualityReport, results: list, offline: bool) -> None:
        """
        Renseigne sur `quality` les compteurs "ensemble de l'historique" (rapport
        PDF, shared/report_templates.py) — distincts des compteurs "cette exécution"
        déjà posés par compute_quality() (email, shared/email_notifier.py).

        Lignes/conformité : state_store.peek_cumulative() — calcule ce que serait
        l'état SANS l'écrire (persist_state() reste appelé plus tard, après
        l'upload/écriture de sortie, pour ne jamais avancer l'état avant qu'un run
        ne soit intégralement livré ; les deux lectures de l'état existant
        renvoient donc le même résultat, pas de risque de divergence).

        Valeurs distinctes (catégoriel) : table de classification cumulative de
        chaque champ (déjà indépendante du run courant, voir field_processor.py) —
        valable aussi bien en mode fichier local qu'en mode base de données.

        En mode fichier local (--input, pas d'état persistant) : ce run EST
        considéré comme tout l'historique, pour ne jamais afficher un rapport
        vide/à zéro lors d'un test offline.
        """
        from shared.field_processor import cumulative_already_clean_stats, cumulative_classification_stats
        from shared.state_store import peek_cumulative

        if offline:
            cum_rows, cum_outliers = quality.n_rows, quality.n_outlier_rows
        else:
            existing = self._get_state()
            cum_rows, cum_outliers, _ = peek_cumulative(existing, quality.n_rows, quality.n_outlier_rows)

        quality.cumulative_n_rows = cum_rows
        quality.cumulative_n_outlier_rows = cum_outliers
        quality.cumulative_taux_conformite_pct = (
            round(100 * (cum_rows - cum_outliers) / cum_rows, 2) if cum_rows else 0.0
        )

        n_distinct_total, n_distinct_normalized = cumulative_classification_stats(results)
        quality.cumulative_n_distinct_total = n_distinct_total
        quality.cumulative_n_distinct_normalized = n_distinct_normalized
        quality.cumulative_taux_normalisation_pct = (
            round(100 * n_distinct_normalized / n_distinct_total, 2) if n_distinct_total else 0.0
        )

        _, n_already_clean = cumulative_already_clean_stats(results)
        quality.cumulative_n_already_clean = n_already_clean
        # 3 catégories mutuellement exclusives, qui totalisent n_distinct_total :
        # déjà propre / nettoyée avec succès par la pipeline / outlier non résolue.
        n_distinct_outliers = n_distinct_total - n_distinct_normalized
        n_distinct_cleaned = n_distinct_normalized - n_already_clean
        quality.cumulative_taux_deja_propre_pct = (
            round(100 * n_already_clean / n_distinct_total, 2) if n_distinct_total else 0.0
        )
        quality.cumulative_taux_nettoyage_pct = (
            round(100 * n_distinct_cleaned / n_distinct_total, 2) if n_distinct_total else 0.0
        )
        quality.cumulative_taux_outliers_distinct_pct = (
            round(100 * n_distinct_outliers / n_distinct_total, 2) if n_distinct_total else 0.0
        )

    def _resolve_output_dir(self) -> Path:
        """
        Répertoire de sortie local, utilisé par write_output() et
        _generate_pdf_reports(). OUTPUT_BASE non défini (.env) : chemin relatif
        `output.local_dir` du YAML, inchangé (usage repo/dev, ex: "e11_rdcc/outputs/").
        OUTPUT_BASE défini : `{OUTPUT_BASE}/{API_ID EN MAJUSCULES}/` — un
        sous-dossier par API nommé directement d'après son api_id (pas de niveau
        "outputs/" intermédiaire), pour que les 3 API lancées en parallèle écrivent
        chacune dans son propre dossier, sans collision, à un chemin prévisible
        côté exploitation (ex: {OUTPUT_BASE}/E11_RDCC/).
        """
        import os

        base_dir = os.getenv("OUTPUT_BASE", "").strip()
        if base_dir:
            return Path(base_dir) / self.api_id.upper()
        return Path(self.cfg["output"]["local_dir"])

    # ---- rapport PDF (optionnel, hook subclass) -------------------------------------------
    def build_reports_markdown(self, results: list, quality: QualityReport) -> Optional[str]:
        """
        Hook optionnel : retourne le Markdown du rapport PDF unique joint à l'email
        (Rapport_Qualite_Outliers_*.pdf — qualité + outliers combinés dans UN seul
        document, contenu inchangé). Dépend du schéma de chaque API — implémenté par
        la sous-classe concrète (decision H, voir e11_rdcc/reports.py). None (défaut)
        = pas de rapport PDF pour cette API.
        """
        return None

    def _generate_pdf_reports(self, results: list, quality: QualityReport, run_dt: datetime) -> list:
        report_md = self.build_reports_markdown(results, quality)
        if report_md is None:
            return []

        # Sous-dossier "Rapport" dédié — SÉPARÉ du classeur de classification
        # (write_output()). Le PDF est daté et s'accumule à chaque run ; le
        # classeur, lui, reste seul, jamais daté/déplacé, branché au BI (voir
        # write_output() et _resolve_output_dir()).
        out_dir = self._resolve_output_dir() / "Rapport"
        out_dir.mkdir(parents=True, exist_ok=True)
        date_tag = run_dt.strftime("%Y%m%d")

        try:
            from shared.pdf_report import markdown_to_pdf
            pdf_path = markdown_to_pdf(
                report_md, out_dir / f"Rapport_Qualite_Outliers_{self.api_id}_{date_tag}.pdf"
            )
            return [pdf_path]
        except Exception:
            # Best-effort, comme l'upload SharePoint/l'email : un échec de génération
            # PDF ne doit pas transformer un run par ailleurs réussi en échec.
            self.logger.exception("Échec génération du rapport PDF — email envoyé sans PDF joint")
            return []

    # ---- écriture / distribution -------------------------------------------------------
    def write_output(self, sheets: dict, run_dt: datetime) -> Path:
        """
        Chemin et nom de fichier FIXES (`{api_id}_classification.xlsx`, jamais daté,
        jamais déplacé) — le fichier lui-même n'est jamais supprimé/recréé sous un
        autre nom, seul son CONTENU est actualisé (écrasé) à chaque run. Essentiel
        pour un branchement Power BI stable : le point de connexion ne doit jamais
        changer. Les feuilles de classification catégorielles sont cumulatives
        (référentiel + cache, voir CategoricalFieldProcessor) donc aucune valeur
        déjà connue ne disparaît d'un run à l'autre même si absente du delta.
        """
        out_dir = self._resolve_output_dir()
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
               pdf_report_paths: Optional[list] = None, dry_run: bool = False) -> None:
        from shared.email_notifier import send_run_notification

        if not self.cfg.get("email", {}).get("enabled", True):
            return
        endpoint_label = self.cfg.get("email", {}).get("display_name", self.api_id)
        sent = send_run_notification(status=status, report=report, error=error, exc=exc,
                                      attachments=attachments, pdf_report_paths=pdf_report_paths,
                                      endpoint_label=endpoint_label, dry_run=dry_run)
        if not sent and not dry_run:
            self.logger.warning(
                "Email de notification NON envoyé — configuration SMTP/EMAIL_TO incomplète "
                "ou échec d'envoi (voir détail ci-dessus / .env.example)."
            )

    def persist_state(self, mode: str, status: str, max_dtcr, rows_processed: int,
                       outliers_this_run: int = 0):
        from shared.state_store import record_run_result

        return record_run_result(self.api_id, mode, status, max_dtcr,
                                  rows_processed, outliers_this_run)

    # ---- run complet ---------------------------------------------------------------------
    def run(self, mode: str = "auto", override_input: Optional[str] = None, dry_run: bool = False) -> dict:
        from shared.quality_report import format_duration_mmss

        started_at = datetime.now()
        champs_traites = ", ".join(p.field_name for p in self.field_processors)
        # L'horodatage de chaque ligne est déjà fourni par le format du logger
        # (voir shared/logging_conf.py) — pas besoin de le répéter dans le message.
        self.logger.info("=== Démarrage %s ===", self.api_id)
        self.logger.info("Mode : %s", mode)
        self.logger.info("Champs traités : %s", champs_traites)

        try:
            df_raw, resolved_mode = self.load_data(mode, override_input)
            self.logger.info("%s lignes chargées (mode=%s)", len(df_raw), resolved_mode)
            df_raw = self.preprocess(df_raw)

            df_final, results = self.process_fields(df_raw)
            finished_at = datetime.now()

            quality = self.compute_quality(results, df_final, started_at, finished_at, resolved_mode)
            for line in quality.to_log_lines():
                self.logger.info(line)

            offline = resolved_mode == "file"
            self._attach_cumulative_stats(quality, results, offline)

            instructions_df = self.build_instructions_df(results)
            sheets = self.assemble_output_workbook(results, instructions_df)
            local_path = self.write_output(sheets, started_at)
            pdf_paths = self._generate_pdf_reports(results, quality, started_at)

            if not offline:
                dt_cr_col = self.cfg["input"].get("dt_cr_column", "dtCr")
                from shared.db_connector import get_max_dtcr
                max_dtcr = get_max_dtcr(df_raw, dt_cr_col)

                self.upload_output(local_path, dry_run=dry_run)
                self.persist_state(resolved_mode, "OK", max_dtcr, len(df_raw), quality.n_outlier_rows)
                self.notify("OK", quality, attachments=[local_path, *pdf_paths],
                            pdf_report_paths=pdf_paths, dry_run=dry_run)

            run_finished_at = datetime.now()
            self.logger.info("=== Terminé %s ===", self.api_id)
            self.logger.info("Statut : OK")
            self.logger.info("Durée totale : %s", format_duration_mmss((run_finished_at - started_at).total_seconds()))
            return {"status": "OK", "api_id": self.api_id, "mode": resolved_mode,
                    "quality": quality, "path": local_path, "pdf_paths": pdf_paths}

        except Exception as exc:
            run_finished_at = datetime.now()
            self.logger.exception("=== Échec %s ===", self.api_id)  # trace complète -> log uniquement, jamais l'email
            self.logger.error("Statut : KO")
            self.logger.error("Durée totale : %s", format_duration_mmss((run_finished_at - started_at).total_seconds()))
            # Mode fichier local (--input) : 100% hors ligne, y compris en cas
            # d'échec. Ni état incrémental, ni email — un test offline qui plante
            # ne doit pas envoyer un vrai email KO aux destinataires de prod
            # (constaté en test : un `--input` en erreur notifiait la boîte métier).
            if override_input:
                self.logger.info("[mode fichier] Notification KO ignorée (exécution hors ligne)")
                raise

            try:
                fallback_mode = mode if mode in ("initial", "incremental") else "incremental"
                self.persist_state(fallback_mode, "KO", None, 0, 0)
            except Exception:
                self.logger.exception("Échec enregistrement état KO")
            try:
                self.notify("KO", None, error=str(exc), exc=exc, dry_run=dry_run)
            except Exception:
                self.logger.exception("Échec notification KO")
            raise
