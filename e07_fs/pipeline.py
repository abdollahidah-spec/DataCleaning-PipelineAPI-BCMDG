"""
e07_fs/pipeline.py
====================
Pipeline consolidée E07_FS (Flux Sortants) : 7 champs catégoriels — TypeSwift,
ModeReglement, Devise, NomDonneurOrdre, Beneficiaire, NatureEconomique, Pays —
et la validation des transactions (MontantTransaction, TauxDeChange,
DateTransaction, gabarit « sans activité ») en un seul run, un seul jeu de
livrables (voir shared/base_api_pipeline.py pour l'orchestration générique, et la
décision H du plan pour le choix d'un package autonome).

Produit est volontairement hors périmètre pour l'instant (décision métier) : non
normalisé, uniquement lu par le contrôle du gabarit « sans activité ».
"""
from __future__ import annotations

from shared.base_api_pipeline import BaseApiPipeline
from shared.field_processor import FieldProcessor

from e07_fs.fields.beneficiaire import build_beneficiaire_processor
from e07_fs.fields.devise import build_devise_processor
from e07_fs.fields.mode_reglement import build_mode_reglement_processor
from e07_fs.fields.nature_economique import build_nature_economique_processor
from e07_fs.fields.nomdonneurordre import build_nomdonneurordre_processor
from e07_fs.fields.pays import build_pays_processor
from e07_fs.fields.transactions import build_transactions_processor
from e07_fs.fields.typeswift import build_typeswift_processor

_CATEGORICAL_BUILDERS = {
    "TypeSwift": build_typeswift_processor,
    "ModeReglement": build_mode_reglement_processor,
    "Devise": build_devise_processor,
    "NomDonneurOrdre": build_nomdonneurordre_processor,
    "Beneficiaire": build_beneficiaire_processor,
    "NatureEconomique": build_nature_economique_processor,
    "Pays": build_pays_processor,
}


def build_e07_field_processors(cfg: dict) -> list:
    """
    Construit la liste des FieldProcessor à partir de cfg["fields"].
    `type: categorical` -> le `name` du champ sélectionne le module e07_fs.fields.* ;
    `type: transaction_validation` -> moteur de validation (transactions.py).
    """
    processors = []
    for field_cfg in cfg["fields"]:
        ftype = field_cfg["type"]
        name = field_cfg["name"]
        if ftype == "categorical":
            builder = _CATEGORICAL_BUILDERS.get(name)
            if builder is None:
                raise ValueError(f"Champ catégoriel inconnu pour E07_FS : {name!r}")
            processors.append(builder(field_cfg))
        elif ftype == "transaction_validation":
            processors.append(build_transactions_processor(field_cfg))
        else:
            raise ValueError(f"Type de champ inconnu pour E07_FS : {ftype!r}")
    return processors


class E07Pipeline(BaseApiPipeline):
    def _build_field_processors(self, cfg: dict) -> list[FieldProcessor]:
        return build_e07_field_processors(cfg)

    def build_reports_markdown(self, results: list, quality) -> str:
        """Un seul PDF (Rapport_Qualite_Outliers_*.pdf) combinant les deux rapports,
        séparés par un saut de page."""
        from shared.report_templates import DEFAULT_TOP_N_REFBANQUE_DETAIL
        from e07_fs.reports import build_outliers_report_markdown, build_quality_report_markdown

        quality_md = build_quality_report_markdown(quality)
        top_n = self.cfg.get("reports", {}).get("top_n_outliers_detail",
                                                 DEFAULT_TOP_N_REFBANQUE_DETAIL)
        outliers_md = build_outliers_report_markdown(quality, results, top_n=top_n)
        return f'{quality_md}\n\n<div style="page-break-before: always;"></div>\n\n{outliers_md}'
