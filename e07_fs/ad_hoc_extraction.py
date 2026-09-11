"""
e07_fs/ad_hoc_extraction.py
=============================
Extraction ad hoc E07_FS, INDÉPENDANTE du run automatisé — même outil que
e11_rdcc/ad_hoc_extraction.py : un analyste fournit sa propre requête SQL (ou un
nom de table, ou un fichier local), le pipeline applique le nettoyage normal à
tous les champs configurés dont les colonnes sont présentes dans le résultat (les
autres sont ignorés, pas d'erreur), et écrit UNE extraction locale : colonnes de
la requête + leurs versions nettoyées insérées juste après. Ne touche ni
SharePoint, ni email, ni l'état incrémental — un outil local, à lancer à la main.

Usage (depuis la racine du repo) :
    python -m e07_fs.ad_hoc_extraction --config e07_fs/config/E07_FS.yaml \\
        --query "SELECT TOP 100 * FROM [DATAWAREHOUSE_SA_PROD].[dbo].[E7EtatBcmFluxSortants]"

    python -m e07_fs.ad_hoc_extraction --config e07_fs/config/E07_FS.yaml \\
        --sql-file mon_extrait.sql --output mon_export.csv

    python -m e07_fs.ad_hoc_extraction --config e07_fs/config/E07_FS.yaml \\
        --input tests/fixtures/e07_fs_sample.csv
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from shared.config import load_config
from shared.console import force_utf8_console
from shared.env_loader import load_env_authoritative
from shared.field_processor import CategoricalFieldProcessor
from shared.writer import write_csv

from e07_fs.fields.transactions import TransactionsProcessor
from e07_fs.pipeline import E07Pipeline


def _processor_is_applicable(processor, columns) -> bool:
    """Un champ n'est appliqué que si TOUTES ses colonnes requises sont présentes
    dans le résultat de la requête ad hoc — sinon il est silencieusement ignoré.
    Pour la validation des transactions, les colonnes du gabarit « sans activité »
    restent facultatives (une colonne absente du gabarit est déjà ignorée)."""
    if isinstance(processor, CategoricalFieldProcessor):
        return processor.col_in in columns
    if isinstance(processor, TransactionsProcessor):
        cfg = processor.cfg
        required = [cfg.montant, cfg.taux, cfg.date_transaction, cfg.dt_cr,
                    cfg.ref_banque, cfg.reference_transaction, cfg.pays]
        return all(c in columns for c in required)
    return True


def run_ad_hoc_extraction(
    config_path: str,
    query: Optional[str] = None,
    table: Optional[str] = None,
    input_file: Optional[str] = None,
    output_path: Optional[str] = None,
) -> Path:
    cfg = load_config(config_path)
    pipeline = E07Pipeline(cfg, config_source=config_path)

    if input_file:
        from shared.db_connector import load_file
        df_raw = load_file(input_file, cfg)
        source_desc = f"fichier local {input_file}"
    elif query:
        from shared.db_connector import load_query
        df_raw = load_query(query)
        source_desc = "requête SQL personnalisée"
    else:
        from shared.db_connector import load_table
        table_name = table or cfg["input"]["table_name"]
        df_raw = load_table(table_name)
        source_desc = f"table {table_name}"

    print(f"[Ad hoc] Source : {source_desc} — {len(df_raw)} lignes, {len(df_raw.columns)} colonnes")

    applicable = [p for p in pipeline.field_processors if _processor_is_applicable(p, df_raw.columns)]
    skipped = [p.field_name for p in pipeline.field_processors if p not in applicable]
    if skipped:
        print(f"[Ad hoc] Champs ignorés (colonnes absentes du résultat) : {', '.join(skipped)}")
    if not applicable:
        print("[Ad hoc] Aucun champ configuré applicable à ce résultat — extraction brute, sans nettoyage.")

    pipeline.field_processors = applicable
    df_final, results = pipeline.process_fields(df_raw)
    extraction_df = pipeline.build_extraction_frame(results, df_final)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(output_path) if output_path else Path(f"e07_fs/outputs/ad_hoc_extraction_{ts}.csv")
    write_csv(extraction_df, out_path)
    print(f"[Ad hoc] Extraction écrite -> {out_path}")
    return out_path


def main() -> int:
    force_utf8_console()
    load_env_authoritative()
    parser = argparse.ArgumentParser(description="Extraction ad hoc E07_FS (requête SQL personnalisée, table, ou fichier local)")
    parser.add_argument("--config", default="e07_fs/config/E07_FS.yaml", help="Chemin du YAML E07_FS")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--query", default=None, help="Requête SQL SELECT personnalisée")
    source.add_argument("--sql-file", default=None, help="Fichier .sql contenant la requête")
    source.add_argument("--table", default=None, help="Nom de table (par défaut : celle du YAML), chargée en entier")
    source.add_argument("--input", default=None, help="Fichier local CSV/Excel")
    parser.add_argument("--output", default=None,
                         help="Chemin du CSV de sortie (défaut : e07_fs/outputs/ad_hoc_extraction_{ts}.csv)")
    args = parser.parse_args()

    query = args.query
    if args.sql_file:
        query = Path(args.sql_file).read_text(encoding="utf-8")

    try:
        run_ad_hoc_extraction(args.config, query=query, table=args.table,
                               input_file=args.input, output_path=args.output)
    except Exception as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
