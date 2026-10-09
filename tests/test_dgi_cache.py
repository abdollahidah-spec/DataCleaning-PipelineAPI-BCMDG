"""
Mémoire locale des résolutions DGI déterministes (shared/dgi_cache.py).

Le rapprochement des libellés nouveaux contre les ~52 000 raisons sociales DGI est
l'étape la plus longue d'un chargement (~25 min pour 8 000 libellés, mesuré le
17/09/2026) et son résultat ne dépend que du libellé et du contenu de la base DGI.
Il est donc mémorisé d'un run à l'autre, avec une empreinte de la base DGI comme
garde-fou. Aucun appel réseau dans ces tests : le rapprochement est simulé.
"""
import json

import pandas as pd
import pytest

from shared.dgi_cache import METHODES_MEMORISABLES, charger, chemin, empreinte_dgi, enregistrer


@pytest.fixture(autouse=True)
def etat_isole(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    return tmp_path


def _index(raisons) -> dict:
    from e10_fe.fields._entity_matching import prepare_dgi_index

    return prepare_dgi_index(pd.DataFrame({
        "RAISON_SOCIALE": raisons,
        "NIF": [f"0137195{i}" for i in range(len(raisons))],
        "FORME_JURIDIQUE": ["SARL"] * len(raisons),
    }))


# ---- le fichier de mémoire lui-même ---------------------------------------------------

def test_aller_retour():
    emp = "3:42"
    assert enregistrer("E10_FE", "Beneficiaire", emp, {"ACME": ("ACME SA", "DGI_FUZZY_STRONG")}) == 1
    assert charger("E10_FE", "Beneficiaire", emp) == {"ACME": ("ACME SA", "DGI_FUZZY_STRONG")}


def test_seules_les_methodes_deterministes_sont_memorisees():
    resolutions = {
        "A": ("A SA", "DGI_EXACT_NORM"),
        "B": ("B SA", "DGI_FUZZY_STRONG"),
        "C": ("OUTLIER", "DGI_NO_MATCH"),
        "D": ("D SA", "DGI_CLAUDE_ARBITRAGE"),   # payant -> cache warm-start du champ
        "E": ("SNIM", "PUBLIC_ENT"),              # dépend d'un autre fichier
    }
    assert enregistrer("E10_FE", "Beneficiaire", "x", resolutions) == 3
    assert set(charger("E10_FE", "Beneficiaire", "x")) == {"A", "B", "C"}
    assert all(m in METHODES_MEMORISABLES for _, m in charger("E10_FE", "Beneficiaire", "x").values())


def test_base_dgi_differente_invalide_la_memoire(capsys):
    enregistrer("E10_FE", "Beneficiaire", "100:2000", {"A": ("A SA", "DGI_EXACT_NORM")})

    assert charger("E10_FE", "Beneficiaire", "101:2050") == {}
    assert "base fiscale différente" in capsys.readouterr().out


def test_empreinte_suit_le_contenu_dgi():
    assert empreinte_dgi(_index(["A SARL"])) != empreinte_dgi(_index(["A SARL", "B SARL"]))
    assert empreinte_dgi(_index(["A SARL"])) == empreinte_dgi(_index(["A SARL"]))


def test_fichier_illisible_ignore(etat_isole):
    chemin("E10_FE", "Beneficiaire").write_text("{ pas du json", encoding="utf-8")
    assert charger("E10_FE", "Beneficiaire", "x") == {}


# ---- intégration : le rapprochement n'est pas refait au run suivant --------------------

def _run_beneficiaire(monkeypatch, tmp_path, dgi, appels, cfg=None):
    import e10_fe.fields.beneficiaire as benef
    from e10_fe.fields._entity_matching import prepare_public_ent_index

    monkeypatch.setattr(benef, "_REFERENTIEL_DIR", tmp_path)
    monkeypatch.setattr(benef, "classify_local", lambda label: (None, None))
    candidats = list(dgi["clean_to_orig"])

    def faux_rapprochement(labels, *a, **k):
        appels.append(list(labels))
        return {lab: [(candidats[0], 95.0), (candidats[-1], 55.0)] for lab in labels}

    monkeypatch.setattr(benef, "_dgi_fuzzy_batch", faux_rapprochement)
    public = prepare_public_ent_index(pd.DataFrame({"Short Name": [], "Raison social - Public Ent": []}))
    df = pd.DataFrame({"Beneficiaire": ["STE INCONNUE DU REFERENTIEL"], "NifNni": [""],
                       "ReferenceTransaction": ["TX1"], "RefBanque": ["B1"]})
    return benef.treating_beneficiaire(df, ref={}, dgi_index=dgi, public_index=public,
                                       api_id="E10_FE", cfg=cfg or {}, warm_start=False)


def test_second_run_reutilise_la_memoire(tmp_path, monkeypatch, capsys):
    dgi = _index(["SOCIETE INCONNUE DU REFERENTIEL SARL", "AUTRE SOCIETE SARL"])
    appels = []

    premier = _run_beneficiaire(monkeypatch, tmp_path, dgi, appels)
    assert len(appels) == 1 and appels[0], "le premier run doit faire le rapprochement"
    assert premier["Beneficiaire_method"].iloc[0] == "DGI_FUZZY_STRONG"
    memoire = json.loads(chemin("E10_FE", "Beneficiaire").read_text(encoding="utf-8"))
    assert memoire["resolutions"]

    second = _run_beneficiaire(monkeypatch, tmp_path, dgi, appels)
    assert appels[1] == [], "le second run ne doit plus rien rapprocher"
    assert second["Beneficiaire_Normalisé"].iloc[0] == premier["Beneficiaire_Normalisé"].iloc[0]
    assert second["Beneficiaire_method"].iloc[0] == "DGI_FUZZY_STRONG"
    assert "déjà rapproché" in capsys.readouterr().out


def test_nouvelle_base_dgi_refait_le_rapprochement(tmp_path, monkeypatch):
    appels = []
    _run_beneficiaire(monkeypatch, tmp_path, _index(["SOCIETE INCONNUE DU REFERENTIEL SARL"]), appels)
    _run_beneficiaire(monkeypatch, tmp_path, _index(["SOCIETE INCONNUE DU REFERENTIEL SARL", "NOUVELLE SARL"]), appels)

    assert appels[1], "une base DGI différente doit invalider la mémoire"


def test_memoire_desactivable(tmp_path, monkeypatch):
    dgi = _index(["SOCIETE INCONNUE DU REFERENTIEL SARL"])
    appels = []
    cfg = {"matching": {"cache_resolutions": False}}

    _run_beneficiaire(monkeypatch, tmp_path, dgi, appels, cfg=cfg)
    _run_beneficiaire(monkeypatch, tmp_path, dgi, appels, cfg=cfg)

    assert appels[0] and appels[1], "mémoire désactivée : les deux runs rapprochent"
    assert not chemin("E10_FE", "Beneficiaire").exists()
