"""
shared/field_processor.py
===========================
Abstraction générique "champ" utilisée par l'orchestrateur (base_api_pipeline.py) :
chaque champ d'une API (catégoriel ou numérique) implémente FieldProcessor et
retourne un FieldResult uniforme, quel que soit son type de traitement.

Cette abstraction est volontairement générique et field-agnostic — la logique
métier de chaque champ (Devise, NomCorrespondant, cohérence numérique...) vit
dans le package de l'API qui l'utilise (ex: e11_rdcc/fields/), PAS ici.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd


@dataclass
class FieldResult:
    df: pd.DataFrame                               # colonnes ajoutées par ce champ (sur le snapshot complet)
    classification_df: Optional[pd.DataFrame]      # raw -> normalisé, OUTLIER inclus ; None pour le numérique
    outliers_df: pd.DataFrame                       # stats outliers (catégoriel) ou anomalies (numérique)
    exclude_from_export: list                       # colonnes intermédiaires à exclure de l'extraction CSV
    stats: dict                                      # alimente le Rapport_Qualite
    sheet_names: dict                                # {"classification": "...", "outliers": "..."}


class FieldProcessor(ABC):
    field_name: str

    @abstractmethod
    def process(self, df: pd.DataFrame, api_id: str) -> FieldResult:
        """df = snapshot brut complet (toutes les colonnes source) — aucun processor
        ne dépend de la colonne de sortie d'un autre, ce qui permet de les exécuter
        en parallèle (voir BaseApiPipeline.process_fields)."""
        raise NotImplementedError

    def instructions_rows(self, outliers_df: pd.DataFrame) -> pd.DataFrame:
        """Pré-remplissage de la feuille Instructions (Champ/Input vides par défaut)."""
        return pd.DataFrame(columns=["Champ", "Input", "Label_Attendu"])

    def apply_correction(self, api_id: str, corrections: dict) -> None:
        """Applique des corrections manuelles au cache warm-start de ce champ."""
        raise NotImplementedError(f"{self.field_name} n'a pas de cache warm-start")

    def sheet_columns(self) -> Optional[list]:
        """
        Projection optionnelle des colonnes affichées dans la feuille Excel de ce
        champ (ex: NumericCoherenceProcessor n'expose qu'un sous-ensemble lean de
        son outliers_df interne). None (défaut) = pas de projection, colonnes
        telles quelles.
        """
        return None


class CategoricalFieldProcessor(FieldProcessor):
    """
    Champ catégoriel (Devise, NomCorrespondant, ...) : normalise une colonne
    vers un référentiel fini, produit une table de classification (BI) et des
    stats d'outliers par RefBanque.
    """

    def __init__(
        self,
        field_name: str,
        treating_fn: Callable[..., pd.DataFrame],
        treating_kwargs: dict,
        col_in: str,
        col_out: str,
        ref_banque_col: str,
        outlier_tag: str,
        exclude_suffixes: tuple,
        clean_fn: Callable[[str], str],
        save_warm_start_fn: Optional[Callable[[str, dict, bool], None]] = None,
        classification_fn: Optional[Callable[[str], pd.DataFrame]] = None,
    ):
        self.field_name = field_name
        self.treating_fn = treating_fn
        self.treating_kwargs = treating_kwargs
        self.col_in = col_in
        self.col_out = col_out
        self.ref_banque_col = ref_banque_col
        self.outlier_tag = outlier_tag
        self.exclude_suffixes = exclude_suffixes
        self.clean_fn = clean_fn
        self.save_warm_start_fn = save_warm_start_fn
        # classification_fn(api_id) -> table CUMULATIVE (référentiel + cache warm-start),
        # indépendante des lignes du run en cours — indispensable en mode incrémental :
        # un label vu la semaine dernière mais absent du delta de cette semaine ne doit
        # JAMAIS disparaître du classeur BI (chemin stable, écrasé à chaque run).
        # Sans ça, on ne verrait dans la classification que les labels du run courant.
        self.classification_fn = classification_fn

    def process(self, df: pd.DataFrame, api_id: str) -> FieldResult:
        from shared.build_tables import build_classification_table, build_tables

        kwargs = dict(self.treating_kwargs)
        if "api_id" in kwargs:
            kwargs["api_id"] = api_id
        out = self.treating_fn(df, **kwargs)

        _, outliers_df = build_tables(
            out, col_in=self.col_in, col_out=self.col_out,
            ref_banque_col=self.ref_banque_col, outlier_tag=self.outlier_tag,
        )
        if self.classification_fn is not None:
            classification_df = self.classification_fn(api_id)
        else:
            classification_df = build_classification_table(out, self.col_in, self.col_out)

        exclude = [f"{self.col_in}{s}" for s in self.exclude_suffixes] + ["_ws_hit"]

        return FieldResult(
            df=out,
            classification_df=classification_df,
            outliers_df=outliers_df,
            exclude_from_export=exclude,
            stats=_categorical_stats(out, self.col_in, self.col_out, self.outlier_tag),
            sheet_names={"classification": self.field_name, "outliers": f"Outliers_{self.field_name}"},
        )

    def instructions_rows(self, outliers_df: pd.DataFrame) -> pd.DataFrame:
        if outliers_df.empty or self.col_in not in outliers_df.columns:
            return pd.DataFrame(columns=["Champ", "Input", "Label_Attendu"])
        vals = sorted(outliers_df[self.col_in].dropna().unique().tolist(), key=str)
        return pd.DataFrame({"Champ": self.field_name, "Input": vals, "Label_Attendu": ""})

    def apply_correction(self, api_id: str, corrections: dict) -> None:
        if self.save_warm_start_fn is None:
            raise NotImplementedError(f"{self.field_name} n'a pas de save_warm_start_fn configurée")
        self.save_warm_start_fn(api_id, corrections, False)


def _categorical_stats(df: pd.DataFrame, col_in: str, col_out: str, outlier_tag: str) -> dict:
    distinct_total = int(df[col_in].dropna().nunique())
    distinct_norm = int(df.loc[df[col_out] != outlier_tag, col_in].dropna().nunique())
    n_out = int((df[col_out] == outlier_tag).sum())
    return {
        "n_rows": len(df),
        "n_distinct_total": distinct_total,
        "n_distinct_normalized": distinct_norm,
        "n_outlier_rows": n_out,
        "taux_normalisation_pct": round(100 * distinct_norm / max(distinct_total, 1), 2),
        "taux_outliers_pct": round(100 * n_out / max(len(df), 1), 2),
    }
