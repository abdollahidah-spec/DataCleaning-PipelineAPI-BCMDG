"""
Champs catégoriels propres à E07_FS : TypeSwift, ModeReglement, NatureEconomique
(Devise, Pays, NomDonneurOrdre et Beneficiaire sont des portages d'E08, déjà
couverts par leurs propres tests). Aucun appel réseau : Claude est mocké, et les
caches warm-start sont redirigés vers tmp_path — jamais d'écriture dans
e07_fs/referentiel/.
"""
import json

import pandas as pd
import pytest

import e07_fs.fields.mode_reglement as mode_mod
import e07_fs.fields.nature_economique as nat_mod
import e07_fs.fields.typeswift as swift_mod

_REF = "e07_fs/referentiel/"


@pytest.fixture(autouse=True)
def caches(tmp_path, monkeypatch):
    for mod in (swift_mod, mode_mod, nat_mod):
        monkeypatch.setattr(mod, "_REFERENTIEL_DIR", tmp_path)
    return tmp_path


def _df(col, valeurs, refs=None) -> pd.DataFrame:
    refs = refs or ["TX1"] * len(valeurs)
    return pd.DataFrame({col: valeurs, "ReferenceTransaction": refs, "RefBanque": ["B1"] * len(valeurs)})


def _ecrire_cache(dossier, nom, classif) -> None:
    (dossier / nom).write_text(json.dumps({"version": "1.0.0", "classif": classif}), encoding="utf-8")


# ---- TypeSwift ----------------------------------------------------------------------

def _swift_ref():
    return swift_mod.load_typeswift_referentiel(_REF + "typeswift_referentiel.json", flux="FS")


def test_typeswift_codes_valides_compares_sans_espaces():
    out = swift_mod.treating_typeswift(_df("TypeSwfit", ["MT103", "mt 202 cov", "pacs.008"]),
                                       ref=_swift_ref(), warm_start=False)
    assert list(out["TypeSwift_Normalisé"]) == ["MT 103", "MT 202 COV", "pacs.008"]
    assert set(out["TypeSwfit_method"]) == {"MAP"}


def test_typeswift_numero_seul_prefixe_mt():
    out = swift_mod.treating_typeswift(_df("TypeSwfit", ["103"]), ref=_swift_ref(), warm_start=False)
    assert out["TypeSwift_Normalisé"].iloc[0] == "MT 103"
    assert out["TypeSwfit_method"].iloc[0] == "PREFIX"


def test_typeswift_bruit_et_montant_sont_noise():
    out = swift_mod.treating_typeswift(_df("TypeSwfit", ["1,500.00", "STRING"]), ref=_swift_ref(), warm_start=False)
    assert list(out["TypeSwift_Normalisé"]) == ["OUTLIER", "OUTLIER"]
    assert list(out["TypeSwfit_method"]) == ["NOISE", "NOISE"]


def test_typeswift_code_du_flux_entrant_est_outlier_en_fs():
    """pacs.004 n'est valide que pour les Flux Entrants (valid_fe)."""
    out = swift_mod.treating_typeswift(_df("TypeSwfit", ["pacs.004"]), ref=_swift_ref(), warm_start=False)
    assert out["TypeSwift_Normalisé"].iloc[0] == "OUTLIER"
    assert out["TypeSwfit_check"].iloc[0]


def test_typeswift_regle_na_temoin_reference_transaction():
    out = swift_mod.treating_typeswift(_df("TypeSwfit", ["NA", "NA"], refs=["NA", "TX1"]),
                                       ref=_swift_ref(), warm_start=False)
    assert list(out["TypeSwift_Normalisé"]) == ["NA", "OUTLIER"]


def test_typeswift_warm_start(caches):
    _ecrire_cache(caches, "validated_classif_typeswift_e07_fs.json", {"MT1O3": "MT 103"})
    out = swift_mod.treating_typeswift(_df("TypeSwfit", ["MT1O3 "]), ref=_swift_ref(), api_id="E07_FS")
    assert out["TypeSwift_Normalisé"].iloc[0] == "MT 103"
    assert out["TypeSwfit_method"].iloc[0] == "WARM"


def test_typeswift_classification_cumulative_referentiel_plus_cache(caches):
    _ecrire_cache(caches, "validated_classif_typeswift_e07_fs.json", {"MT1O3": "MT 103"})
    table = swift_mod.build_full_classification_typeswift(_swift_ref(), "E07_FS", "TypeSwfit", "TypeSwift_Normalisé")
    paires = set(map(tuple, table.to_numpy()))
    assert ("MT 103", "MT 103") in paires and ("MT1O3", "MT 103") in paires


# ---- ModeReglement ------------------------------------------------------------------

def test_mode_reglement_cascade():
    ref = mode_mod.load_mode_reglement_referentiel(_REF + "mode_reglement_referentiel.json")
    out = mode_mod.treating_mode_reglement(_df("ModeReglement", ["TL", " cd ", "TR", "SWIFT", "XX"]),
                                           ref=ref, warm_start=False)
    assert list(out["ModeReglement_Normalisé"]) == ["TL", "CD", "TL", "OUTLIER", "OUTLIER"]
    assert list(out["ModeReglement_method"]) == ["MAP", "MAP", "ALIAS", "NOISE", "OUTLIER"]


def test_mode_reglement_regle_na():
    ref = mode_mod.load_mode_reglement_referentiel(_REF + "mode_reglement_referentiel.json")
    out = mode_mod.treating_mode_reglement(_df("ModeReglement", ["NA", "NA"], refs=["NA", "TX1"]),
                                           ref=ref, warm_start=False)
    assert list(out["ModeReglement_Normalisé"]) == ["NA", "OUTLIER"]


# ---- NatureEconomique ---------------------------------------------------------------

def _nat_ref():
    return nat_mod.load_nature_economique_referentiel(_REF + "nature_economique_referentiel_E07.json")


def _interdire_claude(monkeypatch):
    def _refus(*_a, **_k):
        raise AssertionError("Claude ne doit pas être sollicité pour cette valeur")
    monkeypatch.setattr(nat_mod, "call_claude_match_batch", _refus)


def test_nature_eco_label_exact_et_mapping_direct(monkeypatch):
    _interdire_claude(monkeypatch)
    ref = _nat_ref()
    out = nat_mod.treating_nature_economique(_df("NatureEconomique", ["Fret maritime", "riz", "AUTRE"]),
                                             ref=ref, warm_start=False)
    attendus = ["FRET MARITIME", "PRODUITS ALIMENTAIRES", "AUTRES"]
    assert list(out["NatureEconomique_Normalisé"]) == attendus
    assert set(out["NatureEconomique_method"]) == {"MAP"}
    assert list(out["NatureEconomique_Categorie"]) == [ref.label_vers_categorie[l] for l in attendus]


def test_nature_eco_outliers_evidents_sans_appel_claude(monkeypatch):
    """Nom de personne, adresse, nombre, modalité vague, pays, en-tête : filtrés
    avant Claude (pré-filtre de l'ancien repo) — rien à payer pour ces valeurs."""
    _interdire_claude(monkeypatch)
    valeurs = ["Mohamed Ould Ahmed", "12 RUE DE PARIS", "123456", "TRANSFERTS", "FRANCE", "STRING"]
    out = nat_mod.treating_nature_economique(_df("NatureEconomique", valeurs), ref=_nat_ref(), warm_start=False)
    assert set(out["NatureEconomique_Normalisé"]) == {"OUTLIER"}
    assert set(out["NatureEconomique_method"]) == {"NOISE"}


def test_nature_eco_warm_start_insensible_casse_et_espaces(caches, monkeypatch):
    _interdire_claude(monkeypatch)
    _ecrire_cache(caches, "validated_classif_nature_economique_e07_fs.json",
                  {"Assurance": "ASSURANCE ET REASSURANCE"})
    out = nat_mod.treating_nature_economique(_df("NatureEconomique", ["  ASSURANCE "]),
                                             ref=_nat_ref(), api_id="E07_FS")
    assert out["NatureEconomique_Normalisé"].iloc[0] == "ASSURANCE ET REASSURANCE"
    assert out["NatureEconomique_method"].iloc[0] == "WARM"


def test_nature_eco_claude_liste_fermee_une_fois_par_valeur_nettoyee(caches, monkeypatch):
    """Deux saisies brutes d'une même valeur nettoyée = un seul item envoyé à Claude,
    et chaque variante brute est mise en cache."""
    appels = []

    def fake(batch, valid_labels, system_prompt, cfg):
        appels.append(list(batch))
        assert "OUTLIER" not in valid_labels and "TOURISME SEJOUR" in valid_labels
        return ["TOURISME SEJOUR"] * len(batch)

    monkeypatch.setattr(nat_mod, "call_claude_match_batch", fake)
    ref = _nat_ref()
    out = nat_mod.treating_nature_economique(
        _df("NatureEconomique", ["Frais hotel mission", "FRAIS  HOTEL MISSION"]), ref=ref, api_id="E07_FS"
    )
    assert appels == [["FRAIS HOTEL MISSION"]]
    assert set(out["NatureEconomique_Normalisé"]) == {"TOURISME SEJOUR"}
    assert set(out["NatureEconomique_method"]) == {"CLAUDE"}
    assert out["NatureEconomique_Categorie"].iloc[0] == ref.label_vers_categorie["TOURISME SEJOUR"]
    cache = json.loads((caches / "validated_classif_nature_economique_e07_fs.json").read_text(encoding="utf-8"))
    assert cache["classif"] == {"Frais hotel mission": "TOURISME SEJOUR",
                                "FRAIS  HOTEL MISSION": "TOURISME SEJOUR"}


def test_nature_eco_echec_technique_claude_non_mis_en_cache(caches, monkeypatch):
    monkeypatch.setattr(nat_mod, "call_claude_match_batch", lambda *a, **k: None)
    out = nat_mod.treating_nature_economique(_df("NatureEconomique", ["Frais hotel mission"]),
                                             ref=_nat_ref(), api_id="E07_FS")
    assert out["NatureEconomique_Normalisé"].iloc[0] == "OUTLIER"
    assert not (caches / "validated_classif_nature_economique_e07_fs.json").exists()


def test_nature_eco_regle_na(monkeypatch):
    _interdire_claude(monkeypatch)
    out = nat_mod.treating_nature_economique(_df("NatureEconomique", ["NA", "NA"], refs=["NA", "TX1"]),
                                             ref=_nat_ref(), warm_start=False)
    assert list(out["NatureEconomique_Normalisé"]) == ["NA", "OUTLIER"]
    assert list(out["NatureEconomique_Categorie"]) == ["", ""]


# ---- Pipeline -----------------------------------------------------------------------

def test_produit_hors_perimetre_n_a_pas_de_module():
    """Produit est volontairement hors périmètre (décision métier) : le déclarer
    comme champ catégoriel doit échouer explicitement, pas être ignoré."""
    from e07_fs.pipeline import build_e07_field_processors

    with pytest.raises(ValueError, match="Produit"):
        build_e07_field_processors({"fields": [{"name": "Produit", "type": "categorical"}]})
