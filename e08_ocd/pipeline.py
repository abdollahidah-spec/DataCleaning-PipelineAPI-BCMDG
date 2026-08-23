"""
e08_ocd/pipeline.py
=====================
Pipeline consolidée E08_OCD : orchestre Devise, NomCorrespondant, Produits,
NomDonneurOrdre, Beneficiaire, Pays (tous catégoriels) en un seul run — un seul
jeu de livrables pour toute l'API (voir shared/base_api_pipeline.py pour
l'orchestration générique, et décision H du plan pour le choix d'un package
self-contained).
"""
from __future__ import annotations

from shared.base_api_pipeline import BaseApiPipeline
from shared.field_processor import FieldProcessor

from e08_ocd.fields.beneficiaire import build_beneficiaire_processor
from e08_ocd.fields.devise import build_devise_processor
from e08_ocd.fields.nomcorrespondant import build_nomcorrespondant_processor
from e08_ocd.fields.nomdonneurordre import build_nomdonneurordre_processor
from e08_ocd.fields.pays import build_pays_processor
from e08_ocd.fields.produits import build_produits_processor

_CATEGORICAL_BUILDERS = {
    "Devise": build_devise_processor,
    "NomCorrespondant": build_nomcorrespondant_processor,
    "Produits": build_produits_processor,
    "NomDonneurOrdre": build_nomdonneurordre_processor,
    "Beneficiaire": build_beneficiaire_processor,
    "Pays": build_pays_processor,
}


def build_e08_field_processors(cfg: dict) -> list:
    """
    Construit la liste des FieldProcessor à partir de cfg["fields"].
    `type: categorical` -> le `name` du champ sélectionne le module e08_ocd.fields.*.
    """
    processors = []
    for field_cfg in cfg["fields"]:
        ftype = field_cfg["type"]
        name = field_cfg["name"]
        if ftype == "categorical":
            builder = _CATEGORICAL_BUILDERS.get(name)
            if builder is None:
                raise ValueError(f"Champ catégoriel inconnu pour E08_OCD : {name!r}")
            processors.append(builder(field_cfg))
        else:
            raise ValueError(f"Type de champ inconnu pour E08_OCD (uniquement categorical) : {ftype!r}")
    return processors


class E08Pipeline(BaseApiPipeline):
    def _build_field_processors(self, cfg: dict) -> list[FieldProcessor]:
        return build_e08_field_processors(cfg)

    def build_reports_markdown(self, results: list, quality) -> str:
        """Un seul PDF (Rapport_Qualite_Outliers_*.pdf) combinant les deux rapports —
        contenu inchangé, simplement assemblés avec un saut de page entre les deux."""
        from e08_ocd.reports import build_outliers_report_markdown, build_quality_report_markdown

        quality_md = build_quality_report_markdown(quality)
        outliers_md = build_outliers_report_markdown(quality, results)
        return f'{quality_md}\n\n<div style="page-break-before: always;"></div>\n\n{outliers_md}'
