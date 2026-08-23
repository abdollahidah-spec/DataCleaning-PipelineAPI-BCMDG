import pandas as pd

from shared.field_processor import CategoricalFieldProcessor


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


def test_instructions_rows_prefilled_from_outliers():
    df = pd.DataFrame({"RefBanque": ["B1", "B1"], "TestField": ["Z", "Z2"]})
    processor = _build_processor()
    result = processor.process(df, api_id="TEST_API")

    instructions = processor.instructions_rows(result.outliers_df)
    assert set(instructions["Champ"]) == {"TestField"}
    assert set(instructions["Input"]) == {"Z", "Z2"}
    assert (instructions["Label_Attendu"] == "").all()


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
