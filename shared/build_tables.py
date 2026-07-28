"""
shared/build_tables.py
========================
Construction des tables de sortie communes à tous les champs catégoriels :
  - build_tables()              -> table de stats outliers (par RefBanque + champ)
  - build_classification_table()-> table de classification complète (BI), OUTLIER inclus
"""
from __future__ import annotations

import pandas as pd


def build_tables(
    df: pd.DataFrame,
    col_in: str,
    col_out: str,
    ref_banque_col: str = "RefBanque",
    outlier_tag: str = "OUTLIER",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Retourne (df_clean, df_analysis) :
      df_clean    : mapping col_in -> col_out, OUTLIER exclus (usage interne/diagnostic)
      df_analysis : stats sur les OUTLIER uniquement, par RefBanque + col_in si
                    ref_banque_col est présent dans df, sinon stats globales.
    """
    df_clean = (
        df[df[col_out] != outlier_tag][[col_in, col_out]]
        .drop_duplicates()
        .sort_values([col_out, col_in])
        .reset_index(drop=True)
    )

    outliers = df[df[col_out] == outlier_tag]

    if ref_banque_col in df.columns:
        totals = (
            df.groupby(ref_banque_col)
            .agg(
                Nombre_total_OUTLIERS=(col_out, lambda s: int((s == outlier_tag).sum())),
                Nombre_total_CLEAN_VALUES=(col_out, lambda s: int((s != outlier_tag).sum())),
                Nombre_total_LIGNES=(col_out, "size"),
            )
            .reset_index()
        )
        counts = (
            outliers.groupby([ref_banque_col, col_in])
            .size()
            .reset_index(name="Nombre_OUTLIERS")
        )
        df_analysis = counts.merge(totals, on=ref_banque_col, how="left")
        denom = df_analysis["Nombre_total_LIGNES"].replace(0, 1)
        df_analysis["Ratio_OUTLIERS"] = (100 * df_analysis["Nombre_OUTLIERS"] / denom).round(2)
        df_analysis = df_analysis[
            [ref_banque_col, col_in, "Nombre_OUTLIERS", "Ratio_OUTLIERS",
             "Nombre_total_OUTLIERS", "Nombre_total_CLEAN_VALUES", "Nombre_total_LIGNES"]
        ].sort_values([ref_banque_col, col_in]).reset_index(drop=True)
    else:
        n_total = len(df)
        n_out_total = int((df[col_out] == outlier_tag).sum())
        n_clean_total = n_total - n_out_total
        df_analysis = (
            outliers.groupby(col_in).size().reset_index(name="Nombre_OUTLIERS")
        )
        df_analysis["Nombre_total_OUTLIERS"] = n_out_total
        df_analysis["Nombre_total_CLEAN_VALUES"] = n_clean_total
        df_analysis["Nombre_total_LIGNES"] = n_total
        denom = max(n_total, 1)
        df_analysis["Ratio_OUTLIERS"] = (100 * df_analysis["Nombre_OUTLIERS"] / denom).round(2)
        df_analysis = df_analysis[
            [col_in, "Nombre_OUTLIERS", "Ratio_OUTLIERS",
             "Nombre_total_OUTLIERS", "Nombre_total_CLEAN_VALUES", "Nombre_total_LIGNES"]
        ].sort_values(col_in).reset_index(drop=True)

    return df_clean, df_analysis


def build_classification_table(df: pd.DataFrame, col_in: str, col_out: str) -> pd.DataFrame:
    """
    Table de classification BI : chaque valeur brute distincte -> sa valeur
    normalisée, OUTLIER INCLUS (contrairement à build_tables()'s df_clean, qui
    les exclut) — c'est ce tableau complet qui est exposé à Power BI.
    """
    return (
        df[[col_in, col_out]]
        .dropna(subset=[col_in])
        .drop_duplicates()
        .sort_values([col_out, col_in])
        .reset_index(drop=True)
    )
