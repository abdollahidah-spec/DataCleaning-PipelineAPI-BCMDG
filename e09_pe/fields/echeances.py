"""
e09_pe/fields/echeances.py
=============================
Moteur de validation pour les 2 champs numérique/date de E09_PE (MontantEcheance,
DateEcheance) — pas de référentiel fini à mapper (un montant/une date n'est pas
une catégorie), donc pas de table de classification : uniquement un rapport
d'anomalies (même approche que e11_rdcc/fields/numeric_coherence.py, mais bien
plus simple : le ticket BCMDG-223 ne demande que 2 règles indépendantes, ligne à
ligne — pas de cohérence J/J+1 entre lignes, pas de clé de groupement, pas de
gabarit "sans activité").

2 règles (ticket BCMDG-223) :
  1. AMOUNT_POSITIVE — MontantEcheance > 0
  2. DATE_VALIDITY   — DateEcheance parsable ET strictement postérieure à dtCr
                        (le ticket écrit littéralement "dateEcheance > dtCr" —
                        contrairement à la règle 4 d'E11 (DateFinJournee <= dtCr),
                        ici la date d'échéance prévisionnelle doit logiquement se
                        situer APRÈS la date de création de la ligne, pas avant/le
                        jour même — pas de coquille à corriger ici, le sens métier
                        est cohérent tel quel)

NumCredoc (référence du Crédit Documentaire) n'est PAS normalisé — gardé comme
identifiant/témoin dans la feuille Anomalies_Echeances, exactement comme
NumCompte pour E11 (voir e09_pe/fields/devise.py pour son rôle de témoin NA).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import pandas as pd

from shared.field_processor import FieldProcessor, FieldResult

_ANOMALY_COLUMNS = [
    "NumCredoc", "RefBanque", "MontantEcheance", "DateEcheance", "dtCr",
    "Rule", "Detail", "Severity",
]

# Pas de projection distincte pour la feuille Excel — toutes les colonnes ci-dessus
# sont déjà la version "lean" demandée (identifiant + colonnes contrôlées + règle/détail).
SHEET_COLUMNS = _ANOMALY_COLUMNS


@dataclass(frozen=True)
class EcheancesConfig:
    montant_echeance: str
    date_echeance: str
    dt_cr: str
    ref_banque: str
    num_credoc: str
    outlier_tag: str = "OUTLIER"


# ══════════════════════════════════════════════════════════════════════════════
# Parsing — versions scalaires (une valeur) et vectorisées (colonne entière)
# ══════════════════════════════════════════════════════════════════════════════

def to_float_safe(x) -> Optional[float]:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def to_date_safe(x) -> Optional[date]:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    s = str(x).strip()
    if not s or s.lower() in ("nan", "none", "null", "na"):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, errors="raise").date()
    except Exception:
        return None


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


def _build_anomaly_row(row, cfg: EcheancesConfig, rule: str, detail: str, severity: str = "ERROR") -> dict:
    return {
        "NumCredoc": row.get(cfg.num_credoc, ""),
        "RefBanque": row.get(cfg.ref_banque, ""),
        "MontantEcheance": row.get(cfg.montant_echeance, ""),
        "DateEcheance": row.get(cfg.date_echeance, ""),
        "dtCr": row.get(cfg.dt_cr, ""),
        "Rule": rule,
        "Detail": detail,
        "Severity": severity,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Règles ligne-à-ligne (utilisées pour construire le texte Detail — uniquement sur
# le sous-ensemble des lignes déjà identifiées comme anomales, voir run_all_rules)
# ══════════════════════════════════════════════════════════════════════════════

def check_amount_positive_row(row, cfg: EcheancesConfig) -> dict:
    """Rule 1 : MontantEcheance > 0."""
    val = to_float_safe(row.get(cfg.montant_echeance))
    if val is None:
        return {"rule": "AMOUNT_POSITIVE", "is_anomaly": True,
                "detail": f"{cfg.montant_echeance} non numérique/manquant : {row.get(cfg.montant_echeance)!r}"}
    if val <= 0:
        return {"rule": "AMOUNT_POSITIVE", "is_anomaly": True,
                "detail": f"{cfg.montant_echeance}={val} <= 0 (doit être strictement positif)"}
    return {"rule": "AMOUNT_POSITIVE", "is_anomaly": False, "detail": ""}


def check_date_validity_row(row, cfg: EcheancesConfig) -> dict:
    """Rule 2 : DateEcheance parsable et strictement postérieure à dtCr."""
    date_ech = to_date_safe(row.get(cfg.date_echeance))
    dt_cr = to_date_safe(row.get(cfg.dt_cr))

    if date_ech is None:
        return {"rule": "DATE_VALIDITY", "is_anomaly": True,
                "detail": f"{cfg.date_echeance} non parsable : {row.get(cfg.date_echeance)!r}"}
    if dt_cr is None:
        return {"rule": "DATE_VALIDITY", "is_anomaly": True,
                "detail": f"{cfg.dt_cr} non parsable : {row.get(cfg.dt_cr)!r}"}
    if date_ech <= dt_cr:
        return {"rule": "DATE_VALIDITY", "is_anomaly": True,
                "detail": f"{cfg.date_echeance}={date_ech} non postérieure à {cfg.dt_cr}={dt_cr} "
                          f"(une échéance prévisionnelle doit être future)"}
    return {"rule": "DATE_VALIDITY", "is_anomaly": False, "detail": ""}


# ══════════════════════════════════════════════════════════════════════════════
# Exécution vectorisée (voir e11_rdcc/fields/numeric_coherence.py pour le même
# principe : décision vectorisée sur les colonnes entières, texte Detail construit
# uniquement sur le sous-ensemble anomal via .iterrows())
# ══════════════════════════════════════════════════════════════════════════════

def run_all_rules(df: pd.DataFrame, cfg: EcheancesConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Exécute les 2 règles. Retourne (df_annotated, anomalies_df). Une ligne en
    échec sur les deux règles produit deux lignes dans anomalies_df — jamais
    masquer une anomalie derrière une autre."""
    df = df.copy()

    montant = _to_float_series(df[cfg.montant_echeance])
    amount_anomaly = montant.isna() | (montant <= 0)

    date_ech = _to_date_series(df[cfg.date_echeance])
    dt_cr = _to_date_series(df[cfg.dt_cr])
    date_missing = date_ech.isna() | dt_cr.isna()
    date_anomaly = date_missing | (date_ech <= dt_cr)

    conforme = ~(amount_anomaly | date_anomaly)
    df["_EC_row_conforme"] = conforme.to_numpy()

    anomaly_rows = []
    if amount_anomaly.any():
        for _, row in df.loc[amount_anomaly].iterrows():
            r = check_amount_positive_row(row, cfg)
            anomaly_rows.append(_build_anomaly_row(row, cfg, r["rule"], r["detail"]))
    if date_anomaly.any():
        for _, row in df.loc[date_anomaly].iterrows():
            r = check_date_validity_row(row, cfg)
            anomaly_rows.append(_build_anomaly_row(row, cfg, r["rule"], r["detail"]))

    anomalies_df = pd.DataFrame(anomaly_rows, columns=_ANOMALY_COLUMNS) if anomaly_rows \
        else pd.DataFrame(columns=_ANOMALY_COLUMNS)
    return df, anomalies_df


# ══════════════════════════════════════════════════════════════════════════════
# FieldProcessor
# ══════════════════════════════════════════════════════════════════════════════

class EcheancesProcessor(FieldProcessor):
    def __init__(self, field_name: str, cfg: EcheancesConfig):
        self.field_name = field_name
        self.cfg = cfg

    def process(self, df: pd.DataFrame, api_id: str) -> FieldResult:
        annotated, anomalies_df = run_all_rules(df, self.cfg)
        n_rows = len(df)
        n_error = int((anomalies_df["Severity"] == "ERROR").sum()) if not anomalies_df.empty else 0
        stats = {
            "n_rows": n_rows,
            "n_anomalies_error": n_error,
            "taux_conformite_pct": round(100 * annotated["_EC_row_conforme"].sum() / max(n_rows, 1), 2),
        }
        return FieldResult(
            df=annotated,
            classification_df=None,
            outliers_df=anomalies_df,
            exclude_from_export=["_EC_row_conforme"],
            stats=stats,
            sheet_names={"outliers": "Anomalies_Echeances"},
        )

    def instructions_rows(self, outliers_df: pd.DataFrame) -> pd.DataFrame:
        # Pas de cache warm-start pour la validation numérique — rien à préremplir.
        return pd.DataFrame(columns=["Champ", "Input", "Label_Attendu"])

    def sheet_columns(self) -> list:
        return SHEET_COLUMNS


def build_echeances_processor(field_cfg: dict) -> EcheancesProcessor:
    """Factory : construit le FieldProcessor Echeances depuis le bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    cfg = EcheancesConfig(
        montant_echeance=cols["montant_echeance"],
        date_echeance=cols["date_echeance"],
        dt_cr=cols["dt_cr"],
        ref_banque=cols["ref_banque"],
        num_credoc=cols["num_credoc"],
        outlier_tag=field_cfg.get("outlier_tag", "OUTLIER"),
    )
    return EcheancesProcessor(field_cfg["name"], cfg)
