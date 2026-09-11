"""
Moteur de validation E07_FS (e07_fs/fields/transactions.py) : montant >= 0,
taux >= 0, DateTransaction < dtCr (au jour), gabarit « sans activité ».
"""
from dataclasses import replace

import pandas as pd
import pytest

from e07_fs.fields.transactions import SHEET_COLUMNS, TransactionsConfig, run_all_rules

_GABARIT = ("TypeSwfit", "ModeReglement", "Devise", "NomDonneurOrdre", "NifNni",
            "SourceDevise", "Beneficiaire", "Produit", "NatureEconomique")


@pytest.fixture
def cfg() -> TransactionsConfig:
    return TransactionsConfig(
        montant="MontantTransaction", taux="TauxDeChange", date_transaction="DateTransaction",
        dt_cr="dtCr", ref_banque="RefBanque", reference_transaction="ReferenceTransaction",
        pays="Pays", gabarit_na=_GABARIT,
    )


def _ligne(**kw) -> dict:
    d = {"RefBanque": "BANK01", "ReferenceTransaction": "TX1", "DateTransaction": "2026-08-20",
         "dtCr": "2026-09-01", "MontantTransaction": "1500", "TauxDeChange": "38.5", "Pays": "FR"}
    d.update({c: "X" for c in _GABARIT})
    d.update(kw)
    return d


def _sans_activite(**kw) -> dict:
    d = _ligne(ReferenceTransaction="NA", MontantTransaction="0", TauxDeChange="0", Pays="NoAs")
    d.update({c: "NA" for c in _GABARIT})
    d.update(kw)
    return d


def _anomalies(cfg, *lignes) -> pd.DataFrame:
    return run_all_rules(pd.DataFrame(list(lignes)), cfg)[1]


def test_ligne_conforme(cfg):
    annote, anomalies = run_all_rules(pd.DataFrame([_ligne()]), cfg)
    assert anomalies.empty
    assert annote["_TX_row_conforme"].iloc[0]


# ---- montant / taux >= 0 -------------------------------------------------------------

def test_montant_et_taux_nuls_sont_valides(cfg):
    """Le ticket exige >= 0 (et non > 0) : zéro est valide."""
    assert _anomalies(cfg, _ligne(MontantTransaction="0", TauxDeChange="0")).empty


def test_montant_negatif(cfg):
    a = _anomalies(cfg, _ligne(MontantTransaction="-50"))
    assert list(a["Rule"]) == ["AMOUNT_NON_NEGATIVE"]
    assert "< 0" in a["Detail"].iloc[0]


@pytest.mark.parametrize("valeur", ["abc", ""])
def test_montant_non_numerique_ou_vide(cfg, valeur):
    a = _anomalies(cfg, _ligne(MontantTransaction=valeur))
    assert list(a["Rule"]) == ["AMOUNT_NON_NEGATIVE"]
    assert "non numérique" in a["Detail"].iloc[0]


def test_virgule_decimale_acceptee(cfg):
    assert _anomalies(cfg, _ligne(MontantTransaction="1500,5", TauxDeChange="38,5")).empty


def test_taux_negatif(cfg):
    a = _anomalies(cfg, _ligne(TauxDeChange="-2"))
    assert list(a["Rule"]) == ["RATE_NON_NEGATIVE"]


# ---- DateTransaction < dtCr ------------------------------------------------------------

def test_date_anterieure_valide(cfg):
    assert _anomalies(cfg, _ligne(DateTransaction="2026-08-31", dtCr="2026-09-01")).empty


def test_date_posterieure_anomalie(cfg):
    a = _anomalies(cfg, _ligne(DateTransaction="2026-09-05", dtCr="2026-09-01"))
    assert list(a["Rule"]) == ["DATE_VALIDITY"]
    assert "2026-09-05" in a["Detail"].iloc[0] and "2026-09-01" in a["Detail"].iloc[0]


def test_meme_jour_anomalie_en_strict_valide_en_inclusif(cfg):
    """Ticket : « dateTransaction < dtCr » (strict). L'option date_inclusive
    autorise le même jour, si le métier le confirme."""
    ligne = _ligne(DateTransaction="2026-09-01 08:00:00", dtCr="2026-09-01 17:00:00")
    assert list(_anomalies(cfg, ligne)["Rule"]) == ["DATE_VALIDITY"]
    assert _anomalies(replace(cfg, date_inclusive=True), ligne).empty


def test_heure_ignoree(cfg):
    """Comparaison au jour : 23:59 la veille reste antérieure à dtCr."""
    assert _anomalies(cfg, _ligne(DateTransaction="2026-08-31 23:59:00", dtCr="2026-09-01 00:00:01")).empty


def test_date_non_parsable(cfg):
    a = _anomalies(cfg, _ligne(DateTransaction="pas une date"))
    assert list(a["Rule"]) == ["DATE_VALIDITY"]
    assert "DateTransaction non parsable" in a["Detail"].iloc[0]


def test_dtcr_non_parsable(cfg):
    a = _anomalies(cfg, _ligne(dtCr=""))
    assert "dtCr non parsable" in a["Detail"].iloc[0]


def test_colonnes_deja_typees_par_sql_server(cfg):
    df = pd.DataFrame([_ligne()])
    df["DateTransaction"] = pd.to_datetime(df["DateTransaction"])
    df["dtCr"] = pd.to_datetime(df["dtCr"])
    df["MontantTransaction"] = df["MontantTransaction"].astype(float)
    assert run_all_rules(df, cfg)[1].empty


# ---- gabarit « sans activité » -----------------------------------------------------------

def test_message_sans_activite_conforme(cfg):
    assert _anomalies(cfg, _sans_activite()).empty


def test_message_sans_activite_casse_et_espaces_toleres(cfg):
    assert _anomalies(cfg, _sans_activite(Devise=" na ", Pays="NOAS")).empty


def test_message_sans_activite_non_conforme_liste_les_ecarts(cfg):
    a = _anomalies(cfg, _sans_activite(Devise="USD", MontantTransaction="500", Pays="FR"))
    assert list(a["Rule"]) == ["NO_ACTIVITY_CONFORMITY"]
    detail = a["Detail"].iloc[0]
    assert "Devise!=NA" in detail and "MontantTransaction!=0" in detail and "Pays!=NoAs" in detail
    assert "TypeSwfit" not in detail


def test_transaction_reelle_non_soumise_au_gabarit(cfg):
    """Le gabarit ne vise que les messages dont la référence vaut NA ; un 'NA' isolé
    sur une vraie transaction relève de la règle NA du champ catégoriel."""
    assert _anomalies(cfg, _ligne(Devise="NA")).empty


def test_colonne_du_gabarit_absente_ignoree(cfg):
    ligne = _sans_activite()
    del ligne["Produit"]
    assert _anomalies(cfg, ligne).empty


def test_plusieurs_regles_plusieurs_lignes(cfg):
    a = _anomalies(cfg, _ligne(MontantTransaction="-1", TauxDeChange="-1", DateTransaction="2026-09-02"))
    assert sorted(a["Rule"]) == ["AMOUNT_NON_NEGATIVE", "DATE_VALIDITY", "RATE_NON_NEGATIVE"]
    assert set(a["ReferenceTransaction"]) == {"TX1"}


def test_delta_vide(cfg):
    vide = pd.DataFrame(columns=list(_ligne()))
    annote, anomalies = run_all_rules(vide, cfg)
    assert anomalies.empty and list(anomalies.columns) == SHEET_COLUMNS
    assert len(annote) == 0


# ---- configuration -------------------------------------------------------------------------

def test_projection_sql_inclut_les_colonnes_du_gabarit():
    """SourceDevise et Produit ne sont normalisés par aucun champ, mais le contrôle
    du gabarit les lit : la requête doit les rapatrier. Les colonnes techniques et
    les colonnes de sortie ne doivent jamais être demandées."""
    from shared.config import load_config
    from shared.query_columns import projection_for

    colonnes = set(projection_for(load_config("e07_fs/config/E07_FS.yaml")))
    assert {"SourceDevise", "Produit", "NifNni", "MontantTransaction", "TauxDeChange",
            "DateTransaction", "dtCr", "ReferenceTransaction", "TypeSwfit"} <= colonnes
    assert not colonnes & {"FkId", "idSysLog", "fkidSyncOperation", "TypeSwift_Normalisé"}


def test_processeur_depuis_yaml():
    from shared.config import load_config
    from e07_fs.fields.transactions import build_transactions_processor

    cfg_yaml = load_config("e07_fs/config/E07_FS.yaml")
    bloc = next(f for f in cfg_yaml["fields"] if f["type"] == "transaction_validation")
    proc = build_transactions_processor(bloc)
    res = proc.process(pd.DataFrame([_ligne(MontantTransaction="-1")]), "E07_FS")
    assert res.sheet_names == {"outliers": "Anomalies_Transactions"}
    assert res.stats["n_anomalies_error"] == 1
    assert not proc.cfg.date_inclusive and "SourceDevise" in proc.cfg.gabarit_na
