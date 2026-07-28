"""
shared/writer.py
==================
Écriture des fichiers de sortie (CSV, Excel multi-feuilles).
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

EXCEL_MAX_ROWS = 1_048_576
INSTRUCTIONS_COLS = ["Champ", "Input", "Label_Attendu"]


def empty_instructions_df() -> pd.DataFrame:
    return pd.DataFrame(columns=INSTRUCTIONS_COLS)


def write_csv(df: pd.DataFrame, path: str | Path) -> Path:
    p = Path(path).with_suffix(".csv")
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8-sig", sep=";")
    return p


def write_excel_sheets(frames: dict[str, pd.DataFrame], path: str | Path) -> Path:
    """
    Écrit un classeur Excel multi-feuilles — un onglet par entrée de `frames`
    (dict {nom_onglet: DataFrame}, ordre préservé), découpé en plusieurs onglets
    `{nom}_Part_{i}` si > EXCEL_MAX_ROWS-1 lignes.

    Ne gère plus la feuille "Instructions" automatiquement (contrairement à
    l'ancien repo) — le caller l'inclut explicitement dans `frames` quand il
    en veut une, car toutes les sorties n'en ont pas besoin (ex: le classeur
    de classification BI n'en a pas).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(str(p), engine="xlsxwriter") as writer:
        wb = writer.book
        hfmt = wb.add_format({
            "bold": True, "font_name": "Arial", "font_size": 10,
            "bg_color": "#FFD700", "align": "center", "valign": "vcenter", "border": 0,
        })

        for tab_base, df in frames.items():
            n = len(df)
            ns = max(1, math.ceil(n / (EXCEL_MAX_ROWS - 1))) if n else 1
            for i in range(ns):
                chunk = df.iloc[i * (EXCEL_MAX_ROWS - 1): (i + 1) * (EXCEL_MAX_ROWS - 1)]
                tab = f"{tab_base}_Part_{i + 1}" if ns > 1 else tab_base
                chunk.to_excel(writer, sheet_name=tab, index=False)
                ws = writer.sheets[tab]
                for col_idx, col_name in enumerate(chunk.columns):
                    ws.write(0, col_idx, col_name, hfmt)
                    width = min(len(str(col_name)) + 4, 60)
                    ws.set_column(col_idx, col_idx, width)
                ws.freeze_panes(1, 0)

    return p
