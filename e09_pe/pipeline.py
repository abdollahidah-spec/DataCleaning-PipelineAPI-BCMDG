"""
e09_pe/pipeline.py
=====================
Pipeline consolidée E09_PE : orchestre Devise (catégoriel) et la validation
Echeances (MontantEcheance/DateEcheance) en un seul run — un seul jeu de
livrables pour toute l'API (voir shared/base_api_pipeline.py pour l'orchestration
générique, et décision H du plan pour le choix d'un package self-contained).
"""
from __future__ import annotations

from shared.base_api_pipeline import BaseApiPipeline
from shared.field_processor import FieldProcessor

from e09_pe.fields.devise import build_devise_processor
from e09_pe.fields.echeances import build_echeances_processor


def build_e09_field_processors(cfg: dict) -> list:
    """
    Construit la liste des FieldProcessor à partir de cfg["fields"].
    `type: categorical` -> le `name` du champ sélectionne le module e09_pe.fields.* ;
    `type: numeric_validation` -> moteur de validation Echeances (voir echeances.py).
    """
    processors = []
    for field_cfg in cfg["fields"]:
        ftype = field_cfg["type"]
        name = field_cfg["name"]
        if ftype == "categorical":
            if name == "Devise":
                processors.append(build_devise_processor(field_cfg))
            else:
                raise ValueError(f"Champ catégoriel inconnu pour E09_PE : {name!r}")
        elif ftype == "numeric_validation":
            processors.append(build_echeances_processor(field_cfg))
        else:
            raise ValueError(f"Type de champ inconnu : {ftype!r}")
    return processors


class E09Pipeline(BaseApiPipeline):
    def _build_field_processors(self, cfg: dict) -> list[FieldProcessor]:
        return build_e09_field_processors(cfg)

    def build_reports_markdown(self, results: list, quality) -> str:
        """Un seul PDF (Rapport_Qualite_Outliers_*.pdf) combinant les deux rapports —
        contenu inchangé, simplement assemblés avec un saut de page entre les deux."""
        from e09_pe.reports import build_outliers_report_markdown, build_quality_report_markdown

        quality_md = build_quality_report_markdown(quality)
        outliers_md = build_outliers_report_markdown(quality, results)
        return f'{quality_md}\n\n<div style="page-break-before: always;"></div>\n\n{outliers_md}'
