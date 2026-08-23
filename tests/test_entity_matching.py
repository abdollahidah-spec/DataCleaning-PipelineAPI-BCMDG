"""
Vérifie le socle de nettoyage/classification/matching partagé par NomDonneurOrdre
et Bénéficiaire (e08_ocd/fields/_entity_matching.py) — porté depuis l'ancien repo.
"""
import pandas as pd

from e08_ocd.fields._entity_matching import (
    classify_local,
    clean_label,
    est_outlier_evident,
    hyper_normaliser,
    nettoyer_nifnni,
    nif_valide,
    match_public_entity,
    prepare_dgi_index,
    prepare_public_ent_index,
)


# ---- clean_label --------------------------------------------------------------------

def test_clean_label_uppercases_strips_accents():
    assert clean_label("société générale") == "SOCIETE GENERALE"


def test_clean_label_expands_ould_mint_bint_abbreviations():
    assert clean_label("MED O/ AHMED") == "MED OULD AHMED"
    assert clean_label("FATIMA Mt/ HMD") == "FATIMA MINT HMD"
    assert clean_label("Bt/ SALEM") == "BINT SALEM"


def test_clean_label_joins_single_letter_acronyms():
    assert clean_label("H.M") == "HM"


def test_clean_label_empty_on_nan_like():
    assert clean_label(None) == ""
    assert clean_label("nan") == ""
    assert clean_label("") == ""


def test_hyper_normaliser_strips_everything_non_alnum():
    assert hyper_normaliser("Société-Générale S.A.") == "SOCIETEGENERALESA"


# ---- NIF ------------------------------------------------------------------------------

def test_nettoyer_nifnni_keeps_only_digits():
    assert nettoyer_nifnni("013-719 54") == "01371954"


def test_nif_valide_requires_8_digits_not_all_identical():
    assert nif_valide("01371954") is True
    assert nif_valide("11111111") is False   # tous identiques
    assert nif_valide("1234567") is False    # 7 chiffres


# ---- est_outlier_evident -------------------------------------------------------------

def test_est_outlier_evident_blacklist_and_digits():
    assert est_outlier_evident("STRING") is True
    assert est_outlier_evident("12345") is True
    assert est_outlier_evident("---") is True
    assert est_outlier_evident("") is True
    assert est_outlier_evident("SOCIETE GENERALE") is False


# ---- classify_local ---------------------------------------------------------------------

def test_classify_local_particulier():
    label, method = classify_local(clean_label("Mohamed Ould Ahmed"))
    assert method == "PARTICULIER"
    assert label == "MOHAMED OULD AHMED"


def test_classify_local_ets_personnel():
    label, method = classify_local(clean_label("ETS Mohamed Salem Commerce"))
    assert method == "ETS_PERSONNEL"


def test_classify_local_ets_outlier_when_no_clear_name_after_prefix():
    label, method = classify_local(clean_label("ETS COMMERCE GENERAL"))
    assert method == "ETS_OUTLIER"
    assert label == "OUTLIER"


def test_classify_local_family_firm_is_not_particulier():
    """'X ET FRERES'/'X ET FILS' est une raison sociale (nom commercial familial),
    pas la signature d'un particulier isolé."""
    label, method = classify_local(clean_label("Mohamed Ould Ahmed ET FRERES"))
    assert method is None


def test_classify_local_plain_company_name_returns_none():
    """Ni particulier ni ETS -> (None, None), à résoudre en cascade (référentiel/DGI/Claude)."""
    label, method = classify_local(clean_label("SOCIETE GENERALE DE BANQUE"))
    assert (label, method) == (None, None)


# ---- prepare_dgi_index / prepare_public_ent_index / match_public_entity ---------------

def _dgi_df() -> pd.DataFrame:
    return pd.DataFrame({
        "RAISON_SOCIALE": ["SOCIETE EXEMPLE SARL", "AUTRE ENTREPRISE SA"],
        "NIF": ["01371954", "00075812"],
        "FORME_JURIDIQUE": ["SARL", "SA"],
    })


def test_prepare_dgi_index_builds_nif_and_clean_lookups():
    idx = prepare_dgi_index(_dgi_df())
    assert idx["nif_index"]["01371954"] == "SOCIETE EXEMPLE SARL"
    assert "SOCIETE EXEMPLE SARL" in idx["clean_to_orig"].values()
    assert idx["clean_to_meta"]["SOCIETE EXEMPLE SARL"]["forme_juridique"] == "SARL"


def test_prepare_dgi_index_skips_missing_raison_sociale():
    df = pd.DataFrame({"RAISON_SOCIALE": [None, "VALID SARL"], "NIF": ["1", "01371954"], "FORME_JURIDIQUE": ["", "SARL"]})
    idx = prepare_dgi_index(df)
    assert len(idx["dgi_clean"]) == 1


def _public_ent_df() -> pd.DataFrame:
    return pd.DataFrame({
        "Short Name": ["SNIM", "SMH"],
        "Raison social - Public Ent": [
            "Société Nationale Industrielle et Minière",
            "Société Mauritanienne des Hydrocarbures",
        ],
    })


def test_match_public_entity_by_full_raison_sociale():
    idx = prepare_public_ent_index(_public_ent_df())
    assert match_public_entity(clean_label("Société Nationale Industrielle et Minière"), idx) == "SNIM"


def test_match_public_entity_by_short_name_token():
    idx = prepare_public_ent_index(_public_ent_df())
    assert match_public_entity(clean_label("SNIM"), idx) == "SNIM"


def test_match_public_entity_returns_none_when_no_match():
    idx = prepare_public_ent_index(_public_ent_df())
    assert match_public_entity(clean_label("ENTREPRISE SANS RAPPORT"), idx) is None


def test_match_public_entity_empty_index_returns_none():
    idx = prepare_public_ent_index(pd.DataFrame(columns=["Short Name", "Raison social - Public Ent"]))
    assert match_public_entity(clean_label("SNIM"), idx) is None
