"""
Vérifie le contenu du rapport PDF E08 (Markdown avant conversion) — même gabarit
BA que E11/E09, avec le mapping à 3 champs (phase 1, uniquement catégoriels)
propre à E08 (pas de ligne numérique/date pour l'instant).
"""
from datetime import datetime

import pandas as pd

from e08_ocd.reports import build_outliers_report_markdown, build_quality_report_markdown
from shared.field_processor import CategoricalFieldProcessor
from shared.quality_report import QualityReport


def _fake_treating_fn(df: pd.DataFrame, field_col: str, api_id=None) -> pd.DataFrame:
    mapping = {"A": "X", "B": "Y"}
    df = df.copy()
    df[f"{field_col}_Normalisé"] = df[field_col].map(mapping).fillna("OUTLIER")
    df[f"{field_col}_method"] = df[f"{field_col}_Normalisé"].map(lambda v: "MAP" if v != "OUTLIER" else "OUTLIER")
    return df


def _processor(field_name: str, col_in: str, col_out: str) -> CategoricalFieldProcessor:
    return CategoricalFieldProcessor(
        field_name=field_name, treating_fn=_fake_treating_fn,
        treating_kwargs={"field_col": col_in, "api_id": None},
        col_in=col_in, col_out=col_out, ref_banque_col="RefBanque",
        outlier_tag="OUTLIER", exclude_suffixes=(), clean_fn=lambda x: str(x).strip(),
    )


def _quality_report() -> QualityReport:
    return QualityReport(
        api_id="E08_OCD", mode="incremental",
        started_at=datetime(2026, 8, 23, 10, 0), finished_at=datetime(2026, 8, 23, 10, 0, 30),
        n_rows=40, taux_conformite_pct=55.0,
        per_field_stats={
            "Devise": {"n_distinct_total": 5, "n_distinct_normalized": 4},
            "NomCorrespondant": {"n_distinct_total": 6, "n_distinct_normalized": 5},
            "Produits": {"n_distinct_total": 8, "n_distinct_normalized": 6},
        },
        outliers_by_champ={"Devise": 2, "NomCorrespondant": 3, "Produits": 1},
        # Voir tests/test_e11_reports.py::_quality_report — même principe (rapport
        # PDF = compteurs "ensemble de l'historique").
        cumulative_n_rows=40,
        cumulative_taux_conformite_pct=55.0,
        cumulative_n_distinct_total=19,
        cumulative_n_distinct_normalized=15,
        cumulative_taux_normalisation_pct=round(100 * 15 / 19, 2),
        cumulative_n_already_clean=10,
        cumulative_taux_deja_propre_pct=round(100 * 10 / 19, 2),
        cumulative_taux_nettoyage_pct=round(100 * 5 / 19, 2),   # 15 normalisées - 10 déjà propres
        cumulative_taux_outliers_distinct_pct=round(100 * 4 / 19, 2),  # 19 total - 15 normalisées
    )


def _results() -> list:
    devise = _processor("Devise", "Devise", "Devise_Normalisé")
    devise_result = devise.process(pd.DataFrame({"RefBanque": ["B1"], "Devise": ["A"]}), api_id="E08_OCD")

    nomcorr = _processor("NomCorrespondant", "NomCorrespondant", "NomCorrespondant_Normalisé")
    nomcorr_result = nomcorr.process(
        pd.DataFrame({"RefBanque": ["B1", "B2"], "NomCorrespondant": ["Z1", "Z2"]}), api_id="E08_OCD"
    )

    produits = _processor("Produits", "Produits", "Produits_Normalisé")
    produits_result = produits.process(pd.DataFrame({"RefBanque": ["B3"], "Produits": ["Z3"]}), api_id="E08_OCD")

    return [(devise, devise_result), (nomcorr, nomcorr_result), (produits, produits_result)]


def test_quality_report_markdown_uses_generic_shared_template():
    md = build_quality_report_markdown(_quality_report())
    assert "Rapport de qualité des traitements — E08_OCD" in md
    assert "Nombre total de lignes traitées : 40" in md


def test_outliers_report_has_three_rows_no_numcredoc_row():
    md = build_outliers_report_markdown(_quality_report(), _results())

    for label in ("devise", "nomCorrespondant", "produits"):
        assert label in md
    assert "numCredoc" not in md

    assert "| devise | 2 |" in md
    assert "| nomCorrespondant | 3 |" in md
    assert "| produits | 1 |" in md
    assert "**Total** | **6**" in md


def test_outliers_report_refbanque_detail_present():
    md = build_outliers_report_markdown(_quality_report(), _results())
    assert "Z1" in md
    assert "Z2" in md
    assert "Z3" in md
