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


def _select_clause(columns: Optional[list]) -> str:
    """Projection explicite des colonnes, ou `*` si non déterminée (voir
    shared/query_columns.py) — évite de rapatrier des colonnes inutilisées."""
    if not columns:
        return "*"
    return ", ".join(f"[{c}]" for c in columns)


def _run_query(query, params: Optional[dict], operation: str, table_name: str,
                chunk_size: Optional[int] = None, progress=None) -> pd.DataFrame:
    from sqlalchemy.exc import SQLAlchemyError

    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    db = os.getenv("DB_NAME", "DATAWAREHOUSE_SA_PROD")
    try:
        with get_engine().connect() as conn:
            if chunk_size:
                # Lecture par paquets : donne une progression pendant un
                # chargement long (sinon aucun retour pendant des heures) et
                # évite de garder un seul résultat géant côté curseur.
                frames, total = [], 0
                for chunk in pd.read_sql(query, conn, params=params or {}, chunksize=chunk_size):
                    frames.append(chunk)
                    total += len(chunk)
                    if progress:
                        progress(total)
                df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            else:
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


def existing_columns(table_name: str) -> set:
    """
    Colonnes réellement présentes dans la table (INFORMATION_SCHEMA) — requête de
    métadonnées, quasi instantanée. Permet de ne jamais demander une colonne
    absente (échec `Invalid column name`) et de signaler immédiatement un écart
    entre le YAML et le schéma réel, au lieu d'échouer bien plus tard.
    """
    from sqlalchemy import text

    query = text("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = :t")
    df = _run_query(query, {"t": table_name}, "la lecture du schéma", table_name)
    return set(df["COLUMN_NAME"]) if not df.empty else set()


def load_table(table_name: str, columns: Optional[list] = None, chunk_size: Optional[int] = None,
                progress=None, dt_cr_col: Optional[str] = None,
                since: Optional[datetime] = None) -> pd.DataFrame:
    """
    Initial Load. `columns` restreint la projection (défaut : toutes) ;
    `chunk_size` active la lecture par paquets avec progression ; `since` borne
    l'historique rapatrié (`load.initial_since` du YAML) — utile quand la table
    remonte plusieurs années dont le métier n'a pas besoin.
    """
    from sqlalchemy import text

    db = os.getenv("DB_NAME", "DATAWAREHOUSE_SA_PROD")
    select = _select_clause(columns)
    params = None
    where = ""
    if since is not None and dt_cr_col:
        where = f" WHERE [{dt_cr_col}] >= :since"
        params = {"since": since}
    query = text(f"SELECT {select} FROM [{db}].[dbo].[{table_name}]{where}")
    return _run_query(query, params, "la lecture complète (Initial Load)", table_name,
                       chunk_size=chunk_size, progress=progress)


def load_table_delta(table_name: str, dt_cr_col: str, since: Optional[datetime],
                      columns: Optional[list] = None, chunk_size: Optional[int] = None,
                      progress=None) -> pd.DataFrame:
    """
    Charge uniquement les lignes ajoutées depuis `since` (Incremental Load).
    `since=None` -> équivalent à un load_table() complet (aucun filtre).
    Même projection de colonnes et même lecture par paquets que load_table().
    """
    from sqlalchemy import text

    db = os.getenv("DB_NAME", "DATAWAREHOUSE_SA_PROD")
    select = _select_clause(columns)
    if since is None:
        query = text(f"SELECT {select} FROM [{db}].[dbo].[{table_name}]")
        params = {}
    else:
        query = text(f"SELECT {select} FROM [{db}].[dbo].[{table_name}] WHERE [{dt_cr_col}] > :since")
        params = {"since": since}
    return _run_query(query, params, "la lecture du delta (Incremental Load)", table_name,
                       chunk_size=chunk_size, progress=progress)


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
