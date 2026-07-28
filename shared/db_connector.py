"""
shared/db_connector.py
========================
Connexion SQL Server et chargement de données. Credentials chargés depuis .env.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()


def get_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    driver = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
    url = URL.create(
        "mssql+pyodbc",
        username=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        query={"driver": driver},
    )
    return create_engine(url)


def load_table(table_name: str) -> pd.DataFrame:
    """Charge toutes les colonnes d'une table SQL Server (Initial Load — historique complet)."""
    from sqlalchemy import text

    db = os.getenv("DB_NAME", "DATAWAREHOUSE_SA_PROD")
    query = f"SELECT * FROM [{db}].[dbo].[{table_name}]"
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def load_table_delta(table_name: str, dt_cr_col: str, since: Optional[datetime]) -> pd.DataFrame:
    """
    Charge uniquement les lignes ajoutées depuis `since` (Incremental Load).
    `since=None` -> équivalent à un load_table() complet (aucun filtre).
    """
    from sqlalchemy import text

    db = os.getenv("DB_NAME", "DATAWAREHOUSE_SA_PROD")
    if since is None:
        query = text(f"SELECT * FROM [{db}].[dbo].[{table_name}]")
        params = {}
    else:
        query = text(
            f"SELECT * FROM [{db}].[dbo].[{table_name}] WHERE [{dt_cr_col}] > :since"
        )
        params = {"since": since}
    with get_engine().connect() as conn:
        return pd.read_sql(query, conn, params=params)


def load_query(query: str) -> pd.DataFrame:
    """
    Exécute une requête SQL arbitraire fournie par l'utilisateur — extraction ad hoc
    (voir e11_rdcc/ad_hoc_extraction.py), indépendante du run automatisé. Seules les
    requêtes de lecture (SELECT, ou WITH ... SELECT pour une CTE) sont autorisées :
    garde-fou contre une requête destructrice collée par erreur.
    """
    from sqlalchemy import text

    stripped = query.strip()
    if not (stripped[:6].upper() == "SELECT" or stripped[:4].upper() == "WITH"):
        raise ValueError(
            "load_query() n'accepte que des requêtes de lecture (SELECT / WITH ... SELECT) "
            "— requête refusée pour éviter une écriture accidentelle."
        )
    with get_engine().connect() as conn:
        return pd.read_sql(text(query), conn)


def get_max_dtcr(df: pd.DataFrame, dt_cr_col: str) -> Optional[datetime]:
    """MAX(dt_cr_col) parmi les lignes réellement présentes dans df ; None si df vide/non parsable."""
    if df.empty or dt_cr_col not in df.columns:
        return None
    parsed = pd.to_datetime(df[dt_cr_col], errors="coerce")
    if parsed.isna().all():
        return None
    return parsed.max().to_pydatetime()


def load_file(path: str, cfg: dict) -> pd.DataFrame:
    """Charge un fichier CSV ou Excel local (dtype=str pour préserver les valeurs brutes)."""
    p = Path(path)
    inp = cfg.get("input", {})
    if not p.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    if p.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(p, sheet_name=inp.get("sheet", 0), dtype=str, keep_default_na=False)
    if p.suffix.lower() in (".csv", ".tsv"):
        return pd.read_csv(
            p, sep=inp.get("sep", ";"),
            encoding=inp.get("encoding", "utf-8-sig"), dtype=str,
            keep_default_na=False,
        )
    raise ValueError(f"Format non supporté : {p.suffix}")
