"""
shared/db_connector.py
========================
Connexion SQL Server et chargement de données. Credentials chargés depuis .env.

Toute erreur de connexion/lecture est ré-encapsulée en DataSourceError avec un
message explicite (opération, table, cause probable) — jamais une trace
SQLAlchemy/pyodbc brute remontée telle quelle à l'appelant. Une source vide
(0 ligne) n'est pas une erreur mais déclenche un avertissement explicite dans
les logs, pour ne jamais faire croire silencieusement qu'un run "à 0 ligne"
s'est bien passé sans que personne ne le remarque.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

from shared.errors import DataSourceError

# Chargement de confort pour un usage direct de ce module (script ad hoc, REPL) :
# SANS override, pour ne jamais écraser une variable déjà posée par l'appelant.
# Le chargement AUTORITAIRE de .env (override=True, qui fait primer .env sur une
# éventuelle variable d'environnement périmée de la machine) a lieu une seule fois
# au démarrage du process, dans le point d'entrée CLI — voir
# shared/env_loader.py::load_env_authoritative et {api}/run_pipeline.py.
load_dotenv()

_REQUIRED_ENV_VARS = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"]


def _short_db_error(exc: Exception) -> str:
    """Les erreurs pyodbc/SQLAlchemy incluent souvent la requête SQL complète et
    plusieurs couches d'exception imbriquées — on ne garde que la première ligne
    significative pour rester lisible dans un message d'erreur/log."""
    msg = str(exc).strip()
    first_line = msg.split("\n")[0]
    return first_line[:300]


def get_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    missing = [k for k in _REQUIRED_ENV_VARS if not os.getenv(k)]
    if missing:
        raise DataSourceError(
            f"Connexion base de données impossible : variable(s) d'environnement manquante(s) "
            f"dans .env : {missing}. Vérifie que le fichier .env existe à la racine du repo et "
            f"est bien renseigné (voir .env.example)."
        )

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


def _run_query(query, params: Optional[dict], operation: str, table_name: str) -> pd.DataFrame:
    from sqlalchemy.exc import SQLAlchemyError

    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    db = os.getenv("DB_NAME", "DATAWAREHOUSE_SA_PROD")
    try:
        with get_engine().connect() as conn:
            df = pd.read_sql(query, conn, params=params or {})
    except DataSourceError:
        raise
    except SQLAlchemyError as exc:
        raise DataSourceError(
            f"Échec de {operation} sur la table '{table_name}' (base '{db}' — {host}:{port}) : "
            f"{_short_db_error(exc)}. Vérifie l'accès réseau à la base, les credentials dans .env, "
            f"et que la table/colonne existe bien telle que configurée dans le YAML."
        ) from exc
    except Exception as exc:
        raise DataSourceError(
            f"Échec inattendu lors de {operation} sur la table '{table_name}' (base '{db}') : "
            f"{_short_db_error(exc)}."
        ) from exc

    if df.empty:
        print(f"  [AVERTISSEMENT] {operation} sur '{table_name}' n'a retourné AUCUNE ligne "
              f"(source vide pour cette fenêtre de traitement).")
    return df


def load_table(table_name: str) -> pd.DataFrame:
    """Charge toutes les colonnes d'une table SQL Server (Initial Load — historique complet)."""
    from sqlalchemy import text

    db = os.getenv("DB_NAME", "DATAWAREHOUSE_SA_PROD")
    query = text(f"SELECT * FROM [{db}].[dbo].[{table_name}]")
    return _run_query(query, None, "la lecture complète (Initial Load)", table_name)


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
        query = text(f"SELECT * FROM [{db}].[dbo].[{table_name}] WHERE [{dt_cr_col}] > :since")
        params = {"since": since}
    return _run_query(query, params, "la lecture du delta (Incremental Load)", table_name)


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
    return _run_query(text(query), None, "l'exécution de la requête personnalisée", "(requête ad hoc)")


def get_max_dtcr(df: pd.DataFrame, dt_cr_col: str) -> Optional[datetime]:
    """MAX(dt_cr_col) parmi les lignes réellement présentes dans df ; None si df vide/non parsable."""
    if df.empty or dt_cr_col not in df.columns:
        return None
    parsed = pd.to_datetime(df[dt_cr_col], errors="coerce")
    if parsed.isna().all():
        return None
    # floor("us") avant to_pydatetime() : datetime Python ne va qu'à la microseconde,
    # sans ça pandas émet un UserWarning "Discarding nonzero nanoseconds" à chaque
    # colonne dtCr en précision nanoseconde (le cas sur la vraie base) — sans perte
    # réelle, une précision sub-microseconde n'a aucun sens pour un delta hebdomadaire.
    return parsed.max().floor("us").to_pydatetime()


def load_file(path: str, cfg: dict) -> pd.DataFrame:
    """Charge un fichier CSV ou Excel local (dtype=str pour préserver les valeurs brutes)."""
    p = Path(path)
    inp = cfg.get("input", {})
    if not p.exists():
        raise DataSourceError(
            f"Fichier source introuvable : '{path}' (chemin résolu depuis le répertoire courant "
            f"— vérifie l'orthographe et que la commande est bien lancée depuis la racine du repo)."
        )
    try:
        if p.suffix.lower() in (".xlsx", ".xls"):
            df = pd.read_excel(p, sheet_name=inp.get("sheet", 0), dtype=str, keep_default_na=False)
        elif p.suffix.lower() in (".csv", ".tsv"):
            df = pd.read_csv(
                p, sep=inp.get("sep", ";"),
                encoding=inp.get("encoding", "utf-8-sig"), dtype=str,
                keep_default_na=False,
            )
        else:
            raise DataSourceError(
                f"Format de fichier non supporté : '{p.suffix}' (fichier '{path}') — "
                f"formats acceptés : .csv, .tsv, .xlsx, .xls."
            )
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Échec de lecture du fichier '{path}' : {_short_db_error(exc)}.") from exc

    if df.empty:
        print(f"  [AVERTISSEMENT] Le fichier '{path}' ne contient aucune ligne (source vide).")
    return df
