"""
e07_fs/fields/transactions.py
===============================
Moteur de validation des champs numériques/date d'E07_FS (Flux Sortants) —
MontantTransaction, TauxDeChange, DateTransaction — et de la conformité des
messages « sans activité ». Pas de référentiel à mapper (un montant n'est pas une
catégorie) : uniquement un rapport d'anomalies, comme E09 (échéances) et E11.

4 règles (ticket E07 FS) :
  1. AMOUNT_NON_NEGATIVE    — MontantTransaction numérique et >= 0
  2. RATE_NON_NEGATIVE      — TauxDeChange numérique et >= 0
  3. DATE_VALIDITY          — DateTransaction parsable ET antérieure à dtCr.
                              Ticket : « dateTransaction < dtCr ». Comparaison AU JOUR,
                              heure ignorée (même convention qu'E09/E11). Stricte par
                              défaut ; `date_inclusive: true` dans le YAML autorise le
                              même jour que dtCr.
  4. NO_ACTIVITY_CONFORMITY — un message « sans activité » doit respecter le gabarit
                              du ticket : champs texte à 'NA', Pays à 'NoAs',
                              montant et taux à 0.

Un message est « sans activité » SI ET SEULEMENT SI ReferenceTransaction vaut 'NA' :
une vraie transaction porte toujours une référence. C'est aussi le témoin de la
règle NA des champs catégoriels d'E07 (shared/na_rule.py).

Les colonnes du gabarit sont configurables (`no_activity.template_na_columns`) :
une colonne absente du jeu de données est simplement ignorée.

Une ligne en échec sur plusieurs règles produit plusieurs lignes d'anomalies —
jamais masquer une anomalie derrière une autre. Construction entièrement
vectorisée (aucun parcours ligne à ligne, voir l'historique E09/E11).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from shared.field_processor import FieldProcessor, FieldResult

_EPSILON = 1e-9   # bruit flottant uniquement — le gabarit exige un 0 exact

_ANOMALY_COLUMNS = [
    "ReferenceTransaction", "RefBanque", "DateTransaction", "dtCr",
    "MontantTransaction", "TauxDeChange", "Rule", "Detail", "Severity",
]
SHEET_COLUMNS = _ANOMALY_COLUMNS


@dataclass(frozen=True)
class TransactionsConfig:
    montant: str
    taux: str
    date_transaction: str
    dt_cr: str
    ref_banque: str
    reference_transaction: str
    pays: str
    gabarit_na: tuple = ()
    pays_sans_activite: str = "NoAs"
    date_inclusive: bool = False
    outlier_tag: str = "OUTLIER"


# ══════════════════════════════════════════════════════════════════════════════
# Parsing vectorisé (colonne entière) — chemin rapide si SQL Server a déjà typé
# ══════════════════════════════════════════════════════════════════════════════

def _to_float_series(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    cleaned = s.astype(str).str.strip()
    cleaned = cleaned.mask(cleaned.str.lower().isin(["nan", "none", "null", ""]))
    cleaned = cleaned.str.replace(",", ".", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


def _to_date_series(s: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    as_str = s.astype(str).str.strip()
    as_str = as_str.mask(as_str.str.lower().isin(["nan", "none", "null", "na", ""]))
    return pd.to_datetime(as_str, errors="coerce", format="mixed")


def _vaut(s: pd.Series, jeton: str) -> pd.Series:
    return s.astype(str).str.strip().str.upper().eq(jeton.upper())


def _col(df: pd.DataFrame, col: str, idx) -> pd.Series:
    """Colonne restreinte aux lignes voulues, ou chaîne vide si absente."""
    return df.loc[idx, col] if col in df.columns else pd.Series("", index=idx)


def _joindre(idx, paires) -> pd.Series:
    """Pour chaque ligne, libellés des conditions vraies, joints par ", "."""
    parts = pd.Series([""] * len(idx), index=idx, dtype=object)
    for libelle, masque in paires:
        m = masque.loc[idx]
        if m.any():
            parts[m] = parts[m].where(parts[m] == "", parts[m] + ", ") + libelle
    return parts


# ══════════════════════════════════════════════════════════════════════════════
# Construction des lignes d'anomalies (d'un bloc)
# ══════════════════════════════════════════════════════════════════════════════

def _frame(df: pd.DataFrame, cfg: TransactionsConfig, masque, regle: str, details) -> pd.DataFrame:
    idx = df.index[masque]
    return pd.DataFrame({
        "ReferenceTransaction": _col(df, cfg.reference_transaction, idx).to_numpy(),
        "RefBanque": _col(df, cfg.ref_banque, idx).to_numpy(),
        "DateTransaction": _col(df, cfg.date_transaction, idx).to_numpy(),
        "dtCr": _col(df, cfg.dt_cr, idx).to_numpy(),
        "MontantTransaction": _col(df, cfg.montant, idx).to_numpy(),
        "TauxDeChange": _col(df, cfg.taux, idx).to_numpy(),
        "Rule": regle,
        "Detail": np.asarray(details, dtype=object),
        "Severity": "ERROR",
    })


def _details_positif(df: pd.DataFrame, col: str, valeurs: pd.Series, masque) -> pd.Series:
    idx = df.index[masque]
    brut = _col(df, col, idx)
    val = valeurs[masque]
    manquant = val.isna()
    detail = pd.Series(index=idx, dtype=object)
    # Gardes .any() : sur une sélection vide, Series.map() renvoie du float64 et la
    # concaténation avec une chaîne échoue (même piège qu'E09).
    if manquant.any():
        detail[manquant] = f"{col} non numérique/manquant : " + brut[manquant].map(repr)
    if (~manquant).any():
        detail[~manquant] = f"{col}=" + val[~manquant].astype(str) + " < 0 (doit être positif ou nul)"
    return detail


def _details_date(df: pd.DataFrame, cfg: TransactionsConfig, d_tx, d_cr, masque) -> pd.Series:
    idx = df.index[masque]
    tx, cr = d_tx[masque], d_cr[masque]
    tx_ko = tx.isna()
    cr_ko = ~tx_ko & cr.isna()
    ordre = ~tx_ko & ~cr_ko
    detail = pd.Series(index=idx, dtype=object)
    if tx_ko.any():
        detail[tx_ko] = (f"{cfg.date_transaction} non parsable : "
                         + _col(df, cfg.date_transaction, idx)[tx_ko].map(repr))
    if cr_ko.any():
        detail[cr_ko] = f"{cfg.dt_cr} non parsable : " + _col(df, cfg.dt_cr, idx)[cr_ko].map(repr)
    if ordre.any():
        sens = "postérieure à" if cfg.date_inclusive else "non antérieure à"
        detail[ordre] = (f"{cfg.date_transaction}=" + tx[ordre].dt.date.astype(str)
                         + f" {sens} {cfg.dt_cr}=" + cr[ordre].dt.date.astype(str))
    return detail


# ══════════════════════════════════════════════════════════════════════════════
# Exécution des 4 règles
# ══════════════════════════════════════════════════════════════════════════════

def run_all_rules(df: pd.DataFrame, cfg: TransactionsConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retourne (df_annoté, anomalies_df)."""
    df = df.copy()

    montant = _to_float_series(df[cfg.montant])
    taux = _to_float_series(df[cfg.taux])
    montant_ko = montant.isna() | (montant < 0)
    taux_ko = taux.isna() | (taux < 0)

    d_tx = _to_date_series(df[cfg.date_transaction]).dt.normalize()
    d_cr = _to_date_series(df[cfg.dt_cr]).dt.normalize()
    hors_ordre = (d_tx > d_cr) if cfg.date_inclusive else (d_tx >= d_cr)
    date_ko = d_tx.isna() | d_cr.isna() | hors_ordre

    # Gabarit « sans activité » — uniquement sur les messages dont la référence vaut NA.
    sans_activite = _vaut(df[cfg.reference_transaction], "NA")
    ecarts = [(f"{c}!=NA", ~_vaut(df[c], "NA")) for c in cfg.gabarit_na if c in df.columns]
    if cfg.pays in df.columns:
        ecarts.append((f"{cfg.pays}!={cfg.pays_sans_activite}",
                       ~_vaut(df[cfg.pays], cfg.pays_sans_activite)))
    ecarts.append((f"{cfg.montant}!=0", montant.isna() | (montant.abs() > _EPSILON)))
    ecarts.append((f"{cfg.taux}!=0", taux.isna() | (taux.abs() > _EPSILON)))
    un_ecart = pd.Series(False, index=df.index)
    for _, m in ecarts:
        un_ecart = un_ecart | m
    gabarit_ko = sans_activite & un_ecart

    df["_TX_row_conforme"] = (~(montant_ko | taux_ko | date_ko | gabarit_ko)).to_numpy()

    frames = []
    if montant_ko.any():
        frames.append(_frame(df, cfg, montant_ko, "AMOUNT_NON_NEGATIVE",
                             _details_positif(df, cfg.montant, montant, montant_ko)))
    if taux_ko.any():
        frames.append(_frame(df, cfg, taux_ko, "RATE_NON_NEGATIVE",
                             _details_positif(df, cfg.taux, taux, taux_ko)))
    if date_ko.any():
        frames.append(_frame(df, cfg, date_ko, "DATE_VALIDITY",
                             _details_date(df, cfg, d_tx, d_cr, date_ko)))
    if gabarit_ko.any():
        idx = df.index[gabarit_ko]
        detail = (f"Message sans activité ({cfg.reference_transaction}=NA) non conforme au gabarit : "
                  + _joindre(idx, ecarts))
        frames.append(_frame(df, cfg, gabarit_ko, "NO_ACTIVITY_CONFORMITY", detail))

    anomalies = pd.concat(frames, ignore_index=True)[_ANOMALY_COLUMNS] if frames \
        else pd.DataFrame(columns=_ANOMALY_COLUMNS)
    return df, anomalies


# ══════════════════════════════════════════════════════════════════════════════
# FieldProcessor
# ══════════════════════════════════════════════════════════════════════════════

class TransactionsProcessor(FieldProcessor):
    def __init__(self, field_name: str, cfg: TransactionsConfig):
        self.field_name = field_name
        self.cfg = cfg

    def process(self, df: pd.DataFrame, api_id: str) -> FieldResult:
        annotated, anomalies = run_all_rules(df, self.cfg)
        n_rows = len(df)
        stats = {
            "n_rows": n_rows,
            "n_anomalies_error": int(len(anomalies)),
            "taux_conformite_pct": round(100 * annotated["_TX_row_conforme"].sum() / max(n_rows, 1), 2),
        }
        return FieldResult(
            df=annotated,
            classification_df=None,
            outliers_df=anomalies,
            exclude_from_export=["_TX_row_conforme"],
            stats=stats,
            sheet_names={"outliers": "Anomalies_Transactions"},
        )

    def sheet_columns(self) -> list:
        return SHEET_COLUMNS


def build_transactions_processor(field_cfg: dict) -> TransactionsProcessor:
    """Factory : construit le moteur de validation depuis le bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    sans_activite = field_cfg.get("no_activity") or {}
    cfg = TransactionsConfig(
        montant=cols["montant_transaction"],
        taux=cols["taux_de_change"],
        date_transaction=cols["date_transaction"],
        dt_cr=cols["dt_cr"],
        ref_banque=cols["ref_banque"],
        reference_transaction=cols["reference_transaction"],
        pays=cols["pays"],
        gabarit_na=tuple(sans_activite.get("template_na_columns") or ()),
        pays_sans_activite=sans_activite.get("pays_value", "NoAs"),
        date_inclusive=bool(field_cfg.get("date_inclusive", False)),
        outlier_tag=field_cfg.get("outlier_tag", "OUTLIER"),
    )
    return TransactionsProcessor(field_cfg["name"], cfg)
