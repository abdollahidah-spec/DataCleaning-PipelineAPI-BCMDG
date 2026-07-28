"""
shared/na_rule.py
===================
Règle NA/OUTLIER commune à tous les champs catégoriels (portée telle quelle
depuis DataCleaning-PipelineField-BCMDG/shared/base_pipeline.py::apply_na_rule).
"""
from __future__ import annotations

import pandas as pd


def apply_na_rule(
    row:       "pd.Series",
    field_col: str,
    ref_col:   str,
    iso_col:   str,
    mth_col:   str,
) -> tuple:
    """
    Logique :
      field == 'NA'  ET  ref == 'NA'   -> ('NA', 'NA')
      field == 'NA'  ET  ref != 'NA'   -> ('OUTLIER', 'OUTLIER')
      field vide / null / NaN          -> ('OUTLIER', 'OUTLIER')
      valeur non identifiée (OUTLIER)  -> ('OUTLIER', 'OUTLIER')
      sinon                            -> (current_iso, current_mth) inchangé
    """
    field_upper = str(row.get(field_col, "")).strip().upper()
    ref_upper   = str(row.get(ref_col,   "")).strip().upper()
    current_iso = row[iso_col]
    current_mth = row[mth_col]

    if field_upper == "NA":
        return ("NA", "NA") if ref_upper == "NA" else ("OUTLIER", "OUTLIER")

    if field_upper in ("", "NAN", "NONE", "NULL"):
        return "OUTLIER", "OUTLIER"

    if current_iso == "OUTLIER":
        return "OUTLIER", "OUTLIER"

    return current_iso, current_mth
