"""
Vérifie le garde-fou de load_query() : seules les requêtes de lecture sont
acceptées (protection contre une requête destructrice collée par erreur dans
l'outil d'extraction ad hoc).
"""
import pytest

from shared.db_connector import load_query


def test_load_query_rejects_delete():
    with pytest.raises(ValueError, match="lecture"):
        load_query("DELETE FROM dbo.E11EtatBcmReleveDesComptesCorrespondants")


def test_load_query_rejects_drop_table():
    with pytest.raises(ValueError, match="lecture"):
        load_query("DROP TABLE dbo.E11EtatBcmReleveDesComptesCorrespondants")


def test_load_query_accepts_select_syntax_check_only(monkeypatch):
    """On vérifie juste que le SELECT passe le garde-fou (pas d'appel DB réel ici) —
    monkeypatch get_engine pour éviter toute tentative de connexion."""
    import shared.db_connector as dbc

    class _FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _FakeEngine:
        def connect(self): return _FakeConn()

    monkeypatch.setattr(dbc, "get_engine", lambda: _FakeEngine())
    monkeypatch.setattr(dbc.pd, "read_sql", lambda *a, **k: "ok")

    result = load_query("SELECT * FROM dbo.E11EtatBcmReleveDesComptesCorrespondants")
    assert result == "ok"


def test_load_query_accepts_cte_with_syntax(monkeypatch):
    import shared.db_connector as dbc

    class _FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _FakeEngine:
        def connect(self): return _FakeConn()

    monkeypatch.setattr(dbc, "get_engine", lambda: _FakeEngine())
    monkeypatch.setattr(dbc.pd, "read_sql", lambda *a, **k: "ok")

    result = load_query("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte")
    assert result == "ok"
