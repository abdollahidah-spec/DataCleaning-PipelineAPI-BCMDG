"""
e11_rdcc/run_pipeline.py
===========================
CLI de la pipeline E11_RDCC.

Usage (depuis la racine du repo) :
    python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml
    python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode initial
    python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --input tests/fixtures/e11_rdcc_sample.csv
    python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --dry-run
"""
from __future__ import annotations

import argparse
import sys

from shared.config import load_config
from shared.console import force_utf8_console

from e11_rdcc.pipeline import E11Pipeline


def main() -> int:
    force_utf8_console()
    parser = argparse.ArgumentParser(description="Pipeline de nettoyage E11_RDCC")
    parser.add_argument("--config", required=True, help="Chemin du YAML E11_RDCC")
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
        pipeline = E11Pipeline(cfg, config_source=args.config)
        result = pipeline.run(mode=args.mode, override_input=args.input, dry_run=args.dry_run)
    except Exception as exc:
        # Couvre aussi les erreurs de chargement/validation de config (YAML
        # invalide, clé manquante...) — avant, seul pipeline.run() était protégé,
        # donc une ConfigError levée à la construction du pipeline (avant même le
        # premier accès aux données) affichait une trace Python brute au lieu du
        # message clair attendu.
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1

    print(f"\n[{result['api_id']}] {result['status']} — mode={result['mode']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
