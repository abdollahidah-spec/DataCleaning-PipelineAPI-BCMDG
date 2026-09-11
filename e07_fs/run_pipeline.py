"""
e07_fs/run_pipeline.py
==========================
CLI de la pipeline E07_FS.

Usage (depuis la racine du repo) :
    python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml
    python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml --mode initial
    python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml --input tests/fixtures/e07_fs_sample.csv
    python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml --dry-run
"""
from __future__ import annotations

import argparse
import sys

from shared.config import load_config
from shared.console import force_utf8_console
from shared.env_loader import load_env_authoritative

from e07_fs.pipeline import E07Pipeline


def main() -> int:
    # .env fait foi, AVANT tout le reste (voir shared/env_loader.py).
    load_env_authoritative()
    force_utf8_console()
    parser = argparse.ArgumentParser(description="Pipeline de nettoyage E07_FS")
    parser.add_argument("--config", required=True, help="Chemin du YAML E07_FS")
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
        pipeline = E07Pipeline(cfg, config_source=args.config)
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
