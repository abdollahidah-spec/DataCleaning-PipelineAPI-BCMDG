"""
Étape MAP_CIBLE (index_valeurs_cibles, portée depuis l'ancien repo) sur les champs
entités d'E07 : un libellé déjà égal à un nom légal du référentiel est résolu
directement, sans DGI ni Claude. Vérifie aussi que l'assemblage par jointure du
NomDonneurOrdre conserve l'ordre des lignes.
"""
import pandas as pd
import pytest

import e07_fs.fields.beneficiaire as benef_mod
import e07_fs.fields.nomdonneurordre as ndo_mod
from e07_fs.fields._entity_matching import index_valeurs_cibles, prepare_dgi_index, prepare_public_ent_index


@pytest.fixture(autouse=True)
def caches(tmp_path, monkeypatch):
    monkeypatch.setattr(ndo_mod, "_REFERENTIEL_DIR", tmp_path)
    monkeypatch.setattr(benef_mod, "_REFERENTIEL_DIR", tmp_path)


def _public_index() -> dict:
    return prepare_public_ent_index(pd.DataFrame({"Short Name": ["SNIM"],
                                                  "Raison social - Public Ent": ["Société Nationale Industrielle et Minière"]}))


def _dgi_index() -> dict:
    return prepare_dgi_index(pd.DataFrame({"RAISON_SOCIALE": ["SOCIETE EXEMPLE DE TEST SARL"],
                                           "NIF": ["01371954"], "FORME_JURIDIQUE": ["SARL"]}))


def test_index_ignore_outlier_et_valeurs_vides():
    index = index_valeurs_cibles({"A": "ACME TRADING LTD", "B": "OUTLIER", "C": "", "D": "acme trading ltd"})
    assert index == {"ACME TRADING LTD": "ACME TRADING LTD"}


def test_nomdonneurordre_map_cible_et_ordre_des_lignes():
    df = pd.DataFrame({
        "NomDonneurOrdre": ["acme trading ltd", "SOC EXEMPLE", "Mohamed Ould Ahmed", "acme trading ltd"],
        "NifNni": ["", "01371954", "", ""],
        "ReferenceTransaction": ["TX1", "TX2", "TX3", "TX4"], "RefBanque": ["B1"] * 4,
    })
    out = ndo_mod.treating_nomdonneurordre(df, ref={"ACME": "ACME TRADING LTD"}, dgi_index=_dgi_index(),
                                           public_index=_public_index(), api_id="E07_FS", warm_start=False)
    assert list(out["NomDonneurOrdre_method"]) == ["MAP_CIBLE", "NIF_EXACT", "PARTICULIER", "MAP_CIBLE"]
    assert out["NomDonneurOrdre_Normalisé"].iloc[1] == "SOCIETE EXEMPLE DE TEST SARL"


def test_beneficiaire_map_cible_sans_claude(monkeypatch):
    monkeypatch.setattr(benef_mod, "call_claude_beneficiaire_web_batch",
                        lambda *a, **k: pytest.fail("appel Claude inattendu"))
    df = pd.DataFrame({"Beneficiaire": ["Glencore International AG"], "ReferenceTransaction": ["TX1"],
                       "RefBanque": ["B1"]})
    out = benef_mod.treating_beneficiaire(df, ref={"GLENCORE": "GLENCORE INTERNATIONAL AG"},
                                          public_index=_public_index(), api_id="E07_FS", warm_start=False)
    assert out["Beneficiaire_method"].iloc[0] == "MAP_CIBLE"
    assert out["Beneficiaire_Normalisé"].iloc[0] == "GLENCORE INTERNATIONAL AG"
