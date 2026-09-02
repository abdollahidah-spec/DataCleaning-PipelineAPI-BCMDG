import pandas as pd

from shared.field_processor import (
    CategoricalFieldProcessor,
    cumulative_already_clean_stats,
    cumulative_classification_stats,
)


def _fake_treating_fn(df: pd.DataFrame, field_col: str, api_id: str = None) -> pd.DataFrame:
    """Simule un treating_fn de champ catégoriel : mappe A->X, B->Y, tout le reste OUTLIER."""
    mapping = {"A": "X", "B": "Y"}
    df = df.copy()
    df[f"{field_col}_Normalisé"] = df[field_col].map(mapping).fillna("OUTLIER")
    df[f"{field_col}_method"] = df[f"{field_col}_Normalisé"].map(lambda v: "MAP" if v != "OUTLIER" else "OUTLIER")
    df[f"{field_col}_clean"] = df[field_col]
    df[f"{field_col}_check"] = df[f"{field_col}_Normalisé"] == "OUTLIER"
    return df


def _build_processor() -> CategoricalFieldProcessor:
    return CategoricalFieldProcessor(
        field_name="TestField",
        treating_fn=_fake_treating_fn,
        treating_kwargs={"field_col": "TestField", "api_id": None},
        col_in="TestField",
        col_out="TestField_Normalisé",
        ref_banque_col="RefBanque",
        outlier_tag="OUTLIER",
        exclude_suffixes=("_clean", "_method", "_check"),
        clean_fn=lambda x: str(x).strip(),
    )


def test_classification_df_includes_outliers():
    df = pd.DataFrame({
        "RefBanque": ["B1", "B1", "B2"],
        "TestField": ["A", "B", "Z"],
    })
    processor = _build_processor()
    result = processor.process(df, api_id="TEST_API")

    classification = result.classification_df
    assert set(classification["TestField"]) == {"A", "B", "Z"}
    assert classification.loc[classification["TestField"] == "Z", "TestField_Normalisé"].iloc[0] == "OUTLIER"
    assert classification.loc[classification["TestField"] == "A", "TestField_Normalisé"].iloc[0] == "X"


def test_outliers_df_stats_by_refbanque():
    df = pd.DataFrame({
        "RefBanque": ["B1", "B1", "B2"],
        "TestField": ["A", "Z", "Z"],
    })
    processor = _build_processor()
    result = processor.process(df, api_id="TEST_API")

    outliers = result.outliers_df
    assert "Nombre_OUTLIERS" in outliers.columns
    assert outliers["Nombre_OUTLIERS"].sum() == 2


def test_exclude_from_export_lists_intermediate_columns():
    processor = _build_processor()
    assert set(processor.process(pd.DataFrame({"RefBanque": ["B1"], "TestField": ["A"]}), "TEST_API")
               .exclude_from_export) == {"TestField_clean", "TestField_method", "TestField_check", "_ws_hit"}


def _fake_treating_fn_with_noise(df: pd.DataFrame, field_col: str, api_id: str = None) -> pd.DataFrame:
    """Comme _fake_treating_fn, mais distingue le bruit connu (NOISE) du vrai
    inconnu (OUTLIER) — imite la distinction ajoutée dans e11_rdcc/fields/devise.py."""
    mapping = {"A": "X"}
    noise = {"N1", "N2"}
    df = df.copy()

    def _method(v, normalized):
        if normalized != "OUTLIER":
            return "MAP"
        return "NOISE" if v in noise else "OUTLIER"

    df[f"{field_col}_Normalisé"] = df[field_col].map(mapping).fillna("OUTLIER")
    df[f"{field_col}_method"] = [
        _method(v, n) for v, n in zip(df[field_col], df[f"{field_col}_Normalisé"])
    ]
    df[f"{field_col}_clean"] = df[field_col]
    df[f"{field_col}_check"] = df[f"{field_col}_Normalisé"] == "OUTLIER"
    return df


def test_categorical_stats_breaks_down_outliers_by_method():
    df = pd.DataFrame({
        "RefBanque": ["B1"] * 5,
        "TestField": ["A", "N1", "N1", "N2", "UNKNOWN1"],
    })
    processor = CategoricalFieldProcessor(
        field_name="TestField", treating_fn=_fake_treating_fn_with_noise,
        treating_kwargs={"field_col": "TestField", "api_id": None},
        col_in="TestField", col_out="TestField_Normalisé", ref_banque_col="RefBanque",
        outlier_tag="OUTLIER", exclude_suffixes=("_clean", "_method", "_check"),
        clean_fn=lambda x: str(x).strip(),
    )
    result = processor.process(df, api_id="TEST_API")

    stats = result.stats
    assert stats["outliers_by_method"] == {"NOISE": 3, "OUTLIER": 1}          # lignes (N1 x2, N2 x1, UNKNOWN1 x1)
    assert stats["distinct_outliers_by_method"] == {"NOISE": 2, "OUTLIER": 1}  # valeurs distinctes (N1, N2 / UNKNOWN1)


def test_cumulative_classification_stats_reads_classification_table_not_run_rows():
    """Régression : les stats "globales" (rapport PDF) doivent venir de la table
    de classification CUMULATIVE (référentiel + cache, voir classification_fn),
    PAS des lignes du run en cours — sinon un run au delta minuscule afficherait
    un historique global minuscule lui aussi (retour BA : indicateurs du PDF
    calculés sur l'ensemble de l'historique, pas seulement le delta)."""
    processor = _build_processor()
    # Le run en cours ne voit que 2 lignes (1 valeur distincte), mais la table de
    # classification (simulée via classification_fn) représente TOUT l'historique
    # déjà connu : 4 valeurs distinctes dont 3 normalisées.
    processor.classification_fn = lambda api_id: pd.DataFrame({
        "TestField": ["A", "B", "C", "Z"],
        "TestField_Normalisé": ["X", "Y", "W", "OUTLIER"],
    })
    result = processor.process(pd.DataFrame({"RefBanque": ["B1"], "TestField": ["A"]}), api_id="TEST_API")

    total, normalized = cumulative_classification_stats([(processor, result)])
    assert total == 4
    assert normalized == 3


def test_cumulative_classification_stats_ignores_non_categorical_and_empty():
    class _DummyNumeric:
        field_name = "Numeric"

    from shared.field_processor import FieldResult

    numeric_result = FieldResult(
        df=pd.DataFrame(), classification_df=None, outliers_df=pd.DataFrame(),
        exclude_from_export=[], stats={}, sheet_names={},
    )
    total, normalized = cumulative_classification_stats([(_DummyNumeric(), numeric_result)])
    assert (total, normalized) == (0, 0)


def test_cumulative_already_clean_stats_distinguishes_exact_match_from_any_treatment():
    """Retour métier explicite : une valeur brute IDENTIQUE à sa valeur normalisée
    (comparaison littérale) = déjà propre, 0 traitement. Toute différence — même
    un simple espace en trop retiré — ou un OUTLIER non résolu = un traitement a
    eu lieu."""
    processor = _build_processor()
    processor.classification_fn = lambda api_id: pd.DataFrame({
        "TestField": ["X", "  A", "B", "Z"],
        "TestField_Normalisé": ["X", "A", "Y", "OUTLIER"],
    })
    result = processor.process(pd.DataFrame({"RefBanque": ["B1"], "TestField": ["A"]}), api_id="TEST_API")

    total, already_clean = cumulative_already_clean_stats([(processor, result)])
    assert total == 4
    assert already_clean == 1  # seule "X" -> "X" est une correspondance littérale exacte


def test_cumulative_already_clean_stats_outlier_never_counts_as_already_clean():
    processor = _build_processor()
    processor.classification_fn = lambda api_id: pd.DataFrame({
        "TestField": ["OUTLIER"], "TestField_Normalisé": ["OUTLIER"],
    })
    result = processor.process(pd.DataFrame({"RefBanque": ["B1"], "TestField": ["A"]}), api_id="TEST_API")

    total, already_clean = cumulative_already_clean_stats([(processor, result)])
    assert (total, already_clean) == (1, 0)
