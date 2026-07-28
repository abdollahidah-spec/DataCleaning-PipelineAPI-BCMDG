"""
scripts/seed_state.py
=======================
Seeding manuel de l'état incrémental (fichier JSON local, voir shared/state_store.py)
— usage ponctuel pour un scénario de bascule (ex: reprendre le traitement delta à
partir d'une date connue sans relancer un Initial Load complet). N'est JAMAIS
appelé automatiquement par le pipeline. Ne touche pas la base de données.

Usage (depuis la racine du repo) :
    python -m scripts.seed_state --api-id E11_RDCC --last-dtcr 2026-07-20
    python -m scripts.seed_state --api-id E11_RDCC   # sans --last-dtcr : jamais traité
"""
from __future__ import annotations

import argparse
from datetime import datetime

from shared.console import force_utf8_console
from shared.state_store import seed_initial_state


def main() -> None:
    force_utf8_console()
    parser = argparse.ArgumentParser(description="Seed manuel de l'état incrémental local")
    parser.add_argument("--api-id", required=True)
    parser.add_argument("--last-dtcr", default=None, help="Format YYYY-MM-DD — dernier dtCr déjà traité")
    args = parser.parse_args()

    last_dtcr = datetime.strptime(args.last_dtcr, "%Y-%m-%d") if args.last_dtcr else None

    seed_initial_state(args.api_id, last_dtcr)
    print(f"État initial créé pour {args.api_id} (last_dtcr_processed={last_dtcr})")


if __name__ == "__main__":
    main()
