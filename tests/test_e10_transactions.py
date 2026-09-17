"""
Moteur de validation E10_FE (e10_fe/fields/transactions.py) : montant >= 0,
taux >= 0, DateTransaction < dtCr (au jour), gabarit « sans activité » — et la
configuration réelle d'E10 (pas de SourceDevise, historique borné à 2024).
"""
from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest

from e10_fe.fields.transactions import SHEET_COLUMNS, TransactionsConfig, run_all_rules

# Pas de SourceDevise : la colonne n'existe pas dans E10EtatBcmFluxEntrants.
_GABARIT = ("TypeSwfit", "ModeReglement", "Devise", "NomDonneurOrdre", "NifNni",
            "Beneficiaire", "Produit", "NatureEconomique")


@pytest.fixture
def cfg() -> TransactionsConfig:
    return TransactionsConfig(
        montant="MontantTransaction", taux="TauxDeChange", date_transaction="DateTransaction",
        dt_cr="dtCr", ref_banque="RefBanque", reference_transaction="ReferenceTransaction",
        pays="Pays", gabarit_na=_GABARIT,
    )


def _ligne(**kw) -> dict:
    d = {"RefBanque": "BANK01", "ReferenceTransaction": "TX1", "DateTransaction": "2026-08-20 00:00:00.0000000",
         "dtCr": "2026-09-01 12:03:59.7133333", "MontantTransaction": 1500.0, "TauxDeChange": 38.5, "Pays": "FR"}
    d.update({c: "X" for c in _GABARIT})
    d.update(kw)
    return d


def _sans_activite(**kw) -> dict:
    d = _ligne(ReferenceTransaction="NA", MontantTransaction=0.0, TauxDeChange=0.0, Pays="NoAs")
    d.update({c: "NA" for c in _GABARIT})
    d.update(kw)
    return d


def _anomalies(cfg, *lignes) -> pd.DataFrame:
    return run_all_rules(pd.DataFrame(list(lignes)), cfg)[1]


def test_ligne_conforme_formats_reels_sql_server(cfg):
    """Formats relevés en base : DateTransaction et dtCr en texte à 7 décimales."""
    annote, anomalies = run_all_rules(pd.DataFrame([_ligne()]), cfg)
    assert anomalies.empty
    assert annote["_TX_row_conforme"].iloc[0]


def test_montant_negatif_et_taux_negatif(cfg):
    a = _anomalies(cfg, _ligne(MontantTransaction=-10.0, TauxDeChange=-1.0))
    assert sorted(a["Rule"]) == ["AMOUNT_NON_NEGATIVE", "RATE_NON_NEGATIVE"]


def test_montant_et_taux_nuls_valides(cfg):
    assert _anomalies(cfg, _ligne(MontantTransaction=0.0, TauxDeChange=0.0)).empty


def test_taux_null_sur_transaction_reelle(cfg):
    """~1 % des lignes réelles ont un TauxDeChange NULL : non numérique -> anomalie."""
    a = _anomalies(cfg, _ligne(TauxDeChange=None))
    assert list(a["Rule"]) == ["RATE_NON_NEGATIVE"]
    assert "non numérique" in a["Detail"].iloc[0]


def test_meme_jour_strict_et_option_inclusive(cfg):
    ligne = _ligne(DateTransaction="2026-09-01 00:00:00.0000000", dtCr="2026-09-01 12:03:59.7133333")
    assert list(_anomalies(cfg, ligne)["Rule"]) == ["DATE_VALIDITY"]
    assert _anomalies(replace(cfg, date_inclusive=True), ligne).empty


def test_date_posterieure_et_non_parsable(cfg):
    a = _anomalies(cfg, _ligne(DateTransaction="2026-09-05"), _ligne(DateTransaction="pas une date"))
    assert list(a["Rule"]) == ["DATE_VALIDITY", "DATE_VALIDITY"]


def test_message_sans_activite_conforme(cfg):
    assert _anomalies(cfg, _sans_activite()).empty


def test_message_sans_activite_taux_null_non_conforme(cfg):
    a = _anomalies(cfg, _sans_activite(TauxDeChange=None))
    assert sorted(a["Rule"]) == ["NO_ACTIVITY_CONFORMITY", "RATE_NON_NEGATIVE"]
    assert "TauxDeChange!=0" in a.loc[a["Rule"] == "NO_ACTIVITY_CONFORMITY", "Detail"].iloc[0]


def test_delta_vide(cfg):
    annote, anomalies = run_all_rules(pd.DataFrame(columns=list(_ligne())), cfg)
    assert anomalies.empty and list(anomalies.columns) == SHEET_COLUMNS and len(annote) == 0


# ---- configuration réelle ------------------------------------------------------------------

def test_projection_sql_e10_sans_source_devise():
    from shared.config import load_config
    from shared.query_columns import projection_for

    colonnes = set(projection_for(load_config("e10_fe/config/E10_FE.yaml")))
    assert {"Produit", "NifNni", "MontantTransaction", "TauxDeChange", "DateTransaction", "dtCr",
            "ReferenceTransaction", "TypeSwfit", "Beneficiaire", "NomDonneurOrdre"} <= colonnes
    assert "SourceDevise" not in colonnes
    assert not colonnes & {"Id", "Banque", "FkId", "idSysLog", "fkidSyncOperation"}


@pytest.mark.parametrize("chemin", ["e10_fe/config/E10_FE.yaml", "e07_fs/config/E07_FS.yaml"])
def test_historique_borne_au_1er_janvier_2024(chemin):
    from shared.base_api_pipeline import _parse_initial_since
    from shared.config import load_config

    assert _parse_initial_since(load_config(chemin)["load"]["initial_since"]) == datetime(2024, 1, 1)


def test_entites_inversees_par_rapport_a_e07():
    """Flux entrants : DGI (nif_nni + matching) sur Beneficiaire, recherche web sur
    NomDonneurOrdre — l'inverse d'E07."""
    from shared.config import load_config

    champs = {f["name"]: f for f in load_config("e10_fe/config/E10_FE.yaml")["fields"]}
    assert champs["Beneficiaire"]["columns"]["nif_nni"] == "NifNni" and "matching" in champs["Beneficiaire"]
    assert "nif_nni" not in champs["NomDonneurOrdre"]["columns"]
    assert champs["NomDonneurOrdre"]["llm"]["web_search_max_uses"] == 3
    assert champs["TypeSwift"]["flux"] == "FE"
