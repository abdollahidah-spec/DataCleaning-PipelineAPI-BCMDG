"""
Chargement SQL Server (shared/db_connector.py) :

1. Colonnes texte NVARCHAR(MAX) lues via CAST en NVARCHAR(4000). Constat sur la
   vraie base (17/09/2026) : toutes les colonnes texte des tables E07/E10 sont en
   (MAX), ce qui force le pilote ODBC à lire cellule par cellule — 20 000 lignes
   en 11,6 s, contre 2,7 s une fois castées. Longueur réelle maximale relevée :
   149 caractères, toutes tables confondues — aucune troncature possible.
2. Nouvelle tentative automatique sur coupure réseau passagère (constaté : Initial
   Load E10 interrompu après 43 min par « ConnectionWrite (10054) »), jamais sur
   une vraie erreur SQL.
Aucun appel réseau : moteur et lecture pandas sont simulés.
"""
import pandas as pd
import pytest
from sqlalchemy.exc import DBAPIError

import shared.db_connector as dbc
from shared.errors import DataSourceError


class _FakeConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeEngine:
    def connect(self): return _FakeConn()


@pytest.fixture
def moteur(monkeypatch):
    monkeypatch.setattr(dbc, "get_engine", lambda: _FakeEngine())
    monkeypatch.setattr(dbc.time, "sleep", lambda s: None)


def _erreur(message: str) -> DBAPIError:
    return DBAPIError("SELECT ...", {}, Exception(message))


def test_select_clause_caste_uniquement_les_colonnes_max():
    clause = dbc._select_clause(["Devise", "MontantTransaction", "dtCr"], cast_text={"Devise"})
    assert clause == "CAST([Devise] AS NVARCHAR(4000)) AS [Devise], [MontantTransaction], [dtCr]"
    assert dbc._select_clause(None, cast_text={"Devise"}) == "*"


def test_load_table_requete_avec_cast_et_filtre_date(moteur, monkeypatch):
    requetes = []

    def fake_read_sql(query, conn, params=None, chunksize=None):
        requetes.append((str(query), params))
        return pd.DataFrame({"Devise": ["USD"]})

    monkeypatch.setattr(dbc.pd, "read_sql", fake_read_sql)
    from datetime import datetime
    dbc.load_table("E10EtatBcmFluxEntrants", columns=["Devise", "dtCr"], cast_text={"Devise"},
                   dt_cr_col="dtCr", since=datetime(2024, 1, 1))
    sql, params = requetes[0]
    assert "CAST([Devise] AS NVARCHAR(4000)) AS [Devise], [dtCr]" in sql
    assert "WHERE [dtCr] >= :since" in sql and params == {"since": datetime(2024, 1, 1)}


def test_load_table_delta_meme_projection(moteur, monkeypatch):
    requetes = []
    monkeypatch.setattr(dbc.pd, "read_sql",
                        lambda q, c, params=None, chunksize=None: requetes.append(str(q)) or pd.DataFrame({"a": [1]}))
    dbc.load_table_delta("T", "dtCr", pd.Timestamp("2026-09-01").to_pydatetime(),
                         columns=["Pays"], cast_text={"Pays"})
    assert "CAST([Pays] AS NVARCHAR(4000)) AS [Pays]" in requetes[0] and "[dtCr] > :since" in requetes[0]


def test_nouvelle_tentative_apres_coupure_reseau(moteur, monkeypatch):
    appels = {"n": 0}

    def fake_read_sql(query, conn, params=None, chunksize=None):
        appels["n"] += 1
        if appels["n"] == 1:
            raise _erreur("[DBNETLIB]ConnectionWrite (send()). (10054)")
        return pd.DataFrame({"x": [1, 2]})

    monkeypatch.setattr(dbc.pd, "read_sql", fake_read_sql)
    df = dbc.load_table("T", columns=["x"], retries=2)
    assert appels["n"] == 2 and len(df) == 2


def test_lecture_par_paquets_reprise_depuis_le_debut(moteur, monkeypatch):
    """Après une coupure en milieu de lecture, les paquets déjà lus sont écartés :
    jamais de doublons dans le résultat final."""
    appels = {"n": 0}

    def fake_read_sql(query, conn, params=None, chunksize=None):
        appels["n"] += 1
        premier = appels["n"] == 1

        def paquets():
            yield pd.DataFrame({"x": [1, 2]})
            if premier:
                raise _erreur("Communication link failure (08S01)")
            yield pd.DataFrame({"x": [3]})
        return paquets()

    monkeypatch.setattr(dbc.pd, "read_sql", fake_read_sql)
    df = dbc.load_table("T", columns=["x"], chunk_size=2, retries=1)
    assert list(df["x"]) == [1, 2, 3]


def test_pas_de_nouvelle_tentative_sur_erreur_sql(moteur, monkeypatch):
    appels = {"n": 0}

    def fake_read_sql(*a, **k):
        appels["n"] += 1
        raise _erreur("Invalid column name 'Produits'. (207)")

    monkeypatch.setattr(dbc.pd, "read_sql", fake_read_sql)
    with pytest.raises(DataSourceError, match="Invalid column name"):
        dbc.load_table("T", columns=["Produits"], retries=3)
    assert appels["n"] == 1


def test_echec_apres_toutes_les_tentatives(moteur, monkeypatch):
    appels = {"n": 0}

    def fake_read_sql(*a, **k):
        appels["n"] += 1
        raise _erreur("Erreur réseau générale (10054)")

    monkeypatch.setattr(dbc.pd, "read_sql", fake_read_sql)
    with pytest.raises(DataSourceError, match="3 tentative"):
        dbc.load_table("T", columns=["x"], retries=2)
    assert appels["n"] == 3


def test_load_data_transmet_colonnes_max_et_tentatives(monkeypatch):
    """Branchement dans le pipeline : seules les colonnes (MAX) projetées sont castées,
    et `load.retries` du YAML est transmis."""
    import shared.db_connector as db
    from shared.config import load_config
    from e09_pe.pipeline import E09Pipeline

    cfg = load_config("e09_pe/config/E09_PE.yaml")
    types = {"NumCredoc": ("nvarchar", -1), "Devise": ("nvarchar", -1), "RefBanque": ("nvarchar", 50),
             "MontantEcheance": ("float", None), "DateEcheance": ("datetime2", None), "dtCr": ("datetime2", None),
             "Autre": ("nvarchar", -1)}
    monkeypatch.setattr(db, "column_types", lambda table: types)
    recus = {}

    def fake_load_table(table, **kwargs):
        recus.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(db, "load_table", fake_load_table)
    E09Pipeline(cfg).load_data("initial", None)
    assert recus["cast_text"] == {"NumCredoc", "Devise"}
    assert recus["retries"] == cfg["load"]["retries"]


def test_load_data_cast_desactivable(monkeypatch):
    import shared.db_connector as db
    from shared.config import load_config
    from e09_pe.pipeline import E09Pipeline

    cfg = load_config("e09_pe/config/E09_PE.yaml")
    cfg["load"]["cast_max_text"] = False
    monkeypatch.setattr(db, "column_types", lambda table: {"Devise": ("nvarchar", -1), "NumCredoc": ("nvarchar", -1),
                                                          "MontantEcheance": ("float", None), "DateEcheance": ("datetime2", None),
                                                          "dtCr": ("datetime2", None), "RefBanque": ("nvarchar", -1)})
    recus = {}
    monkeypatch.setattr(db, "load_table", lambda table, **kw: recus.update(kw) or pd.DataFrame())
    E09Pipeline(cfg).load_data("initial", None)
    assert not recus["cast_text"]
