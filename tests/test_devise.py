"""
Vérifie la cascade Devise, en particulier la distinction entre bruit CONNU du
référentiel (method="NOISE", pas un problème) et valeur VRAIMENT non reconnue
(method="OUTLIER" générique, candidate à examiner) — voir e11_rdcc/fields/devise.py.
"""
import pandas as pd

from e11_rdcc.fields.devise import DeviseReferentiel, _resolve_devise, clean_devise, treating_devise


def _ref() -> DeviseReferentiel:
    return DeviseReferentiel(
        version="test",
        valid={"USD", "EUR", "MRU"},
        num_map={"840": "USD"},
        aliases={"DOLLARS": "USD"},
        noise={"XXX", "STRING", "0"},
    )


def test_clean_devise_strips_float_suffix_and_spaces():
    assert clean_devise("usd ") == "USD"
    assert clean_devise("978.0") == "978"


def test_resolve_devise_valid_code():
    assert _resolve_devise("USD", _ref()) == ("USD", "MAP")


def test_resolve_devise_numeric_code():
    assert _resolve_devise("840", _ref()) == ("USD", "NUM")


def test_resolve_devise_alias():
    assert _resolve_devise("DOLLARS", _ref()) == ("USD", "ALIAS")


def test_resolve_devise_known_noise_is_tagged_noise_not_outlier():
    """Le point clé : le bruit référencé (known_noise) doit être distingué d'un
    vrai inconnu — même valeur normalisée (OUTLIER) mais méthode différente."""
    assert _resolve_devise("XXX", _ref()) == ("OUTLIER", "NOISE")
    assert _resolve_devise("STRING", _ref()) == ("OUTLIER", "NOISE")


def test_resolve_devise_genuinely_unknown_is_plain_outlier():
    assert _resolve_devise("ZZZZZ123", _ref()) == ("OUTLIER", "OUTLIER")


def test_treating_devise_end_to_end_distinguishes_noise_from_unknown():
    df = pd.DataFrame({
        "Devise": ["USD", "XXX", "ZZZZZ123"],
        "RefBanque": ["B1", "B1", "B1"],
        "NomCorrespondant": ["BANK A", "BANK A", "BANK A"],
    })
    out = treating_devise(df, ref=_ref(), api_id="TEST_API", warm_start=False)

    methods = dict(zip(out["Devise"], out["Devise_method"]))
    assert methods["USD"] == "MAP"
    assert methods["XXX"] == "NOISE"
    assert methods["ZZZZZ123"] == "OUTLIER"

    # Les deux se résolvent bien à OUTLIER (même valeur normalisée) malgré la méthode différente.
    normalized = dict(zip(out["Devise"], out["Devise_Normalisée"]))
    assert normalized["XXX"] == "OUTLIER"
    assert normalized["ZZZZZ123"] == "OUTLIER"
