"""
e08_ocd/reports.py
======================
Mapping E08-spécifique pour les rapports PDF (Rapport_Qualite_Outliers_*.pdf, un
seul fichier — voir E08Pipeline.build_reports_markdown() qui assemble les deux
sections avec un saut de page). Le gabarit/texte/structure Markdown lui-même est
générique (shared/report_templates.py, réutilisé tel quel par toutes les APIs) ;
ce module ne contient que ce qui est propre au schéma d'E08 (decision H).

Tous les champs d'E08 sont catégoriels — pas de champ numérique/date, donc
`numeric_id_col` n'est jamais réellement utilisé (aucun FieldResult non-catégoriel
dans `results`), mais reste requis par la signature générique de
shared/report_templates.py.
"""
from __future__ import annotations

from shared.quality_report import QualityReport
from shared.report_templates import build_outliers_report_markdown as _build_outliers_report_markdown
from shared.report_templates import build_quality_report_markdown  # noqa: F401 — ré-exporté tel quel, 100% générique

_CHAMP_JSON_LABELS = {
    "Devise": "devise",
    "NomCorrespondant": "nomCorrespondant",
    "Produits": "produits",
    "NomDonneurOrdre": "nomDonneurOrdre",
    "Beneficiaire": "beneficiaire",
    "Pays": "pays",
}

_OUTLIER_FIELD_ROWS = [
    "devise",
    "nomCorrespondant",
    "produits",
    "nomDonneurOrdre",
    "beneficiaire",
    "pays",
]


def build_outliers_report_markdown(report: QualityReport, results: list) -> str:
    return _build_outliers_report_markdown(
        report, results, _OUTLIER_FIELD_ROWS, _CHAMP_JSON_LABELS, numeric_id_col="NumCredoc"
    )
