"""
shared/na_rule.py
===================
Règle NA/OUTLIER commune à tous les champs catégoriels (portée telle quelle
depuis DataCleaning-PipelineField-BCMDG/shared/base_pipeline.py::apply_na_rule).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_EMPTY_TOKENS = ("", "NAN", "NONE", "NULL")


def apply_na_rule_frame(
    df:        "pd.DataFrame",
    field_col: str,
    ref_col:   str,
    iso_col:   str,
    mth_col:   str,
) -> tuple:
    """
    Version VECTORISÉE de `apply_na_rule` — même logique, appliquée d'un bloc sur
    tout le DataFrame au lieu d'un appel Python par ligne.

    Motivation : chaque champ catégoriel des 3 APIs appelait la règle via
    `df.apply(..., axis=1)`, soit une fonction Python exécutée ligne par ligne sur
    l'intégralité du jeu de données — de loin le poste le plus coûteux du
    traitement dès que la table dépasse quelques dizaines de milliers de lignes.

    L'équivalence stricte avec la version ligne à ligne est vérifiée par test
    (tests/test_na_rule.py, comparaison sur jeu aléatoire couvrant tous les cas).

    Retourne (iso: Series, method: Series), alignées sur l'index de `df`.
    """
    field_upper = df[field_col].astype(str).str.strip().str.upper()
    ref_upper = df[ref_col].astype(str).str.strip().str.upper()
    current_iso = df[iso_col]
    current_mth = df[mth_col]

    is_na_field = field_upper == "NA"
    is_na_ref = ref_upper == "NA"
    is_empty = field_upper.isin(_EMPTY_TOKENS)
    is_outlier = current_iso == "OUTLIER"

    # `current_mth or "OUTLIER"` de la version ligne à ligne : astype(bool)
    # reproduit exactement la véracité Python (None/"" -> False, NaN -> True).
    mth_if_outlier = current_mth.where(current_mth.astype(bool), "OUTLIER")

    # Ordre = enchaînement des if/elif d'origine (np.select retient le 1er vrai).
    conditions = [is_na_field & is_na_ref, is_na_field, is_empty, is_outlier]
    iso = np.select(conditions, ["NA", "OUTLIER", "OUTLIER", "OUTLIER"], default=current_iso)
    mth = np.select(conditions, ["NA", "OUTLIER", "OUTLIER", mth_if_outlier], default=current_mth)

    return pd.Series(iso, index=df.index), pd.Series(mth, index=df.index)


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
      valeur non identifiée (OUTLIER)  -> ('OUTLIER', current_mth) — méthode PRÉSERVÉE
      sinon                            -> (current_iso, current_mth) inchangé

    Correctif (bug latent porté depuis l'ancien repo) : le cas "déjà OUTLIER"
    préserve désormais `current_mth` au lieu de l'écraser par 'OUTLIER' générique
    — sinon la distinction "bruit déjà CONNU du référentiel" (method='NOISE',
    voir e11_rdcc/fields/devise.py, e08_ocd/fields/produits.py) vs "vraiment
    nouveau/non résolu" (method='CLAUDE'/'OUTLIER') disparaît silencieusement dès
    que cette règle NA s'applique — ce qui est le cas sur QUASIMENT toutes les
    lignes en production (ref_col est presque toujours présent), rendant cette
    distinction inopérante dans le rapport de qualité/les logs malgré son usage
    explicite ailleurs dans le code (shared/field_processor.py::_categorical_stats).
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
        return "OUTLIER", (current_mth or "OUTLIER")

    return current_iso, current_mth
