"""
e11_rdcc/pipeline.py
======================
Pipeline consolidée E11_RDCC : orchestre NomCorrespondant, Devise (catégoriels)
et la cohérence numérique SoldesRDCC en un seul run — un seul jeu de livrables
pour toute l'API (voir shared/base_api_pipeline.py pour l'orchestration générique).
"""
from __future__ import annotations

import pandas as pd

from shared.base_api_pipeline import BaseApiPipeline
from shared.field_processor import FieldProcessor

from e11_rdcc.fields.devise import build_devise_processor
from e11_rdcc.fields.nomcorrespondant import build_nomcorrespondant_processor
from e11_rdcc.fields.numeric_coherence import build_numeric_coherence_processor
from e11_rdcc.global_na import GLOBAL_NA_COLUMN, compute_global_no_activity_column


def build_e11_field_processors(cfg: dict) -> list:
    """
    Construit la liste des FieldProcessor à partir de cfg["fields"].
    `type: categorical` -> le `name` du champ sélectionne le module e11_rdcc.fields.* ;
    `type: numeric_coherence` -> moteur de cohérence générique (voir numeric_coherence.py).

    Point d'extension pour une future API : dupliquer ce fichier avec ses propres
    champs, sans dépendre de celui-ci (décision H du plan).
    """
    processors = []
    for field_cfg in cfg["fields"]:
        ftype = field_cfg["type"]
        name = field_cfg["name"]
        if ftype == "categorical":
            if name == "NomCorrespondant":
                processors.append(build_nomcorrespondant_processor(field_cfg))
            elif name == "Devise":
                processors.append(build_devise_processor(field_cfg))
            else:
                raise ValueError(f"Champ catégoriel inconnu pour E11_RDCC : {name!r}")
        elif ftype == "numeric_coherence":
            processors.append(build_numeric_coherence_processor(field_cfg))
        else:
            raise ValueError(f"Type de champ inconnu : {ftype!r}")
    return processors


class E11Pipeline(BaseApiPipeline):
    def _build_field_processors(self, cfg: dict) -> list[FieldProcessor]:
        return build_e11_field_processors(cfg)

    def preprocess(self, df_raw: pd.DataFrame) -> pd.DataFrame:
        """
        Précalcule la colonne globale de non-activité (voir e11_rdcc/global_na.py)
        AVANT que NomCorrespondant/Devise ne s'exécutent — ces deux champs pointent
        leur `ref_transaction` (YAML) vers cette colonne plutôt que vers un témoin
        partiel isolé (NumCompte seul), pour que la règle NA soit tranchée une seule
        fois, GLOBALEMENT, à partir de tous les champs concernés (correctif BA).
        """
        numeric_field_cfg = next(
            (f for f in self.cfg["fields"] if f["type"] == "numeric_coherence"), None
        )
        if numeric_field_cfg is None:
            return df_raw

        df_raw = df_raw.copy()
        df_raw[GLOBAL_NA_COLUMN] = compute_global_no_activity_column(
            df_raw, numeric_field_cfg["columns"]
        )
        return df_raw

    def preprocess_exclude_columns(self) -> list[str]:
        return [GLOBAL_NA_COLUMN]

    def build_reports_markdown(self, results: list, quality) -> str:
        """Un seul PDF (Rapport_Qualite_Outliers_*.pdf) combinant les deux rapports —
        contenu inchangé, simplement assemblés avec un saut de page entre les deux."""
        from shared.report_templates import DEFAULT_TOP_N_REFBANQUE_DETAIL
        from e11_rdcc.reports import build_outliers_report_markdown, build_quality_report_markdown

        quality_md = build_quality_report_markdown(quality)
        top_n = self.cfg.get("reports", {}).get("top_n_outliers_detail",
                                                 DEFAULT_TOP_N_REFBANQUE_DETAIL)
        outliers_md = build_outliers_report_markdown(quality, results, top_n=top_n)
        return f'{quality_md}\n\n<div style="page-break-before: always;"></div>\n\n{outliers_md}'
