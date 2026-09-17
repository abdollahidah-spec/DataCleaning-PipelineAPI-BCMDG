"""
Champs catégoriels propres à E10_FE (Flux Entrants) : TypeSwift (liste FE),
NatureEconomique (mapping FE), et l'inversion des champs entités par rapport à
E07 — NomDonneurOrdre étranger (Claude + recherche web), Beneficiaire local
(matching DGI). Aucun appel réseau : Claude est mocké, les caches warm-start sont
redirigés vers tmp_path, et la base DGI est un index synthétique.
"""
import json

import pandas as pd
import pytest

import e10_fe.fields.beneficiaire as benef_mod
import e10_fe.fields.mode_reglement as mode_mod
import e10_fe.fields.nature_economique as nat_mod
import e10_fe.fields.nomdonneurordre as ndo_mod
import e10_fe.fields.typeswift as swift_mod
from e10_fe.fields._entity_matching import prepare_dgi_index, prepare_public_ent_index

_REF = "e10_fe/referentiel/"


@pytest.fixture(autouse=True)
def caches(tmp_path, monkeypatch):
    for mod in (swift_mod, mode_mod, nat_mod, ndo_mod, benef_mod):
        monkeypatch.setattr(mod, "_REFERENTIEL_DIR", tmp_path)
    return tmp_path


def _df(col, valeurs, refs=None, **autres) -> pd.DataFrame:
    refs = refs or ["TX1"] * len(valeurs)
    return pd.DataFrame({col: valeurs, "ReferenceTransaction": refs, "RefBanque": ["B1"] * len(valeurs), **autres})


def _ecrire_cache(dossier, nom, classif) -> None:
    (dossier / nom).write_text(json.dumps({"version": "1.0.0", "classif": classif}), encoding="utf-8")


def _public_index() -> dict:
    return prepare_public_ent_index(pd.DataFrame({
        "Short Name": ["SNIM"],
        "Raison social - Public Ent": ["Société Nationale Industrielle et Minière"],
    }))


def _dgi_index() -> dict:
    return prepare_dgi_index(pd.DataFrame({
        "RAISON_SOCIALE": ["SOCIETE EXEMPLE DE TEST SARL", "SOCIETE EXEMPLE VOISINE SARL"],
        "NIF": ["01371954", "01371955"],
        "FORME_JURIDIQUE": ["SARL", "SARL"],
    }))


# ---- TypeSwift : liste des Flux Entrants ----------------------------------------------

def _swift_ref():
    return swift_mod.load_typeswift_referentiel(_REF + "typeswift_referentiel.json", flux="FE")


def test_typeswift_pacs004_valide_en_flux_entrant():
    out = swift_mod.treating_typeswift(_df("TypeSwfit", ["pacs.004", "MT103", "103"]),
                                       ref=_swift_ref(), warm_start=False)
    assert list(out["TypeSwift_Normalisé"]) == ["pacs.004", "MT 103", "MT 103"]


def test_typeswift_code_du_flux_sortant_est_outlier_en_fe():
    """MT 204 et pacs.010 ne sont valides que pour les Flux Sortants (valid_fs)."""
    out = swift_mod.treating_typeswift(_df("TypeSwfit", ["MT 204", "pacs.010"]), ref=_swift_ref(), warm_start=False)
    assert set(out["TypeSwift_Normalisé"]) == {"OUTLIER"}


def test_typeswift_liste_fe_par_defaut():
    assert "pacs.004" in swift_mod.load_typeswift_referentiel(_REF + "typeswift_referentiel.json").lookup.values()


def test_mode_reglement_cascade():
    ref = mode_mod.load_mode_reglement_referentiel(_REF + "mode_reglement_referentiel.json")
    out = mode_mod.treating_mode_reglement(_df("ModeReglement", ["TL", "TR", "SWIFT", None]), ref=ref, warm_start=False)
    assert list(out["ModeReglement_Normalisé"]) == ["TL", "TL", "OUTLIER", "OUTLIER"]


# ---- NatureEconomique : référentiel et mapping FE -------------------------------------

def _nat_ref():
    return nat_mod.load_nature_economique_referentiel(_REF + "nature_economique_referentiel_E10.json")


def _interdire_claude(monkeypatch):
    def _refus(*_a, **_k):
        raise AssertionError("Claude ne doit pas être sollicité pour cette valeur")
    monkeypatch.setattr(nat_mod, "call_claude_match_batch", _refus)


def test_nature_eco_mapping_direct_fe(monkeypatch):
    _interdire_claude(monkeypatch)
    ref = _nat_ref()
    out = nat_mod.treating_nature_economique(
        _df("NatureEconomique", ["Exportation bien", "Poissons", "COMMERCE"]), ref=ref, warm_start=False
    )
    assert list(out["NatureEconomique_Normalisé"]) == ["AUTRES BIENS", "PECHES PRODUITS MARITIMES", "AUTRES BIENS"]
    assert set(out["NatureEconomique_method"]) == {"MAP"}
    assert out["NatureEconomique_Categorie"].iloc[0] == ref.label_vers_categorie["AUTRES BIENS"]


def test_nature_eco_modalites_vagues_et_outliers_sans_claude(monkeypatch):
    _interdire_claude(monkeypatch)
    out = nat_mod.treating_nature_economique(
        _df("NatureEconomique", ["TRANSFERTS", "12345", "FRANCE", "Mohamed Ould Ahmed"]), ref=_nat_ref(), warm_start=False
    )
    assert set(out["NatureEconomique_Normalisé"]) == {"OUTLIER"}
    assert set(out["NatureEconomique_method"]) == {"NOISE"}


def test_nature_eco_ancienne_categorie_non_classe_lue_comme_outlier(caches, monkeypatch):
    _interdire_claude(monkeypatch)
    _ecrire_cache(caches, "validated_classif_nature_economique_e10_fe.json",
                  {"Affaires diverses": "NON CLASSE", "Assurance": "ASSURANCE ET REASSURANCE"})
    out = nat_mod.treating_nature_economique(_df("NatureEconomique", ["Affaires diverses", "assurance"]),
                                             ref=_nat_ref(), api_id="E10_FE")
    assert list(out["NatureEconomique_Normalisé"]) == ["OUTLIER", "ASSURANCE ET REASSURANCE"]
    assert set(out["NatureEconomique_method"]) == {"WARM"}


def test_nature_eco_prompt_flux_entrants(caches, monkeypatch):
    prompts = []

    def fake(batch, valid_labels, system_prompt, cfg):
        prompts.append(system_prompt)
        return ["TOURISME SEJOUR"] * len(batch)

    monkeypatch.setattr(nat_mod, "call_claude_match_batch", fake)
    out = nat_mod.treating_nature_economique(_df("NatureEconomique", ["Frais hotel mission"]),
                                             ref=_nat_ref(), api_id="E10_FE")
    assert out["NatureEconomique_method"].iloc[0] == "CLAUDE"
    assert "flux entrants" in prompts[0] and "VIREMENT PERMANENT" in prompts[0]


# ---- NomDonneurOrdre : émetteur étranger, Claude + recherche web ----------------------

def _run_ndo(valeurs, ref=None, refs=None, **kwargs):
    return ndo_mod.treating_nomdonneurordre(
        _df("NomDonneurOrdre", valeurs, refs=refs), ref=ref or {}, public_index=_public_index(),
        api_id="E10_FE", cfg={}, **kwargs,
    )


def test_ndo_valeur_deja_cible_du_referentiel(monkeypatch):
    monkeypatch.setattr(ndo_mod, "call_claude_beneficiaire_web_batch",
                        lambda *a, **k: pytest.fail("appel Claude inattendu"))
    out = _run_ndo(["glencore international ag"], ref={"GLENCORE INT": "GLENCORE INTERNATIONAL AG"},
                   warm_start=False)
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "GLENCORE INTERNATIONAL AG"
    assert out["NomDonneurOrdre_method"].iloc[0] == "MAP_CIBLE"


def test_ndo_referentiel_et_regle_na(monkeypatch):
    monkeypatch.setattr(ndo_mod, "call_claude_beneficiaire_web_batch",
                        lambda *a, **k: pytest.fail("appel Claude inattendu"))
    out = _run_ndo(["GLENCORE INT", "NA", "NA"], ref={"GLENCORE INT": "GLENCORE INTERNATIONAL AG"},
                   refs=["TX1", "NA", "TX2"], warm_start=False)
    assert list(out["NomDonneurOrdre_Normalisé"]) == ["GLENCORE INTERNATIONAL AG", "NA", "OUTLIER"]
    assert out["NomDonneurOrdre_method"].iloc[0] == "MAP"


def test_ndo_fallback_web_avec_prompt_e10_et_cache(caches, monkeypatch):
    appels = []

    def fake(batch, cfg, system_prompt=None, user_intro=None):
        appels.append((list(batch), system_prompt, user_intro))
        return ["NORDIC STEEL AB"] * len(batch)

    monkeypatch.setattr(ndo_mod, "call_claude_beneficiaire_web_batch", fake)
    monkeypatch.setattr(ndo_mod, "classify_local", lambda label: (None, None))
    out = _run_ndo(["Nordic Steel"])
    assert out["NomDonneurOrdre_method"].iloc[0] == "CLAUDE"
    batch, prompt, intro = appels[0]
    assert "nom d'entreprise" in prompt and "BENEFICIAIRE" not in prompt
    assert "donneurs d'ordre" in intro
    cache = json.loads((caches / "validated_classif_nomdonneurordre_e10_fe.json").read_text(encoding="utf-8"))
    assert cache["classif"] == {batch[0]: "NORDIC STEEL AB"}


def test_ndo_echec_technique_non_mis_en_cache(caches, monkeypatch):
    monkeypatch.setattr(ndo_mod, "call_claude_beneficiaire_web_batch", lambda *a, **k: None)
    monkeypatch.setattr(ndo_mod, "classify_local", lambda label: (None, None))
    out = _run_ndo(["Nordic Steel"])
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "OUTLIER"
    assert not (caches / "validated_classif_nomdonneurordre_e10_fe.json").exists()


# ---- Beneficiaire : entité locale, matching DGI ----------------------------------------

def _run_benef(lignes, ref=None, **kwargs):
    df = pd.DataFrame([{"RefBanque": "B1", "ReferenceTransaction": "TX1", "NifNni": "", **l} for l in lignes])
    return benef_mod.treating_beneficiaire(
        df, ref=ref or {}, dgi_index=_dgi_index(), public_index=_public_index(),
        api_id="E10_FE", cfg={}, warm_start=False, **kwargs,
    )


def test_benef_nif_exact_et_ordre_des_lignes_preserve(monkeypatch):
    monkeypatch.setattr(benef_mod, "call_claude_dgi_arbitrage_batch", lambda items, cfg, system_prompt=None: [None] * len(items))
    out = _run_benef([
        {"Beneficiaire": "SOC EXEMPLE TEST", "NifNni": "01371954"},
        {"Beneficiaire": "Mohamed Ould Ahmed"},
        {"Beneficiaire": "SNIM"},
        {"Beneficiaire": "SOC EXEMPLE TEST", "NifNni": "01371954"},
    ])
    assert list(out["Beneficiaire_method"]) == ["NIF_EXACT", "PARTICULIER", "PUBLIC_ENT", "NIF_EXACT"]
    assert out["Beneficiaire_Normalisé"].iloc[0] == "SOCIETE EXEMPLE DE TEST SARL"


def test_benef_valeur_deja_cible_du_referentiel():
    out = _run_benef([{"Beneficiaire": "societe chinguite pour les services"}],
                     ref={"STE CHINGUITE": "SOCIETE CHINGUITE POUR LES SERVICES"})
    assert out["Beneficiaire_method"].iloc[0] == "MAP_CIBLE"


def test_benef_dgi_exact_normalise():
    out = _run_benef([{"Beneficiaire": "Societe Exemple de Test SARL."}])
    assert out["Beneficiaire_Normalisé"].iloc[0] == "SOCIETE EXEMPLE DE TEST SARL"
    assert out["Beneficiaire_method"].iloc[0] == "DGI_EXACT_NORM"


def test_benef_arbitrage_claude_avec_prompt_flux_entrant(caches, monkeypatch):
    dgi = _dgi_index()
    candidats = list(dgi["clean_to_orig"])[:2]
    monkeypatch.setattr(benef_mod, "_dgi_fuzzy_batch",
                        lambda labels, *a, **k: {lab: [(candidats[0], 85.0), (candidats[1], 83.0)] for lab in labels})
    prompts = []

    def fake(items, cfg, system_prompt=None):
        prompts.append(system_prompt)
        return [1] * len(items)

    monkeypatch.setattr(benef_mod, "call_claude_dgi_arbitrage_batch", fake)
    monkeypatch.setattr(benef_mod, "classify_local", lambda label: (None, None))
    df = pd.DataFrame([{"RefBanque": "B1", "ReferenceTransaction": "TX1", "NifNni": "", "Beneficiaire": "STE EXEMPLE"}])
    out = benef_mod.treating_beneficiaire(df, ref={}, dgi_index=dgi, public_index=_public_index(),
                                          api_id="E10_FE", cfg={}, warm_start=True)
    assert out["Beneficiaire_method"].iloc[0] == "DGI_CLAUDE_ARBITRAGE"
    assert out["Beneficiaire_Normalisé"].iloc[0] == dgi["clean_to_orig"][candidats[0]]
    assert "BENEFICIAIRE d'un flux entrant" in prompts[0]
    assert (caches / "validated_classif_beneficiaire_e10_fe.json").exists()


# ---- Pipeline -----------------------------------------------------------------------------

def test_produit_hors_perimetre_n_a_pas_de_module():
    from e10_fe.pipeline import build_e10_field_processors

    with pytest.raises(ValueError, match="Produit"):
        build_e10_field_processors({"fields": [{"name": "Produit", "type": "categorical"}]})
