"""
Étape MAP_CIBLE sur E08 et E11 : une valeur brute déjà égale à une valeur normalisée
du référentiel est résolue par elle-même, sans appel Claude ni rapprochement DGI.

Ces deux APIs ne l'avaient pas (E07/E10 seulement) : la valeur partait en résolution
payante et pouvait revenir OUTLIER, puis être mise en cache définitivement — cas réel
de l'ancien repo : « BRED BANQUE POPULAIRE » sur E11_RDCC, 4 363 lignes.
"""
import pandas as pd
import pytest

from shared.referentiel_cibles import index_valeurs_cibles


def _refus_claude(monkeypatch, module, nom):
    monkeypatch.setattr(module, nom, lambda *a, **k: pytest.fail("appel Claude inattendu"))


def test_index_ignore_outlier_et_vides():
    index = index_valeurs_cibles({"a": "ACME SA", "b": "OUTLIER", "c": ""}, lambda v: v.upper())
    assert index == {"ACME SA": "ACME SA"}


@pytest.mark.parametrize("paquet,api", [("e08_ocd", "E08_OCD"), ("e11_rdcc", "E11_RDCC")])
def test_nomcorrespondant_valeur_deja_conforme(paquet, api, tmp_path, monkeypatch):
    import importlib

    mod = importlib.import_module(f"{paquet}.fields.nomcorrespondant")
    monkeypatch.setattr(mod, "_REFERENTIEL_DIR", tmp_path)
    _refus_claude(monkeypatch, mod, "call_claude_nomcorrespondant_batch")

    ref = {"BRED": "BRED BANQUE POPULAIRE"}
    temoin = "NumCredoc" if paquet == "e08_ocd" else "NumCompte"
    df = pd.DataFrame({"NomCorrespondant": ["Bred Banque Populaire"], temoin: ["X1"],
                       "RefBanque": ["B1"]})
    out = mod.treating_nomcorrespondant(df, ref=ref, api_id=api, warm_start=False)

    assert out["NomCorrespondant_Normalisé"].iloc[0] == "BRED BANQUE POPULAIRE"
    assert out["NomCorrespondant_method"].iloc[0] == "MAP_CIBLE"


def test_e08_nomdonneurordre_valeur_deja_conforme(tmp_path, monkeypatch):
    import e08_ocd.fields.nomdonneurordre as ndo
    from e08_ocd.fields._entity_matching import prepare_dgi_index, prepare_public_ent_index

    monkeypatch.setattr(ndo, "_REFERENTIEL_DIR", tmp_path)
    _refus_claude(monkeypatch, ndo, "call_claude_dgi_arbitrage_batch")
    dgi = prepare_dgi_index(pd.DataFrame({"RAISON_SOCIALE": ["AUTRE SOCIETE SARL"],
                                          "NIF": ["01371954"], "FORME_JURIDIQUE": ["SARL"]}))
    public = prepare_public_ent_index(pd.DataFrame({"Short Name": ["SNIM"],
                                                    "Raison social - Public Ent": ["Societe Nationale"]}))

    df = pd.DataFrame({"NomDonneurOrdre": ["acme trading ltd"], "NifNni": [""],
                       "NumCredoc": ["CD1"], "RefBanque": ["B1"]})
    out = ndo.treating_nomdonneurordre(df, ref={"ACME": "ACME TRADING LTD"}, dgi_index=dgi,
                                       public_index=public, api_id="E08_OCD", warm_start=False)

    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "ACME TRADING LTD"
    assert out["NomDonneurOrdre_method"].iloc[0] == "MAP_CIBLE"


def test_e08_beneficiaire_valeur_deja_conforme(tmp_path, monkeypatch):
    import e08_ocd.fields.beneficiaire as benef
    from e08_ocd.fields._entity_matching import prepare_public_ent_index

    monkeypatch.setattr(benef, "_REFERENTIEL_DIR", tmp_path)
    _refus_claude(monkeypatch, benef, "call_claude_beneficiaire_web_batch")
    public = prepare_public_ent_index(pd.DataFrame({"Short Name": ["SNIM"],
                                                    "Raison social - Public Ent": ["Societe Nationale"]}))

    df = pd.DataFrame({"Beneficiaire": ["Glencore International AG"], "NumCredoc": ["CD1"],
                       "RefBanque": ["B1"]})
    out = benef.treating_beneficiaire(df, ref={"GLENCORE": "GLENCORE INTERNATIONAL AG"},
                                      public_index=public, api_id="E08_OCD", warm_start=False)

    assert out["Beneficiaire_method"].iloc[0] == "MAP_CIBLE"


def test_e08_produits_libelle_deja_conforme(tmp_path, monkeypatch):
    """Un libellé brut égal à un libellé du référentiel ne doit pas partir chez Claude."""
    import e08_ocd.fields.produits as produits

    monkeypatch.setattr(produits, "_REFERENTIEL_DIR", tmp_path)
    _refus_claude(monkeypatch, produits, "call_claude_match_batch")
    categories = {"Produits alimentaires": ["riz", "sucres"]}
    ref = produits.ProduitsReferentiel(
        version="test", categories=categories, aliases={"RIZ BASMATI": "riz"}, noise=set(),
        libelle_vers_categorie={lib: cat for cat, libs in categories.items() for lib in libs},
        all_libelles=["riz", "sucres"],
    )

    df = pd.DataFrame({"Produits": ["Sucres"], "NumCredoc": ["CD1"], "RefBanque": ["B1"]})
    out = produits.treating_produits(df, ref=ref, api_id="E08_OCD", warm_start=False, cfg={})

    assert out["Produit_Normalisé"].iloc[0] == "sucres"
    assert out["Produit_method"].iloc[0] == "MAP_CIBLE"
    assert out["Produit_Categorie"].iloc[0] == "Produits alimentaires"
