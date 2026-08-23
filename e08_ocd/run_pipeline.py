"""
e08_ocd/run_pipeline.py
==========================
CLI de la pipeline E08_OCD.

Usage (depuis la racine du repo) :
    python -m e08_ocd.run_pipeline --config e08_ocd/config/E08_OCD.yaml
    python -m e08_ocd.run_pipeline --config e08_ocd/config/E08_OCD.yaml --mode initial
    python -m e08_ocd.run_pipeline --config e08_ocd/config/E08_OCD.yaml --input tests/fixtures/e08_ocd_sample.csv
    python -m e08_ocd.run_pipeline --config e08_ocd/config/E08_OCD.yaml --dry-run
"""
from __future__ import annotations

import argparse
import sys

from shared.config import load_config
from shared.console import force_utf8_console

from e08_ocd.pipeline import E08Pipeline


def main() -> int:
    force_utf8_console()
    parser = argparse.ArgumentParser(description="Pipeline de nettoyage E08_OCD")
    parser.add_argument("--config", required=True, help="Chemin du YAML E08_OCD")
    parser.add_argument("--mode", choices=["auto", "initial", "incremental"], default="auto",
                         help="auto (défaut) : initial si aucun état en base, incremental sinon")
    parser.add_argument("--input", default=None,
                         help="Fichier local CSV/Excel — force le mode 'file' (100%% offline, "
                              "pas de SharePoint/email/état incrémental)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Traite normalement mais n'envoie ni SharePoint ni email (logge l'intention)")
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
        pipeline = E08Pipeline(cfg, config_source=args.config)
        result = pipeline.run(mode=args.mode, override_input=args.input, dry_run=args.dry_run)
    except Exception as exc:
        # Couvre aussi les erreurs de chargement/validation de config (YAML
        # invalide, clé manquante...) — avant, seul pipeline.run() était protégé.
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1

    print(f"\n[{result['api_id']}] {result['status']} — mode={result['mode']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
