"""
Rapport PDF E07 (Markdown avant conversion) : même gabarit que les autres APIs,
avec les libellés du ticket E07 et le rattachement de chaque règle de validation
à son champ. Produit (hors périmètre) et SourceDevise (non normalisée)
n'apparaissent pas.
"""
from datetime import datetime

import pandas as pd

from e07_fs.reports import build_outliers_report_markdown, build_quality_report_markdown
from shared.field_processor import CategoricalFieldProcessor, FieldResult
from shared.quality_report import QualityReport


def _fake_treating_fn(df: pd.DataFrame, field_col: str, api_id=None) -> pd.DataFrame:
    df = df.copy()
    df["TypeSwift_Normalisé"] = df[field_col].map({"MT103": "MT 103"}).fillna("OUTLIER")
    df[f"{field_col}_method"] = df["TypeSwift_Normalisé"].map(lambda v: "MAP" if v != "OUTLIER" else "OUTLIER")
    return df


class _DummyTransactionsProcessor:
    field_name = "Transactions"


def _quality_report() -> QualityReport:
    return QualityReport(
        api_id="E07_FS", mode="incremental",
        started_at=datetime(2026, 9, 11, 10, 0), finished_at=datetime(2026, 9, 11, 10, 0, 20),
        n_rows=30, taux_conformite_pct=70.0,
        per_field_stats={"TypeSwift": {"n_distinct_total": 4, "n_distinct_normalized": 2}},
        outliers_by_champ={"TypeSwift": 2, "AMOUNT_NON_NEGATIVE": 1, "RATE_NON_NEGATIVE": 1,
                           "DATE_VALIDITY": 3, "NO_ACTIVITY_CONFORMITY": 1},
        cumulative_n_rows=30,
        cumulative_taux_conformite_pct=70.0,
        cumulative_n_distinct_total=4,
        cumulative_n_distinct_normalized=2,
        cumulative_taux_normalisation_pct=50.0,
        cumulative_n_already_clean=1,
        cumulative_taux_deja_propre_pct=25.0,
        cumulative_taux_nettoyage_pct=25.0,
        cumulative_taux_outliers_distinct_pct=50.0,
    )


def _results() -> list:
    swift = CategoricalFieldProcessor(
        field_name="TypeSwift", treating_fn=_fake_treating_fn,
        treating_kwargs={"field_col": "TypeSwfit", "api_id": None},
        col_in="TypeSwfit", col_out="TypeSwift_Normalisé", ref_banque_col="RefBanque",
        outlier_tag="OUTLIER", exclude_suffixes=(), clean_fn=lambda x: str(x).strip(),
    )
    swift_result = swift.process(
        pd.DataFrame({"RefBanque": ["B1", "B2"], "TypeSwfit": ["XYZ", "ABC"]}), api_id="E07_FS"
    )
    anomalies = pd.DataFrame([
        {"RefBanque": "B1", "Rule": "AMOUNT_NON_NEGATIVE", "Severity": "ERROR", "ReferenceTransaction": "TX1"},
        {"RefBanque": "B1", "Rule": "RATE_NON_NEGATIVE", "Severity": "ERROR", "ReferenceTransaction": "TX1"},
        {"RefBanque": "B2", "Rule": "DATE_VALIDITY", "Severity": "ERROR", "ReferenceTransaction": "TX2"},
        {"RefBanque": "B3", "Rule": "NO_ACTIVITY_CONFORMITY", "Severity": "ERROR", "ReferenceTransaction": "NA"},
    ])
    numeric_result = FieldResult(
        df=pd.DataFrame(), classification_df=None, outliers_df=anomalies,
        exclude_from_export=[], stats={}, sheet_names={},
    )
    return [(swift, swift_result), (_DummyTransactionsProcessor(), numeric_result)]


def test_quality_report_generique():
    md = build_quality_report_markdown(_quality_report())
    assert "Rapport de qualité des traitements" in md and "E07_FS" in md
    assert '<td>Lignes traitées</td><td class="num">30</td>' in md


def test_regles_rattachees_a_leur_champ():
    md = build_outliers_report_markdown(_quality_report(), _results())

    assert '<td>typeSwfit</td><td class="num">2</td>' in md
    assert '<td>montantTransaction</td><td class="num">1</td>' in md
    assert '<td>tauxDeChange</td><td class="num">1</td>' in md
    assert '<td>dateTransaction</td><td class="num">3</td>' in md
    assert '<td>referenceTransaction</td><td class="num">1</td>' in md
    for champ in ("modeReglement", "devise", "nomDonneurOrdre", "beneficiaire", "natureEconomique", "pays"):
        assert f"<td>{champ}</td>" in md


def test_produit_et_source_devise_absents():
    md = build_outliers_report_markdown(_quality_report(), _results())
    assert "<td>produit</td>" not in md
    assert "<td>sourceDevise</td>" not in md
