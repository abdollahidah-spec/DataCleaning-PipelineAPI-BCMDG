"""
Vérifie le contenu des deux sections du rapport PDF E09 (Markdown avant
conversion) — même gabarit BA que E11, mais avec le mapping à 3 champs propre à
E09 (pas de ligne numCredoc — voir e09_pe/reports.py).
"""
from datetime import datetime

import pandas as pd

from e09_pe.reports import build_outliers_report_markdown, build_quality_report_markdown
from shared.field_processor import CategoricalFieldProcessor, FieldResult
from shared.quality_report import QualityReport


def _fake_treating_fn(df: pd.DataFrame, field_col: str, api_id=None) -> pd.DataFrame:
    mapping = {"A": "X", "B": "Y"}
    df = df.copy()
    df[f"{field_col}_Normalisé"] = df[field_col].map(mapping).fillna("OUTLIER")
    df[f"{field_col}_method"] = df[f"{field_col}_Normalisé"].map(lambda v: "MAP" if v != "OUTLIER" else "OUTLIER")
    return df


def _devise_processor() -> CategoricalFieldProcessor:
    return CategoricalFieldProcessor(
        field_name="Devise", treating_fn=_fake_treating_fn,
        treating_kwargs={"field_col": "Devise", "api_id": None},
        col_in="Devise", col_out="Devise_Normalisé", ref_banque_col="RefBanque",
        outlier_tag="OUTLIER", exclude_suffixes=(), clean_fn=lambda x: str(x).strip(),
    )


class _DummyEcheancesProcessor:
    field_name = "Echeances"


def _quality_report() -> QualityReport:
    return QualityReport(
        api_id="E09_PE", mode="incremental",
        started_at=datetime(2026, 8, 14, 10, 0), finished_at=datetime(2026, 8, 14, 10, 0, 20),
        n_rows=50, taux_conformite_pct=60.0,
        per_field_stats={"Devise": {"n_distinct_total": 5, "n_distinct_normalized": 3}},
        outliers_by_champ={"Devise": 4, "AMOUNT_POSITIVE": 3, "DATE_VALIDITY": 2},
        # Voir tests/test_e11_reports.py::_quality_report — même principe (rapport
        # PDF = compteurs "ensemble de l'historique").
        cumulative_n_rows=50,
        cumulative_taux_conformite_pct=60.0,
        cumulative_n_distinct_total=5,
        cumulative_n_distinct_normalized=3,
        cumulative_taux_normalisation_pct=60.0,
        cumulative_n_already_clean=2,
        cumulative_taux_deja_propre_pct=40.0,
        cumulative_taux_nettoyage_pct=20.0,           # 3 normalisées - 2 déjà propres = 1/5
        cumulative_taux_outliers_distinct_pct=40.0,   # 5 total - 3 normalisées = 2/5
    )


def _results() -> list:
    devise = _devise_processor()
    devise_result = devise.process(
        pd.DataFrame({"RefBanque": ["B1", "B1", "B2"], "Devise": ["Z1", "Z1", "Z2"]}),
        api_id="E09_PE",
    )

    numeric_df = pd.DataFrame([
        {"RefBanque": "B1", "Rule": "AMOUNT_POSITIVE", "Severity": "ERROR", "NumCredoc": "CD1"},
        {"RefBanque": "B2", "Rule": "DATE_VALIDITY", "Severity": "ERROR", "NumCredoc": "CD2"},
    ])
    numeric_result = FieldResult(
        df=pd.DataFrame(), classification_df=None, outliers_df=numeric_df,
        exclude_from_export=[], stats={}, sheet_names={},
    )
    return [(devise, devise_result), (_DummyEcheancesProcessor(), numeric_result)]


def test_quality_report_markdown_uses_generic_shared_template():
    md = build_quality_report_markdown(_quality_report())

    assert "Rapport de qualité des traitements — E09_PE" in md
    assert "Nombre total de lignes traitées : 50" in md
    assert "Taux de données conformes : 60,0 %" in md


def test_outliers_report_has_three_rows_no_numcredoc():
    md = build_outliers_report_markdown(_quality_report(), _results())

    for label in ("devise", "montantEcheance", "dateEcheance"):
        assert label in md
    assert "numCredoc" not in md

    assert "| devise | 4 |" in md
    assert "| montantEcheance | 3 |" in md
    assert "| dateEcheance | 2 |" in md
    assert "**Total** | **9**" in md


def test_outliers_report_refbanque_detail_uses_numcredoc_as_source_value():
    md = build_outliers_report_markdown(_quality_report(), _results())

    assert "CD1" in md
    assert "CD2" in md
