"""
e09_pe/reports.py
=====================
Mapping E09-spécifique pour les rapports PDF (Rapport_Qualite_Outliers_*.pdf, un
seul fichier — voir E09Pipeline.build_reports_markdown() qui assemble les deux
sections avec un saut de page). Le gabarit/texte/structure Markdown lui-même est
générique (shared/report_templates.py, réutilisé tel quel par toutes les APIs) ;
ce module ne contient que ce qui est propre au schéma à 3 champs contrôlés d'E09
(decision H).

NumCredoc n'apparaît PAS dans la répartition "par champ traité" : ce n'est pas un
champ normalisé/contrôlé en soi, juste l'identifiant (témoin NA de Devise, colonne
"valeur source" des anomalies Echeances) — voir e09_pe/fields/echeances.py.
"""
from __future__ import annotations

from shared.quality_report import QualityReport
from shared.report_templates import build_outliers_report_markdown as _build_outliers_report_markdown
from shared.report_templates import build_quality_report_markdown  # noqa: F401 — ré-exporté tel quel, 100% générique

_CHAMP_JSON_LABELS = {
    "Devise": "devise",
    "AMOUNT_POSITIVE": "montantEcheance",
    "DATE_VALIDITY": "dateEcheance",
}

_OUTLIER_FIELD_ROWS = [
    "devise",
    "montantEcheance",
    "dateEcheance",
]


def build_outliers_report_markdown(report: QualityReport, results: list) -> str:
    return _build_outliers_report_markdown(
        report, results, _OUTLIER_FIELD_ROWS, _CHAMP_JSON_LABELS, numeric_id_col="NumCredoc"
    )
