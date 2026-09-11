"""
e07_fs/reports.py
===================
Mapping E07-spécifique pour le rapport PDF (Rapport_Qualite_Outliers_*.pdf). Le
gabarit lui-même est générique (shared/report_templates.py) ; ce module ne porte
que ce qui est propre au schéma d'E07 (décision H).

Libellés affichés = noms de champs du ticket (camelCase, `typeSwfit` compris).
Rattachement des règles de validation à un champ :
  AMOUNT_NON_NEGATIVE    -> montantTransaction
  RATE_NON_NEGATIVE      -> tauxDeChange
  DATE_VALIDITY          -> dateTransaction
  NO_ACTIVITY_CONFORMITY -> referenceTransaction (c'est elle qui désigne un message
                            « sans activité », voir e07_fs/fields/transactions.py)
"""
from __future__ import annotations

from shared.quality_report import QualityReport
from shared.report_templates import DEFAULT_TOP_N_REFBANQUE_DETAIL
from shared.report_templates import build_outliers_report_markdown as _build_outliers_report_markdown
from shared.report_templates import build_quality_report_markdown  # noqa: F401 — ré-exporté tel quel, 100% générique

_CHAMP_JSON_LABELS = {
    "TypeSwift": "typeSwfit",
    "ModeReglement": "modeReglement",
    "Devise": "devise",
    "NomDonneurOrdre": "nomDonneurOrdre",
    "Beneficiaire": "beneficiaire",
    "NatureEconomique": "natureEconomique",
    "Pays": "pays",
    "AMOUNT_NON_NEGATIVE": "montantTransaction",
    "RATE_NON_NEGATIVE": "tauxDeChange",
    "DATE_VALIDITY": "dateTransaction",
    "NO_ACTIVITY_CONFORMITY": "referenceTransaction",
}

_OUTLIER_FIELD_ROWS = [
    "referenceTransaction",
    "dateTransaction",
    "typeSwfit",
    "modeReglement",
    "devise",
    "montantTransaction",
    "tauxDeChange",
    "nomDonneurOrdre",
    "beneficiaire",
    "natureEconomique",
    "pays",
]


def build_outliers_report_markdown(
    report: QualityReport, results: list, top_n: int = DEFAULT_TOP_N_REFBANQUE_DETAIL
) -> str:
    return _build_outliers_report_markdown(
        report, results, _OUTLIER_FIELD_ROWS, _CHAMP_JSON_LABELS,
        numeric_id_col="ReferenceTransaction", top_n=top_n,
    )
