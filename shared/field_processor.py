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

    def apply_correction(self, api_id: str, corrections: dict) -> None:
        if self.save_warm_start_fn is None:
            raise NotImplementedError(f"{self.field_name} n'a pas de save_warm_start_fn configurée")
        self.save_warm_start_fn(api_id, corrections, False)


# Méthodes signifiant "déjà connu AVANT ce run" (référentiel statique ou cache
# warm-start déjà peuplé) — tout le reste (typiquement "CLAUDE"/"OUTLIER" seul)
# signifie une valeur que le pipeline n'avait jamais eu à trancher auparavant,
# résolue (ou non) pendant CE run — utilisé pour les indicateurs "nouvelles
# valeurs" de l'email (delta), voir shared/quality_report.py.
#
# NIF_EXACT/PUBLIC_ENT/DGI_EXACT_NORM/DGI_FUZZY_STRONG (e08_ocd/fields/
# nomdonneurordre.py, beneficiaire.py) : matches DÉTERMINISTES à confiance forte
# contre une source statique (base fiscale DGI, liste d'entreprises publiques) —
# même niveau de confiance que MAP, donc "déjà connu". PARTICULIER/ETS_PERSONNEL/
# ETS_OUTLIER en sont volontairement EXCLUS : ce sont des déductions heuristiques
# (mots-clés) sur une valeur jamais validée par un humain ni trouvée dans une
# source de référence — plus proche en esprit de CLAUDE (une estimation, pas une
# validation) que de MAP, donc classées "nouvelles" comme le reste.
_KNOWN_BEFORE_RUN_METHODS = {
    "WARM", "MAP", "NUM", "ALIAS", "STRIP", "NOISE",
    "NIF_EXACT", "PUBLIC_ENT", "DGI_EXACT_NORM", "DGI_FUZZY_STRONG",
}


def _categorical_stats(df: pd.DataFrame, col_in: str, col_out: str, outlier_tag: str) -> dict:
    distinct_total = int(df[col_in].dropna().nunique())
    distinct_norm = int(df.loc[df[col_out] != outlier_tag, col_in].dropna().nunique())
    n_out = int((df[col_out] == outlier_tag).sum())

    method_col = f"{col_in}_method"
    outliers_by_method: dict = {}
    distinct_outliers_by_method: dict = {}
    n_new_distinct = n_new_normalized = n_new_outliers = 0

    if method_col in df.columns:
        # Décompose les outliers par méthode de résolution — distingue le bruit DÉJÀ
        # CONNU du référentiel (method "NOISE" pour Devise, "MAP" pour un champ dont
        # le référentiel mappe directement une valeur vers OUTLIER) des valeurs
        # VRAIMENT nouvelles/non résolues. Un outlier "connu" n'est pas un problème
        # à corriger, juste du bruit filtré (voir scripts/analyze_outliers.py).
        if n_out:
            outlier_rows = df[df[col_out] == outlier_tag]
            outliers_by_method = {str(k): int(v) for k, v in outlier_rows[method_col].value_counts().items()}
            distinct_outliers_by_method = {
                str(m): int(sub[col_in].nunique()) for m, sub in outlier_rows.groupby(method_col)
            }

        # Indicateurs "nouvelles valeurs" (delta, pour l'email) : une valeur distincte
        # est "nouvelle" si sa méthode de résolution n'est PAS une des méthodes
        # "déjà connu avant ce run" (référentiel statique ou cache déjà peuplé).
        unique_vals = df.drop_duplicates(subset=[col_in])[[col_in, col_out, method_col]]
        is_new = ~unique_vals[method_col].isin(_KNOWN_BEFORE_RUN_METHODS)
        n_new_distinct = int(is_new.sum())
        n_new_normalized = int((is_new & (unique_vals[col_out] != outlier_tag)).sum())
        n_new_outliers = int((is_new & (unique_vals[col_out] == outlier_tag)).sum())

    return {
        "n_rows": len(df),
        "n_distinct_total": distinct_total,
        "n_distinct_normalized": distinct_norm,
        "n_outlier_rows": n_out,
        "taux_normalisation_pct": round(100 * distinct_norm / max(distinct_total, 1), 2),
        "taux_outliers_pct": round(100 * n_out / max(len(df), 1), 2),
        "outliers_by_method": outliers_by_method,                    # lignes, ex: {"NOISE": 1200, "OUTLIER": 340}
        "distinct_outliers_by_method": distinct_outliers_by_method,  # valeurs distinctes
        "n_new_distinct": n_new_distinct,        # nouvelles valeurs distinctes détectées ce run
        "n_new_normalized": n_new_normalized,    # ... dont normalisées avec succès (ex: résolues par Claude)
        "n_new_outliers": n_new_outliers,        # ... dont toujours en OUTLIER
    }


def cumulative_classification_stats(results: list) -> tuple[int, int]:
    """
    (n_distinct_total, n_distinct_normalized) cumulés — ENSEMBLE DE L'HISTORIQUE
    traité, tous champs catégoriels confondus — PAS les lignes du run en cours.

    Source : la table de classification de chaque champ (CategoricalFieldProcessor.
    classification_fn, référentiel + cache warm-start), déjà cumulative par
    construction et indépendante du run courant (voir son docstring). Contrairement
    à _categorical_stats() ci-dessus (qui reflète seulement le run courant, utilisé
    pour les compteurs "nouveau ce run" de l'email), cette fonction alimente les
    stats "globales" du rapport PDF (shared/report_templates.py, voir
    shared/base_api_pipeline.py::_attach_cumulative_stats).
    """
    total = 0
    normalized = 0
    for p, r in results:
        if not isinstance(p, CategoricalFieldProcessor) or r.classification_df is None:
            continue
        df = r.classification_df
        if df.empty:
            continue
        total += len(df)
        normalized += int((df[p.col_out] != p.outlier_tag).sum())
    return total, normalized


def cumulative_already_clean_stats(results: list) -> tuple[int, int]:
    """
    (n_distinct_total, n_already_clean) cumulés — ENSEMBLE DE L'HISTORIQUE traité,
    tous champs catégoriels confondus. Distingue une valeur reçue DÉJÀ propre
    (aucun traitement de notre part) d'une valeur ayant nécessité un traitement
    (normalisation réussie OU outlier non résolu) — retour métier explicite :
    "si la valeur brute existe telle quelle dans le référentiel, on n'a rien fait ;
    sinon, même un simple espace en trop retiré, c'est déjà un traitement".

    Test = comparaison LITTÉRALE (brut == normalisé) sur la table de classification
    cumulative — pas un nouveau tag de méthode par champ : n'importe quelle
    transformation (STRIP/ALIAS/NUM/CLAUDE/fuzzy...) change forcément la valeur
    normalisée par rapport à la valeur brute, donc échoue ce test et compte comme
    "traitement". Un OUTLIER compte aussi comme "traitement" (tentative échouée),
    jamais comme "déjà propre".
    """
    total = 0
    already_clean = 0
    for p, r in results:
        if not isinstance(p, CategoricalFieldProcessor) or r.classification_df is None:
            continue
        df = r.classification_df
        if df.empty:
            continue
        total += len(df)
        exact_match = (df[p.col_in] == df[p.col_out]) & (df[p.col_out] != p.outlier_tag)
        already_clean += int(exact_match.sum())
    return total, already_clean
