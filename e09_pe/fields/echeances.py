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

from shared.frame_utils import iter_rows_as_dicts
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

def _col_or_blank(df: pd.DataFrame, col: str, index) -> pd.Series:
    """Colonne restreinte aux lignes voulues, ou chaîne vide si elle est absente —
    équivalent vectorisé de `row.get(col, "")`."""
    if col in df.columns:
        return df.loc[index, col]
    return pd.Series("", index=index)


def _anomaly_frame(df: pd.DataFrame, cfg: EcheancesConfig, mask, rule: str,
                    details: pd.Series) -> pd.DataFrame:
    """Lignes d'anomalies d'une règle, construites d'un bloc (aucune boucle)."""
    idx = df.index[mask]
    return pd.DataFrame({
        "NumCredoc": _col_or_blank(df, cfg.num_credoc, idx).to_numpy(),
        "RefBanque": _col_or_blank(df, cfg.ref_banque, idx).to_numpy(),
        "MontantEcheance": _col_or_blank(df, cfg.montant_echeance, idx).to_numpy(),
        "DateEcheance": _col_or_blank(df, cfg.date_echeance, idx).to_numpy(),
        "dtCr": _col_or_blank(df, cfg.dt_cr, idx).to_numpy(),
        "Rule": rule,
        "Detail": details.to_numpy(),
        "Severity": "ERROR",
    })


def _amount_details(df: pd.DataFrame, cfg: EcheancesConfig, montant: pd.Series, mask) -> pd.Series:
    """Texte Detail de AMOUNT_POSITIVE — même formulation que
    check_amount_positive_row, produite pour toutes les lignes d'un coup."""
    idx = df.index[mask]
    brut = _col_or_blank(df, cfg.montant_echeance, idx)
    val = montant[mask]
    non_numerique = val.isna()

    # Gardes .any() indispensables : sur une sélection vide, `Series.map()` renvoie
    # une Series vide typée float64, et concaténer une chaîne à celle-ci lève une
    # UFuncTypeError (attrapé par tests/test_e09_echeances.py).
    detail = pd.Series(index=idx, dtype=object)
    if non_numerique.any():
        detail[non_numerique] = (
            f"{cfg.montant_echeance} non numérique/manquant : "
            + brut[non_numerique].map(repr)
        )
    if (~non_numerique).any():
        detail[~non_numerique] = (
            f"{cfg.montant_echeance}=" + val[~non_numerique].astype(str)
            + " <= 0 (doit être strictement positif)"
        )
    return detail


def _date_details(df: pd.DataFrame, cfg: EcheancesConfig, date_ech: pd.Series,
                   dt_cr: pd.Series, mask) -> pd.Series:
    """Texte Detail de DATE_VALIDITY — mêmes trois formulations que
    check_date_validity_row (échéance non parsable / dtCr non parsable / échéance
    non postérieure), produites de façon vectorisée."""
    idx = df.index[mask]
    ech, cr = date_ech[mask], dt_cr[mask]
    ech_ko = ech.isna()
    cr_ko = ~ech_ko & cr.isna()
    comparaison = ~ech_ko & ~cr_ko

    detail = pd.Series(index=idx, dtype=object)
    if ech_ko.any():
        detail[ech_ko] = (f"{cfg.date_echeance} non parsable : "
                           + _col_or_blank(df, cfg.date_echeance, idx)[ech_ko].map(repr))
    if cr_ko.any():
        detail[cr_ko] = (f"{cfg.dt_cr} non parsable : "
                          + _col_or_blank(df, cfg.dt_cr, idx)[cr_ko].map(repr))
    if comparaison.any():
        # .dt.date : même rendu que les objets `date` de la version scalaire
        # ("2026-01-15" et non "2026-01-15 00:00:00").
        detail[comparaison] = (
            f"{cfg.date_echeance}=" + ech[comparaison].dt.date.astype(str)
            + f" non postérieure à {cfg.dt_cr}=" + cr[comparaison].dt.date.astype(str)
            + " (une échéance prévisionnelle doit être future)"
        )
    return detail


def run_all_rules(df: pd.DataFrame, cfg: EcheancesConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Exécute les 2 règles. Retourne (df_annotated, anomalies_df). Une ligne en
    échec sur les deux règles produit deux lignes dans anomalies_df — jamais
    masquer une anomalie derrière une autre."""
    df = df.copy()

    montant = _to_float_series(df[cfg.montant_echeance])
    amount_anomaly = montant.isna() | (montant <= 0)

    # Comparaison au JOUR (jour/mois/année), heure ignorée des deux côtés :
    # `dtCr` porte un horodatage de chargement (ex: 14h23) alors que
    # `DateEcheance` est une date métier ; comparer les heures rendait le verdict
    # dépendant du moment d'insertion en base, sans aucun sens fonctionnel. C'est
    # aussi ce que faisait déjà la fonction scalaire de référence to_date_safe(),
    # qui renvoie des `date` — la décision vectorisée en diverge(ait) silencieusement.
    # La règle reste « strictement postérieure » : une échéance tombant LE MÊME
    # JOUR que dtCr est une anomalie.
    date_ech = _to_date_series(df[cfg.date_echeance]).dt.normalize()
    dt_cr = _to_date_series(df[cfg.dt_cr]).dt.normalize()
    date_missing = date_ech.isna() | dt_cr.isna()
    date_anomaly = date_missing | (date_ech <= dt_cr)

    conforme = ~(amount_anomaly | date_anomaly)
    df["_EC_row_conforme"] = conforme.to_numpy()

    # Le texte Detail est construit VECTORISÉ, en réutilisant `montant`/`date_ech`/
    # `dt_cr` déjà calculés ci-dessus. La version précédente re-parsait chaque
    # ligne anomale valeur par valeur via to_float_safe/to_date_safe — or
    # to_date_safe essaie jusqu'à 4 formats successifs, chaque échec levant une
    # exception. Sur un Initial Load où presque toutes les lignes sont anomales
    # (5,1 M lignes E09), cela représentait ~10 M de parsings scalaires, soit
    # plusieurs heures. Les fonctions ligne-à-ligne restent disponibles comme
    # référence unitaire (voir tests/test_e09_echeances.py).
    frames = []
    if amount_anomaly.any():
        frames.append(_anomaly_frame(df, cfg, amount_anomaly, "AMOUNT_POSITIVE",
                                      _amount_details(df, cfg, montant, amount_anomaly)))
    if date_anomaly.any():
        frames.append(_anomaly_frame(df, cfg, date_anomaly, "DATE_VALIDITY",
                                      _date_details(df, cfg, date_ech, dt_cr, date_anomaly)))

    anomalies_df = pd.concat(frames, ignore_index=True)[list(_ANOMALY_COLUMNS)] if frames \
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
