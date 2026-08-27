"""
shared/report_templates.py
=============================
Génération Markdown générique des 2 rapports PDF (Rapport_Qualite + Rapport_Outliers)
joints à l'email de chaque API — gabarit EXACT validé par le Business Analyst : ce
texte/cette structure sont identiques d'une API à l'autre (voir e11_rdcc/reports.py
et e09_pe/reports.py pour l'usage concret, avec conversion Markdown -> PDF assurée
par shared/pdf_report.py).

Seul ce qui varie réellement d'une API à l'autre reste dans le module `reports.py`
de chaque API (decision H) : le mapping Rule/champ catégoriel -> nom JSON affiché,
la liste ordonnée des lignes du tableau "par champ traité", et le nom de la colonne
identifiant utilisée comme "valeur source" pour les anomalies non-catégorielles
(NumCompte pour E11, NumCredoc pour E09...).
"""
from __future__ import annotations

from shared.field_processor import CategoricalFieldProcessor
from shared.quality_report import QualityReport, format_duration_mmss

DEFAULT_TOP_N_REFBANQUE_DETAIL = 20


def format_pct_fr(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


def format_int_fr(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def build_quality_report_markdown(report: QualityReport) -> str:
    """Rapport de qualité des traitements — statistiques + définitions, texte
    validé par le Business Analyst reproduit tel quel. 100% générique (aucune
    connaissance du schéma d'une API en particulier).

    Indicateurs calculés sur l'ENSEMBLE DE L'HISTORIQUE traité (champs
    `report.cumulative_*`, voir shared/base_api_pipeline.py::_attach_cumulative_stats),
    PAS uniquement sur le delta de cette exécution — celui-ci reste réservé au
    corps de l'email (shared/email_notifier.py). Seul "Temps total d'exécution"
    reste par construction propre à CETTE exécution (une somme des durées de
    chaque run n'aurait pas de sens métier).

    "Nombre/Taux de valeurs déjà propres à la source" : AJOUT au-delà du gabarit
    initialement validé (retour explicite : distinguer une valeur reçue déjà
    conforme au référentiel — 0 traitement — d'une valeur ayant nécessité un
    travail de la pipeline, même trivial). Les indicateurs déjà validés par le
    Business Analyst restent inchangés au-dessus.
    """
    n_distinct_total = report.cumulative_n_distinct_total
    n_distinct_normalized = report.cumulative_n_distinct_normalized
    n_distinct_outliers = n_distinct_total - n_distinct_normalized
    n_already_clean = report.cumulative_n_already_clean
    # 3 catégories mutuellement exclusives qui totalisent n_distinct_total :
    # déjà propre / nettoyée avec succès par la pipeline / outlier non résolue.
    n_cleaned = n_distinct_normalized - n_already_clean

    return f"""# Rapport de qualité des traitements — {report.api_id}

## Statistiques générales

**Nombre total de lignes traitées : {format_int_fr(report.cumulative_n_rows)}**

*Définition : nombre total d'enregistrements (lignes) pris en compte par la pipeline sur l'ensemble de l'historique disponible, tous champs confondus.*

**Nombre total de valeurs distinctes traitées : {format_int_fr(n_distinct_total)}**

*Définition : nombre total de valeurs uniques, après déduplication sur l'ensemble de l'historique, rencontrées dans les champs concernés avant toute opération de nettoyage ou de normalisation.*

**Nombre de valeurs distinctes normalisées : {format_int_fr(n_distinct_normalized)}**

*Définition : nombre total de valeurs uniques de l'ensemble de l'historique ayant été rattachées avec succès à une valeur normalisée du référentiel, selon une relation permettant de rattacher une valeur normalisée à N valeurs sources.*

**Nombre de valeurs non classifiées (outliers) : {format_int_fr(n_distinct_outliers)}**

*Définition : nombre total de valeurs uniques de l'ensemble de l'historique n'ayant pu être associées automatiquement à aucune valeur normalisée du référentiel et placées en attente de validation métier (outliers).*

**Nombre de valeurs déjà propres à la source : {format_int_fr(n_already_clean)}**

*Définition : nombre de valeurs uniques de l'ensemble de l'historique reçues du système source identiques à une valeur du référentiel — aucun traitement de nettoyage n'a été nécessaire de la part de la pipeline pour ces valeurs.*

**Nombre de valeurs nettoyées par la pipeline (traitement réussi) : {format_int_fr(n_cleaned)}**

*Définition : nombre de valeurs uniques de l'ensemble de l'historique ayant nécessité une opération de la pipeline (correction, alias, normalisation, résolution automatique...) et rattachées avec succès à une valeur du référentiel — exclut les valeurs déjà propres à la source et les outliers non résolus.*

## Indicateurs de performance

**Taux de données conformes : {format_pct_fr(report.cumulative_taux_conformite_pct)}**

*Définition : proportion des lignes de l'ensemble de l'historique traité dont les valeurs respectent l'ensemble des règles de validation définies (formule du solde de fin de journée, cohérence temporelle des soldes, format des dates, etc.), rapportée au nombre total de lignes de l'historique traité.*

**Taux de valeurs normalisées : {format_pct_fr(report.cumulative_taux_normalisation_pct)}**

*Définition : proportion des valeurs distinctes de l'ensemble de l'historique traité ayant été rattachées avec succès à une valeur normalisée, calculée comme suit : Nombre de valeurs distinctes normalisées / Nombre total de valeurs distinctes traitées × 100.*

**Taux de valeurs déjà propres à la source : {format_pct_fr(report.cumulative_taux_deja_propre_pct)}**

*Définition : proportion des valeurs distinctes de l'ensemble de l'historique reçues du système source sans qu'aucun traitement de nettoyage n'ait été nécessaire, calculée comme suit : Nombre de valeurs déjà propres à la source / Nombre total de valeurs distinctes traitées × 100.*

**Taux de valeurs nettoyées par la pipeline : {format_pct_fr(report.cumulative_taux_nettoyage_pct)}**

*Définition : proportion des valeurs distinctes de l'ensemble de l'historique ayant nécessité un traitement de la pipeline et rattachées avec succès à une valeur du référentiel, calculée comme suit : Nombre de valeurs nettoyées par la pipeline / Nombre total de valeurs distinctes traitées × 100.*

**Taux de valeurs non classifiées (outliers) : {format_pct_fr(report.cumulative_taux_outliers_distinct_pct)}**

*Définition : proportion des valeurs distinctes de l'ensemble de l'historique n'ayant pu être associées automatiquement à aucune valeur normalisée du référentiel, calculée comme suit : Nombre de valeurs non classifiées (outliers) / Nombre total de valeurs distinctes traitées × 100.*

**Temps total d'exécution : {format_duration_mmss(report.execution_time_seconds)}**

*Définition : durée totale écoulée entre le début et la fin de l'exécution complète de la pipeline, incluant l'ensemble des étapes (extraction, prétraitement, nettoyage, normalisation, classification, génération des livrables).*
"""


def collect_refbanque_detail(results: list, champ_labels: dict, numeric_id_col: str) -> list:
    """
    (RefBanque, Champ, Valeur source, Nb occurrences) :
      - catégoriel : valeur brute en OUTLIER par banque (shared/build_tables.py::build_tables) ;
      - non-catégoriel (numérique/date) : `numeric_id_col` (l'identifiant "métier" de la
        ligne — NumCompte pour E11, NumCredoc pour E09) associé aux lignes en anomalie
        ERROR, par banque et par règle.
    `champ_labels` : {clé interne (field_name catégoriel OU Rule non-catégorielle) -> nom
    JSON affiché dans le rapport}, propre à chaque API (voir e11_rdcc/reports.py, e09_pe/reports.py).
    """
    rows: list = []
    for p, r in results:
        if isinstance(p, CategoricalFieldProcessor):
            df = r.outliers_df
            if df.empty or p.ref_banque_col not in df.columns or "Nombre_OUTLIERS" not in df.columns:
                continue
            champ = champ_labels.get(p.field_name, p.field_name)
            for _, row in df.iterrows():
                rows.append((str(row[p.ref_banque_col]), champ, str(row[p.col_in]), int(row["Nombre_OUTLIERS"])))
        else:
            df = r.outliers_df
            if df.empty or "Rule" not in df.columns:
                continue
            err = df[df.get("Severity", "ERROR") == "ERROR"]
            if err.empty or "RefBanque" not in err.columns or numeric_id_col not in err.columns:
                continue
            grouped = err.groupby(["RefBanque", "Rule", numeric_id_col]).size().reset_index(name="n")
            for _, row in grouped.iterrows():
                champ = champ_labels.get(str(row["Rule"]), str(row["Rule"]))
                rows.append((str(row["RefBanque"]), champ, str(row[numeric_id_col]), int(row["n"])))
    return rows


def build_outliers_report_markdown(
    report: QualityReport,
    results: list,
    field_rows: list,
    champ_labels: dict,
    numeric_id_col: str,
    top_n: int = DEFAULT_TOP_N_REFBANQUE_DETAIL,
) -> str:
    """Rapport des outliers — répartition par champ traité + par RefBanque, gabarit
    validé par le Business Analyst reproduit tel quel. `field_rows` (liste ordonnée
    des libellés JSON à afficher) et `champ_labels` (mapping clé interne -> libellé
    JSON) sont fournis par le module reports.py de chaque API — seule partie qui
    varie réellement d'une API à l'autre (voir docstring module)."""
    total_outliers = sum(report.outliers_by_champ.values())

    rows_with_counts = []
    for label in field_rows:
        internal_keys = [k for k, v in champ_labels.items() if v == label]
        n = sum(report.outliers_by_champ.get(k, 0) for k in internal_keys)
        pct = round(100 * n / max(total_outliers, 1), 1) if total_outliers else 0.0
        rows_with_counts.append((label, n, pct))

    table_champ_lines = [f"| {label} | {format_int_fr(n)} | {format_pct_fr(pct)} |" for label, n, pct in rows_with_counts]
    table_champ_lines.append(f"| **Total** | **{format_int_fr(total_outliers)}** | **100,0 %** |")

    detail_rows = sorted(collect_refbanque_detail(results, champ_labels, numeric_id_col), key=lambda r: -r[3])
    note = ""
    if len(detail_rows) > top_n:
        note = (
            f"\n\n*{top_n} occurrences les plus fréquentes affichées ci-dessous "
            f"sur {len(detail_rows)} au total — voir le classeur Excel de classification joint pour "
            f"la liste complète et la validation métier détaillée.*"
        )
    shown = detail_rows[:top_n]
    table_banque_lines = [f"| {rb} | {champ} | {val} | {format_int_fr(n)} |" for rb, champ, val, n in shown]
    if not table_banque_lines:
        table_banque_lines = ["| — | — | — | 0 |"]

    return f"""# Rapport des outliers — {report.api_id}

Ce rapport recense les valeurs n'ayant pas pu être classifiées automatiquement par le moteur de normalisation. Une validation métier est requise pour compléter la valeur normalisée attendue.

## Répartition par champ traité

| Champ | Nb valeurs outliers | % du total outliers |
|---|---|---|
{chr(10).join(table_champ_lines)}

## Répartition par RefBanque{note}

| RefBanque | Champ concerné | Valeur source | Nb occurrences |
|---|---|---|---|
{chr(10).join(table_banque_lines)}
"""
