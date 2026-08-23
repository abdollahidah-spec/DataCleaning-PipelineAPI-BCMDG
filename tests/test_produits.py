"""
Vérifie la cascade Produits (e08_ocd/fields/produits.py) : alias connus (MAP),
bruit connu (NOISE, distinct d'un inconnu générique), règle NA (témoin NumCredoc),
et le fallback Claude sur la liste fermée de labels (mocké — jamais de vrai appel
réseau dans les tests).
"""
import pandas as pd
import pytest

from e08_ocd.fields.produits import (
    ProduitsReferentiel,
    clean_produits,
    treating_produits,
)


def _ref() -> ProduitsReferentiel:
    categories = {
        "Produits alimentaires": ["riz", "sucres"],
        "Produit pétrolier": ["gasoil"],
        "Autre": ["Autre"],
    }
    return ProduitsReferentiel(
        version="test",
        categories=categories,
        aliases={"RIZ BASMATI": "riz", "SUCRES": "sucres", "GASOIL": "gasoil"},
        noise={"PROFORMA INVOICE", "CDT"},
        libelle_vers_categorie={lib: cat for cat, libs in categories.items() for lib in libs},
        all_libelles=["riz", "sucres", "gasoil", "Autre"],
    )


def test_clean_produits_strips_accents_and_uppercases():
    assert clean_produits("Riz basmati") == "RIZ BASMATI"
    assert clean_produits("  blé  ") == "BLE"


def test_treating_produits_resolves_alias_as_map():
    df = pd.DataFrame({"Produits": ["Sucres"], "RefBanque": ["B1"], "NumCredoc": ["CD1"]})
    out = treating_produits(df, ref=_ref(), api_id="TEST_API", warm_start=False, cfg={})
    assert out["Produits_Normalisé"].iloc[0] == "sucres"
    assert out["Produits_method"].iloc[0] == "MAP"
    assert out["Produits_Categorie"].iloc[0] == "Produits alimentaires"


def test_treating_produits_known_noise_is_tagged_noise_not_outlier():
    """Comme pour Devise : le bruit référencé (known_noise) doit être distingué
    d'un vrai inconnu — même valeur normalisée (OUTLIER) mais méthode différente."""
    df = pd.DataFrame({"Produits": ["PROFORMA INVOICE"], "RefBanque": ["B1"], "NumCredoc": ["CD1"]})
    out = treating_produits(df, ref=_ref(), api_id="TEST_API", warm_start=False, cfg={})
    assert out["Produits_Normalisé"].iloc[0] == "OUTLIER"
    assert out["Produits_method"].iloc[0] == "NOISE"


def test_treating_produits_na_rule_legit_when_ref_also_na():
    df = pd.DataFrame({"Produits": ["NA"], "RefBanque": ["B1"], "NumCredoc": ["NA"]})
    out = treating_produits(df, ref=_ref(), api_id="TEST_API", warm_start=False, cfg={})
    assert out["Produits_Normalisé"].iloc[0] == "NA"
    assert out["Produits_method"].iloc[0] == "NA"


def test_treating_produits_na_rule_outlier_when_ref_not_na():
    df = pd.DataFrame({"Produits": ["NA"], "RefBanque": ["B1"], "NumCredoc": ["CD1"]})
    out = treating_produits(df, ref=_ref(), api_id="TEST_API", warm_start=False, cfg={})
    assert out["Produits_Normalisé"].iloc[0] == "OUTLIER"


def test_treating_produits_unresolved_value_goes_through_claude_fallback(monkeypatch, tmp_path):
    """Une valeur ni alias ni bruit connu doit être envoyée à Claude — mocké ici,
    jamais de vrai appel réseau — et le résultat doit obligatoirement appartenir
    à la liste fermée des labels valides."""
    import e08_ocd.fields.produits as produits_mod

    monkeypatch.setattr(produits_mod, "_REFERENTIEL_DIR", tmp_path)  # jamais écrire dans le vrai référentiel

    def fake_call_claude_match_batch(batch, valid_labels, system_prompt, cfg):
        assert set(batch) == {"NOUVEAU PRODUIT INCONNU"}
        assert valid_labels == ["riz", "sucres", "gasoil", "Autre"]
        return ["riz"]

    monkeypatch.setattr(produits_mod, "call_claude_match_batch", fake_call_claude_match_batch)

    df = pd.DataFrame({"Produits": ["Nouveau produit inconnu"], "RefBanque": ["B1"], "NumCredoc": ["CD1"]})
    out = treating_produits(df, ref=_ref(), api_id="TEST_API", warm_start=False, cfg={})
    assert out["Produits_Normalisé"].iloc[0] == "riz"
    assert out["Produits_method"].iloc[0] == "CLAUDE"


def test_treating_produits_claude_technical_failure_is_not_cached(monkeypatch, tmp_path):
    """Un échec technique Claude (None, pas une liste) ne doit jamais être mis en
    cache — sinon une modalité réelle serait blacklistée suite à un simple incident."""
    import e08_ocd.fields.produits as produits_mod

    monkeypatch.setattr(produits_mod, "_REFERENTIEL_DIR", tmp_path)
    monkeypatch.setattr(produits_mod, "call_claude_match_batch", lambda *a, **k: None)

    df = pd.DataFrame({"Produits": ["Truc jamais vu"], "RefBanque": ["B1"], "NumCredoc": ["CD1"]})
    out = treating_produits(df, ref=_ref(), api_id="TEST_API", warm_start=True, cfg={})
    assert out["Produits_Normalisé"].iloc[0] == "OUTLIER"
    assert not (tmp_path / "validated_classif_produits_test_api.json").exists()
