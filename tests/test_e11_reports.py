"""
Vérifie le contenu des deux rapports PDF (Markdown avant conversion) — gabarit
EXACT validé par le Business Analyst : Rapport_Qualite (stats + définitions
verbatim) et Rapport_Outliers (répartition par champ traité, 7 lignes fixes +
Total, et par RefBanque).
"""
from datetime import datetime

import pandas as pd

from e11_rdcc.reports import build_outliers_report_markdown, build_quality_report_markdown
from shared.field_processor import CategoricalFieldProcessor, FieldResult
from shared.quality_report import QualityReport


def _fake_treating_fn(df: pd.DataFrame, field_col: str, api_id=None) -> pd.DataFrame:
    mapping = {"A": "X", "B": "Y"}
    df = df.copy()
    df[f"{field_col}_Normalisé"] = df[field_col].map(mapping).fillna("OUTLIER")
    df[f"{field_col}_method"] = df[f"{field_col}_Normalisé"].map(lambda v: "MAP" if v != "OUTLIER" else "OUTLIER")
    return df


def _categorical_processor(field_name: str, col_in: str, col_out: str) -> CategoricalFieldProcessor:
    return CategoricalFieldProcessor(
        field_name=field_name, treating_fn=_fake_treating_fn,
        treating_kwargs={"field_col": col_in, "api_id": None},
        col_in=col_in, col_out=col_out, ref_banque_col="RefBanque",
        outlier_tag="OUTLIER", exclude_suffixes=(), clean_fn=lambda x: str(x).strip(),
    )


class _DummyNumericProcessor:
    field_name = "SoldesRDCC"


def _quality_report() -> QualityReport:
    return QualityReport(
        api_id="E11_RDCC", mode="incremental",
        started_at=datetime(2026, 8, 13, 10, 0), finished_at=datetime(2026, 8, 13, 10, 0, 42),
        n_rows=100, taux_conformite_pct=75.0,
        per_field_stats={
            "NomCorrespondant": {"n_distinct_total": 10, "n_distinct_normalized": 7},
            "Devise": {"n_distinct_total": 4, "n_distinct_normalized": 4},
        },
        outliers_by_champ={
            "NomCorrespondant": 3, "Devise": 0, "ARITHMETIC": 2,
            "TEMPORAL_CONTINUITY": 1, "DATE_VALIDITY": 1, "NO_ACTIVITY_CONFORMITY": 0,
        },
        # Le rapport PDF (build_quality_report_markdown) lit désormais les compteurs
        # "ensemble de l'historique" — voir shared/base_api_pipeline.py::
        # _attach_cumulative_stats. Ici, mêmes valeurs que le run pour garder ce test
        # focalisé sur le gabarit Markdown lui-même (la sémantique cumulative est
        # testée séparément, voir tests/test_base_api_pipeline_cumulative.py).
        cumulative_n_rows=100,
        cumulative_taux_conformite_pct=75.0,
        cumulative_n_distinct_total=14,
        cumulative_n_distinct_normalized=11,
        cumulative_taux_normalisation_pct=round(100 * 11 / 14, 2),
        cumulative_n_already_clean=8,
        cumulative_taux_deja_propre_pct=round(100 * 8 / 14, 2),
        cumulative_taux_nettoyage_pct=round(100 * 3 / 14, 2),          # 11 normalisées - 8 déjà propres
        cumulative_taux_outliers_distinct_pct=round(100 * 3 / 14, 2),  # 14 total - 11 normalisées
    )


def _results() -> list:
    nomcorr = _categorical_processor("NomCorrespondant", "NomCorrespondant", "NomCorrespondant_Normalisé")
    nomcorr_result = nomcorr.process(
        pd.DataFrame({"RefBanque": ["B1", "B1", "B2"], "NomCorrespondant": ["Z1", "Z1", "Z2"]}),
        api_id="E11_RDCC",
    )
    devise = _categorical_processor("Devise", "Devise", "Devise_Normalisé")
    devise_result = devise.process(pd.DataFrame({"RefBanque": ["B1"], "Devise": ["A"]}), api_id="E11_RDCC")

    numeric_df = pd.DataFrame([
        {"RefBanque": "B1", "Rule": "ARITHMETIC", "Severity": "ERROR", "NumCompte": "C1"},
        {"RefBanque": "B1", "Rule": "ARITHMETIC", "Severity": "ERROR", "NumCompte": "C2"},
        {"RefBanque": "B2", "Rule": "TEMPORAL_CONTINUITY", "Severity": "ERROR", "NumCompte": "C3"},
        {"RefBanque": "B2", "Rule": "DATE_VALIDITY", "Severity": "ERROR", "NumCompte": "C4"},
    ])
    numeric_result = FieldResult(
        df=pd.DataFrame(), classification_df=None, outliers_df=numeric_df,
        exclude_from_export=[], stats={}, sheet_names={},
    )
    return [(nomcorr, nomcorr_result), (devise, devise_result), (_DummyNumericProcessor(), numeric_result)]


def test_quality_report_markdown_has_verbatim_definitions_and_correct_numbers():
    md = build_quality_report_markdown(_quality_report())

    assert "Nombre total de lignes traitées : 100" in md
    assert "nombre total d'enregistrements (lignes) pris en compte par la pipeline sur l'ensemble de l'historique" in md
    assert "Nombre total de valeurs distinctes traitées : 14" in md
    assert "Nombre de valeurs distinctes normalisées : 11" in md
    assert "Nombre de valeurs non classifiées (outliers) : 3" in md
    assert "Taux de données conformes : 75,0 %" in md
    assert "Taux de valeurs normalisées : 78,6 %" in md
    assert "Nombre de valeurs déjà propres à la source : 8" in md
    assert "Nombre de valeurs nettoyées par la pipeline (traitement réussi) : 3" in md
    assert "Taux de valeurs déjà propres à la source : 57,1 %" in md
    assert "Taux de valeurs nettoyées par la pipeline : 21,4 %" in md
    assert "Taux de valeurs non classifiées (outliers) : 21,4 %" in md
    assert "Temps total d'exécution : 00 min 42 s" in md
    assert "rattacher une valeur normalisée à N valeurs sources" in md


def test_outliers_report_field_breakdown_matches_ba_template_rows():
    md = build_outliers_report_markdown(_quality_report(), _results())

    for label in (
        "nomCorrespondant", "devise", "soldeDebutJournee", "totalMvtsDebiteursJournee",
        "totalMvtsCrediteurs", "soldeFinJournee", "dateFinJournee",
    ):
        assert label in md

    assert "| nomCorrespondant | 3 |" in md
    assert "| soldeDebutJournee | 1 |" in md      # TEMPORAL_CONTINUITY
    assert "| soldeFinJournee | 2 |" in md         # ARITHMETIC + NO_ACTIVITY_CONFORMITY
    assert "| dateFinJournee | 1 |" in md          # DATE_VALIDITY
    assert "| totalMvtsDebiteursJournee | 0 |" in md
    assert "**Total** | **7**" in md               # 3+0+1+0+0+2+1


def test_outliers_report_refbanque_detail_includes_categorical_and_numeric():
    md = build_outliers_report_markdown(_quality_report(), _results())

    assert "Z1" in md and "B1" in md    # outlier catégoriel NomCorrespondant
    assert "C3" in md and "B2" in md    # anomalie numérique (NumCompte comme "valeur source")


def test_outliers_report_no_truncation_note_under_top_n():
    md = build_outliers_report_markdown(_quality_report(), _results())
    assert "occurrences les plus fréquentes" not in md
