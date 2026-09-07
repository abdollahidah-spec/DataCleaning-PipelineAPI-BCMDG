"""
shared/query_columns.py
=========================
Déduit, depuis le YAML d'une API, la liste des colonnes source réellement
nécessaires — pour remplacer `SELECT *` par une projection explicite.

Pourquoi : `SELECT *` rapatriait toutes les colonnes de la table, y compris
celles qu'aucun champ ne lit. Sur une table d'historique volumineuse, c'est
autant de conversion pyodbc -> objets Python et de mémoire consommées pour rien
(Initial Load E09 bloqué plusieurs heures, constaté en recette).

Sécurité de la déduction : si un traitement lit une colonne qui n'est déclarée
dans aucun bloc `columns`, elle serait absente du DataFrame. Deux garde-fous :
  - `input.extra_columns` (YAML) permet d'ajouter explicitement toute colonne
    supplémentaire à rapatrier ;
  - `input.select_columns: false` désactive complètement la projection et
    revient au `SELECT *` d'origine.
"""
from __future__ import annotations


"""Clés de `columns` désignant une colonne PRODUITE par la pipeline, absente de
la table source : elles ne doivent jamais entrer dans le SELECT (sinon la requête
échoue sur "Invalid column name")."""
_OUTPUT_COLUMN_KEYS = {"field_out"}


def required_columns(cfg: dict) -> set[str]:
    """
    Colonnes SOURCE à rapatrier : celles déclarées dans les blocs `columns` des
    champs (hors colonnes de sortie, voir _OUTPUT_COLUMN_KEYS), plus la colonne
    de delta (`input.dt_cr_column`), plus les éventuelles `input.extra_columns`.
    Les valeurs vides/nulles sont ignorées (une colonne optionnelle non
    configurée, comme `nif_nni` sur E08, ne doit pas produire un nom de colonne
    vide dans la requête).
    """
    cols: set[str] = set()

    for field_cfg in cfg.get("fields", []) or []:
        for key, value in (field_cfg.get("columns") or {}).items():
            if key in _OUTPUT_COLUMN_KEYS:
                continue
            if isinstance(value, str) and value.strip():
                cols.add(value.strip())

    input_cfg = cfg.get("input", {}) or {}
    dt_cr = input_cfg.get("dt_cr_column")
    if isinstance(dt_cr, str) and dt_cr.strip():
        cols.add(dt_cr.strip())

    for extra in input_cfg.get("extra_columns", []) or []:
        if isinstance(extra, str) and extra.strip():
            cols.add(extra.strip())

    # Colonnes synthétiques calculées par la pipeline (ex: le témoin de
    # non-activité globale d'E11) : jamais présentes en base, à ne pas demander.
    return {c for c in cols if not c.startswith("_")}


def projection_for(cfg: dict) -> list[str] | None:
    """
    Liste ordonnée des colonnes à mettre dans le SELECT, ou None pour conserver
    `SELECT *` (projection explicitement désactivée, ou rien de déductible).
    """
    if (cfg.get("input", {}) or {}).get("select_columns") is False:
        return None
    cols = required_columns(cfg)
    return sorted(cols) if cols else None


def primary_field_columns(cfg: dict) -> dict[str, str]:
    """{nom du champ -> sa colonne source principale}. Ces colonnes-là sont
    INDISPENSABLES : leur absence fait échouer le traitement du champ (c'est le
    `KeyError: 'Produits'` rencontré en recette sur E08, survenu au bout de
    plusieurs minutes de traitement au lieu d'être signalé immédiatement)."""
    out: dict[str, str] = {}
    for field_cfg in cfg.get("fields", []) or []:
        col = (field_cfg.get("columns") or {}).get("field")
        name = field_cfg.get("name")
        if isinstance(col, str) and col.strip() and name:
            out[name] = col.strip()
    return out


def reconcile_with_table(cfg: dict, requested: list, available) -> tuple[list, list]:
    """
    Confronte les colonnes demandées au schéma RÉEL de la table.

    Retourne (colonnes_a_selectionner, colonnes_absentes) — les absentes sont
    retirées du SELECT pour ne pas faire échouer la requête (cas d'une colonne
    optionnelle comme `nif_nni`, déclarée mais pas présente en base).

    Lève DataSourceError si une colonne PRINCIPALE de champ manque : le
    traitement échouerait de toute façon plus loin, autant le dire tout de suite
    avec un message exploitable plutôt qu'un KeyError après plusieurs minutes.
    """
    from shared.errors import DataSourceError

    available_set = set(available)
    missing = [c for c in requested if c not in available_set]

    bloquantes = {champ: col for champ, col in primary_field_columns(cfg).items()
                  if col in missing}
    if bloquantes:
        detail = ", ".join(f"champ '{champ}' -> colonne '{col}'"
                            for champ, col in sorted(bloquantes.items()))
        raise DataSourceError(
            f"Colonne(s) introuvable(s) dans la table '{cfg['input']['table_name']}' : {detail}. "
            f"Corrige `columns.field` dans le YAML de l'API. "
            f"Colonnes réellement disponibles : {sorted(available_set)}."
        )

    return [c for c in requested if c in available_set], missing
