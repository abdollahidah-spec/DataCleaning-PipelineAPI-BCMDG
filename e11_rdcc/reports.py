"""
e11_rdcc/reports.py
======================
Mapping E11-spécifique pour les rapports PDF (Rapport_Qualite_Outliers_*.pdf, un
seul fichier — voir E11Pipeline.build_reports_markdown() qui assemble les deux
sections avec un saut de page). Le gabarit/texte/structure Markdown lui-même est
générique (shared/report_templates.py, réutilisé tel quel par toutes les APIs) ;
ce module ne contient que ce qui est propre au schéma à 7 champs d'E11 (decision H).

Judgment call documenté (répartition par champ traité) : les règles numériques
n'ont pas de correspondance 1-pour-1 évidente avec les 7 champs métier du
gabarit BA — chaque anomalie n'est rattachée qu'à UN SEUL champ candidat :
  ARITHMETIC              -> soldeFinJournee          (la règle vérifie ce solde)
  NO_ACTIVITY_CONFORMITY  -> soldeFinJournee           (conformité du gabarit "sans activité")
  TEMPORAL_CONTINUITY     -> soldeDebutJournee         (compare au solde de fin de J-1)
  DATE_VALIDITY           -> dateFinJournee
  totalMvtsDebiteursJournee / totalMvtsCrediteurs ne portent aucune règle dédiée
  aujourd'hui -> toujours 0 dans ce rapport (pas d'anomalie perdue : chaque clé de
  outliers_by_champ est couverte par exactement une des 7 lignes, donc Total ==
  somme des lignes).
"""
from __future__ import annotations

from shared.quality_report import QualityReport
from shared.report_templates import build_outliers_report_markdown as _build_outliers_report_markdown
from shared.report_templates import build_quality_report_markdown  # noqa: F401 — ré-exporté tel quel, 100% générique

_CHAMP_JSON_LABELS = {
    "NomCorrespondant": "nomCorrespondant",
    "Devise": "devise",
    "ARITHMETIC": "soldeFinJournee",
    "NO_ACTIVITY_CONFORMITY": "soldeFinJournee",
    "TEMPORAL_CONTINUITY": "soldeDebutJournee",
    "DATE_VALIDITY": "dateFinJournee",
}

_OUTLIER_FIELD_ROWS = [
    "nomCorrespondant",
    "devise",
    "soldeDebutJournee",
    "totalMvtsDebiteursJournee",
    "totalMvtsCrediteurs",
    "soldeFinJournee",
    "dateFinJournee",
]


def build_outliers_report_markdown(report: QualityReport, results: list) -> str:
    return _build_outliers_report_markdown(
        report, results, _OUTLIER_FIELD_ROWS, _CHAMP_JSON_LABELS, numeric_id_col="NumCompte"
    )
