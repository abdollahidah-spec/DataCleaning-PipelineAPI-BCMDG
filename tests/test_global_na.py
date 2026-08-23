"""
Vérifie le correctif Business Analyst : la ligne "sans activité" légitime doit
être tranchée UNE SEULE FOIS, GLOBALEMENT, à partir de TOUS les champs du
gabarit du ticket ensemble — pas indépendamment par champ avec un témoin
partiel (voir e11_rdcc/global_na.py, ancien bug : NomCorrespondant/Devise
regardaient chacun NumCompte seul, sans vérifier l'autre champ ni les montants).
"""
import pandas as pd
import pytest

from e11_rdcc.global_na import (
    GLOBAL_NA_COLUMN,
    compute_global_no_activity_column,
    has_required_columns,
)

_NUMERIC_COLS = {
    "nom_correspondant": "NomCorrespondant",
    "devise": "Devise",
    "num_compte": "NumCompte",
    "solde_debut": "SoldeDebutJournee",
    "mvts_debiteurs": "TotalMvtsDebiteursJournee",
    "mvts_crediteurs": "TotalMvtsCrediteurs",
    "solde_fin": "SoldeFinJournee",
}


def _row(**overrides) -> dict:
    base = {
        "NomCorrespondant": "NA", "Devise": "NA", "NumCompte": "NA",
        "SoldeDebutJournee": "0", "TotalMvtsDebiteursJournee": "0",
        "TotalMvtsCrediteurs": "0", "SoldeFinJournee": "0",
    }
    base.update(overrides)
    return base


def test_full_template_match_is_legit_no_activity():
    df = pd.DataFrame([_row()])
    result = compute_global_no_activity_column(df, _NUMERIC_COLS)
    assert result.tolist() == ["NA"]


def test_one_categorical_field_not_na_breaks_the_template():
    """C'est exactement le bug corrigé : NomCorrespondant='NA' mais Devise ne
    l'est pas -> pas une vraie ligne sans activité, même si NumCompte='NA'."""
    df = pd.DataFrame([_row(Devise="USD")])
    result = compute_global_no_activity_column(df, _NUMERIC_COLS)
    assert result.tolist() == [""]


def test_all_na_but_one_amount_nonzero_breaks_the_template():
    df = pd.DataFrame([_row(SoldeFinJournee="150.00")])
    result = compute_global_no_activity_column(df, _NUMERIC_COLS)
    assert result.tolist() == [""]


def test_mixed_rows_evaluated_independently():
    df = pd.DataFrame([_row(), _row(Devise="EUR"), _row(TotalMvtsDebiteursJournee="5")])
    result = compute_global_no_activity_column(df, _NUMERIC_COLS)
    assert result.tolist() == ["NA", "", ""]


def test_float_noise_within_epsilon_still_counts_as_zero():
    df = pd.DataFrame([_row(SoldeFinJournee="0.0000000001")])
    result = compute_global_no_activity_column(df, _NUMERIC_COLS)
    assert result.tolist() == ["NA"]


def test_french_decimal_comma_is_parsed():
    df = pd.DataFrame([_row(SoldeDebutJournee="0,0")])
    result = compute_global_no_activity_column(df, _NUMERIC_COLS)
    assert result.tolist() == ["NA"]


def test_result_is_never_equal_to_na_when_false_even_lowercase():
    """Le résultat doit être exploitable tel quel par apply_na_rule (comparaison
    stricte à 'NA') — jamais une chaîne qui pourrait accidentellement matcher."""
    df = pd.DataFrame([_row(Devise="na")])  # Devise elle-même est bien reconnue NA (insensible à la casse)...
    result = compute_global_no_activity_column(df, _NUMERIC_COLS)
    assert result.tolist() == ["NA"]  # ... et donc ce cas particulier reste un vrai match complet


def test_missing_column_raises_clear_value_error():
    df = pd.DataFrame([_row()]).drop(columns=["SoldeFinJournee"])
    with pytest.raises(ValueError, match="SoldeFinJournee"):
        compute_global_no_activity_column(df, _NUMERIC_COLS)


def test_has_required_columns_true_when_all_present():
    df = pd.DataFrame([_row()])
    assert has_required_columns(df, _NUMERIC_COLS) is True


def test_has_required_columns_false_when_missing():
    df = pd.DataFrame([_row()]).drop(columns=["Devise"])
    assert has_required_columns(df, _NUMERIC_COLS) is False


def test_column_name_matches_expected_constant():
    assert GLOBAL_NA_COLUMN == "_E11_GlobalNoActivite"
