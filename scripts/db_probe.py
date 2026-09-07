"""
scripts/db_probe.py
=====================
Diagnostic de volumétrie AVANT de lancer une pipeline — répond en quelques
secondes à : combien de lignes, combien de colonnes, quelles colonnes sont
réellement utilisées, et sur quelle période s'étale l'historique.

À lancer quand un Initial Load semble anormalement long : il permet de savoir
si le problème vient du volume (table énorme) ou d'autre chose (réseau, droits).

Lecture seule (COUNT + MIN/MAX + métadonnées de schéma), aucune écriture.

Usage (depuis la racine du repo) :
    python -m scripts.db_probe --config e09_pe/config/E09_PE.yaml
"""
from __future__ import annotations

import argparse
import sys
import time

from shared.config import load_config
from shared.console import force_utf8_console
from shared.env_loader import load_env_authoritative


def probe(config_path: str) -> int:
    from sqlalchemy import text

    from shared.db_connector import get_engine
    from shared.query_columns import required_columns

    cfg = load_config(config_path)
    table = cfg["input"]["table_name"]
    dt_cr = cfg["input"].get("dt_cr_column", "dtCr")
    needed = required_columns(cfg)

    print(f"\nAPI     : {cfg['api_id']}")
    print(f"Table   : {table}")
    print("Connexion à la base…", flush=True)

    t0 = time.time()
    with get_engine().connect() as conn:
        print(f"Connecté en {time.time() - t0:.1f} s\n", flush=True)

        cols = [r[0] for r in conn.execute(text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t ORDER BY ORDINAL_POSITION"), {"t": table})]
        print(f"Colonnes dans la table          : {len(cols)}")
        print(f"Colonnes utilisées par la pipeline : {len(needed)}  -> {sorted(needed)}")
        inutiles = [c for c in cols if c not in needed]
        if inutiles:
            print(f"Colonnes chargées pour rien avec SELECT * : {len(inutiles)}  -> {inutiles}")

        print("\nComptage des lignes (peut prendre un moment)…", flush=True)
        t0 = time.time()
        n = conn.execute(text(f"SELECT COUNT(*) FROM [dbo].[{table}]")).scalar()
        print(f"Nombre total de lignes : {n:,}".replace(",", " ") + f"   ({time.time() - t0:.1f} s)")

        if dt_cr in cols:
            row = conn.execute(text(
                f"SELECT MIN([{dt_cr}]), MAX([{dt_cr}]) FROM [dbo].[{table}]")).fetchone()
            print(f"Période ({dt_cr})       : {row[0]}  ->  {row[1]}")

            print("\nRépartition par année :")
            for annee, cnt in conn.execute(text(
                f"SELECT YEAR([{dt_cr}]) AS a, COUNT(*) AS n FROM [dbo].[{table}] "
                f"GROUP BY YEAR([{dt_cr}]) ORDER BY a")):
                print(f"   {annee} : {cnt:>12,}".replace(",", " "))

    print("\nSi le volume est élevé, deux leviers dans le YAML :")
    print("  - load.initial_since : borne l'Initial Load à partir d'une date")
    print("  - load.chunk_size    : lecture par paquets, avec progression dans les logs")
    return 0


def main() -> int:
    load_env_authoritative()
    force_utf8_console()
    parser = argparse.ArgumentParser(description="Diagnostic de volumétrie d'une table source")
    parser.add_argument("--config", required=True, help="Chemin du YAML de l'API")
    args = parser.parse_args()
    try:
        return probe(args.config)
    except Exception as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
