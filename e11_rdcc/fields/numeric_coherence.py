"""
e11_rdcc/fields/numeric_coherence.py
=======================================
Moteur de cohérence pour les 5 champs numériques/date de E11_RDCC
(SoldeDebutJournee, TotalMvtsDebiteursJournee, TotalMvtsCrediteurs,
SoldeFinJournee, DateFinJournee) — pas de référentiel fini à mapper ici
(un solde n'est pas une catégorie), donc pas de table de classification :
uniquement un rapport d'anomalies (voir décision C du plan).

4 règles (ticket BCMDG-172) :
  1. ARITHMETIC             — SoldeFinJournee == SoldeDebutJournee + TotalMvtsCrediteurs - TotalMvtsDebiteursJournee
  2. TEMPORAL_CONTINUITY    — SoldeFinJournee(J) == SoldeDebutJournee(J+1), par compte (clé de groupement)
  3. NO_ACTIVITY_CONFORMITY — conformité du gabarit "pas d'activité" (NA sur nomCorrespondant/devise/numCompte
                               => les 4 champs numériques doivent être exactement 0)
  4. DATE_VALIDITY          — DateFinJournee parsable et <= dtCr (confirmé métier : coquille du
                               ticket "soldeFinJournee < dtCr" -> "dateFinJournee <= dtCr")

Une ligne source en violation de plusieurs règles produit PLUSIEURS lignes dans
Anomalies_Numeriques (jamais masquer une anomalie derrière une autre).

NumCompte (numéro de compte) est confirmé comme référence de transaction/compte pour
E11 — utilisé comme colonne "témoin" de la règle NA globale (voir e11_rdcc/global_na.py
et e11_rdcc/config/E11_RDCC.yaml), et conservé dans la feuille Anomalies_Numeriques.

La clé de groupement pour la continuité temporelle (règle 2) est CONFIRMÉE par le
Business Analyst : [RefBanque, NomCorrespondant, NumCompte, Devise] — filtrer d'abord
sur ces 4 critères ensemble avant de comparer SoldeFinJournee(J) à SoldeDebutJournee(J+1).

PERFORMANCE : la décision "cette ligne est-elle anomale ?" (rules 1/3/4) est
VECTORISÉE (opérations pandas/numpy sur des colonnes entières — un aller-retour
Python par colonne, pas par ligne). Seule la construction du texte `Detail` (pour
les lignes réellement anomales, un sous-ensemble généralement bien plus petit que
l'ensemble complet) réutilise les fonctions ligne-à-ligne `check_*_row` existantes,
via `.iterrows()` sur ce sous-ensemble filtré — jamais sur le DataFrame complet.
Sur 338k lignes réelles, ce changement fait passer le traitement de ~17 minutes à
quelques secondes/dizaines de secondes (mesure à confirmer en prod).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

from shared.field_processor import FieldProcessor, FieldResult

_NO_ACTIVITY_EPSILON = 1e-9  # bruit flottant uniquement — PAS la tolérance métier (structural check)

# NumCompte = référence de transaction/compte pour E11 (confirmé métier), remplace
# NomCorrespondant/Devise/ReferenceTransaction comme identifiant de ligne (redondants).
# Colonnes internes complètes (utilisées pour le calcul du rapport de qualité — Severity
# distingue ERROR/WARNING, Rule regroupe par type d'anomalie). La feuille Excel réellement
# écrite est une projection plus courte de ces colonnes (voir SHEET_COLUMNS ci-dessous et
# shared/base_api_pipeline.py::assemble_output_workbook) — demande métier : NumCompte +
# RefBanque + colonnes numériques/date uniquement, Rule/Detail gardés pour rester
# interprétable (sans eux, deux violations différentes sur une même ligne seraient
# des lignes visuellement identiques).
_ANOMALY_COLUMNS = [
    "NumCompte", "RefBanque",
    "SoldeDebutJournee", "TotalMvtsDebiteursJournee", "TotalMvtsCrediteurs",
    "SoldeFinJournee", "DateFinJournee", "dtCr",
    "Rule", "Detail", "Delta", "Severity",
]

# Projection utilisée pour la feuille Excel Anomalies_Numeriques (voir commentaire ci-dessus).
# dtCr ajouté sur demande du Business Analyst, pour faciliter la vérification manuelle
# de la règle 4 (DATE_VALIDITY : DateFinJournee <= dtCr) sans devoir recouper avec la base.
SHEET_COLUMNS = [
    "NumCompte", "RefBanque",
    "SoldeDebutJournee", "TotalMvtsDebiteursJournee", "TotalMvtsCrediteurs",
    "SoldeFinJournee", "DateFinJournee", "dtCr",
    "Rule", "Detail",
]


@dataclass(frozen=True)
class NumericCoherenceConfig:
    solde_debut: str
    mvts_debiteurs: str
    mvts_crediteurs: str
    solde_fin: str
    date_fin: str
    dt_cr: str
    ref_banque: str
    nom_correspondant: str
    devise: str
    num_compte: str
    grouping_key: list = field(
        default_factory=lambda: ["RefBanque", "NomCorrespondant", "NumCompte", "Devise"]
    )
    tolerance_abs: float = 0.01
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
    """Équivalent vectorisé de to_float_safe (une passe pandas/numpy sur toute la
    colonne, pas un appel Python par ligne) — tolère la virgule décimale (locale FR)."""
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    cleaned = s.astype(str).str.strip()
    cleaned = cleaned.mask(cleaned.str.lower().isin(["nan", "none", "null", ""]))
    cleaned = cleaned.str.replace(",", ".", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


def _to_date_series(s: pd.Series) -> pd.Series:
    """Équivalent vectorisé de to_date_safe. Les colonnes SQL Server réelles arrivent
    déjà en datetime natif (rapide) ; le parsing de chaînes ne sert qu'aux fichiers
    locaux CSV/Excel (tests, extractions ad hoc)."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    as_str = s.astype(str).str.strip()
    as_str = as_str.mask(as_str.str.lower().isin(["nan", "none", "null", "na", ""]))
    return pd.to_datetime(as_str, errors="coerce", format="mixed")


def _is_na_value(v) -> bool:
    return str(v).strip().upper() == "NA"


def _is_na_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper().eq("NA")


def _colonnes_lues(cfg: NumericCoherenceConfig) -> list:
    """Colonnes réellement consultées par les règles ligne-à-ligne et par
    _build_anomaly_row — limite le parcours à ces seules colonnes (voir
    shared/frame_utils.py::iter_rows_as_dicts)."""
    return [cfg.num_compte, cfg.ref_banque, cfg.date_fin, cfg.dt_cr, cfg.solde_debut,
            cfg.mvts_debiteurs, cfg.mvts_crediteurs, cfg.solde_fin,
            cfg.nom_correspondant, cfg.devise]


def _build_anomaly_row(row, cfg: NumericCoherenceConfig, rule: str, detail: str,
                        delta: Optional[float] = None, severity: str = "ERROR") -> dict:
    return {
        "NumCompte": row.get(cfg.num_compte, ""),
        "RefBanque": row.get(cfg.ref_banque, ""),
        "DateFinJournee": row.get(cfg.date_fin, ""),
        "dtCr": row.get(cfg.dt_cr, ""),
        "SoldeDebutJournee": row.get(cfg.solde_debut, ""),
        "TotalMvtsDebiteursJournee": row.get(cfg.mvts_debiteurs, ""),
        "TotalMvtsCrediteurs": row.get(cfg.mvts_crediteurs, ""),
        "SoldeFinJournee": row.get(cfg.solde_fin, ""),
        "Rule": rule,
        "Detail": detail,
        "Delta": delta,
        "Severity": severity,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Règles ligne-à-ligne (utilisées pour construire le texte Detail — uniquement sur
# le sous-ensemble des lignes déjà identifiées comme anomales, voir run_all_rules)
# ══════════════════════════════════════════════════════════════════════════════

def check_arithmetic_row(row, cfg: NumericCoherenceConfig) -> dict:
    """Rule 1 : SoldeFinJournee == SoldeDebutJournee + TotalMvtsCrediteurs - TotalMvtsDebiteursJournee."""
    debut = to_float_safe(row.get(cfg.solde_debut))
    deb   = to_float_safe(row.get(cfg.mvts_debiteurs))
    cred  = to_float_safe(row.get(cfg.mvts_crediteurs))
    fin   = to_float_safe(row.get(cfg.solde_fin))

    missing = [name for name, v in (
        (cfg.solde_debut, debut), (cfg.mvts_debiteurs, deb),
        (cfg.mvts_crediteurs, cred), (cfg.solde_fin, fin),
    ) if v is None]
    if missing:
        return {"rule": "ARITHMETIC", "is_anomaly": True, "delta": None,
                "detail": f"Champ(s) non numérique(s)/manquant(s) : {', '.join(missing)}"}

    computed = debut + cred - deb
    delta = round(fin - computed, 2)
    is_anomaly = abs(delta) > cfg.tolerance_abs
    detail = "" if not is_anomaly else (
        f"SoldeFinJournee={fin} != SoldeDebutJournee({debut}) + MvtsCrediteurs({cred}) "
        f"- MvtsDebiteurs({deb}) = {computed} (écart={delta})"
    )
    return {"rule": "ARITHMETIC", "is_anomaly": is_anomaly, "delta": delta, "detail": detail}


def check_no_activity_conformity_row(row, cfg: NumericCoherenceConfig) -> dict:
    """Rule 3 : gabarit 'pas d'activité' — voir exemple JSON du ticket."""
    na_fields = {cfg.nom_correspondant: row.get(cfg.nom_correspondant), cfg.devise: row.get(cfg.devise)}
    if cfg.num_compte:
        na_fields[cfg.num_compte] = row.get(cfg.num_compte)

    na_flags = {name: _is_na_value(v) for name, v in na_fields.items()}
    n_na, n_total = sum(na_flags.values()), len(na_flags)

    if n_na == 0:
        return {"rule": "NO_ACTIVITY_CONFORMITY", "is_anomaly": False, "is_no_activity_row": False, "detail": ""}

    if n_na < n_total:
        not_na = [n for n, is_na in na_flags.items() if not is_na]
        return {"rule": "NO_ACTIVITY_CONFORMITY", "is_anomaly": True, "is_no_activity_row": False,
                "detail": f"NA partielle : {', '.join(not_na)} non-NA alors que d'autres champs identifiants le sont"}

    numeric_cols = [cfg.solde_debut, cfg.mvts_debiteurs, cfg.mvts_crediteurs, cfg.solde_fin]
    nonzero = [c for c in numeric_cols
               if (v := to_float_safe(row.get(c))) is None or abs(v) > _NO_ACTIVITY_EPSILON]
    if nonzero:
        return {"rule": "NO_ACTIVITY_CONFORMITY", "is_anomaly": True, "is_no_activity_row": True,
                "detail": f"Ligne 'sans activité' (NA) mais champ(s) non nul(s)/non numérique(s) : {', '.join(nonzero)}"}
    return {"rule": "NO_ACTIVITY_CONFORMITY", "is_anomaly": False, "is_no_activity_row": True, "detail": ""}


def check_date_validity_row(row, cfg: NumericCoherenceConfig) -> dict:
    """Rule 4 : DateFinJournee parsable et <= dtCr."""
    date_fin = to_date_safe(row.get(cfg.date_fin))
    dt_cr = to_date_safe(row.get(cfg.dt_cr))

    if date_fin is None:
        return {"rule": "DATE_VALIDITY", "is_anomaly": True,
                "detail": f"{cfg.date_fin} non parsable : {row.get(cfg.date_fin)!r}"}
    if dt_cr is None:
        return {"rule": "DATE_VALIDITY", "is_anomaly": True,
                "detail": f"{cfg.dt_cr} non parsable : {row.get(cfg.dt_cr)!r}"}
    if date_fin > dt_cr:
        return {"rule": "DATE_VALIDITY", "is_anomaly": True,
                "detail": f"{cfg.date_fin}={date_fin} postérieure à {cfg.dt_cr}={dt_cr}"}
    return {"rule": "DATE_VALIDITY", "is_anomaly": False, "detail": ""}


# ══════════════════════════════════════════════════════════════════════════════
# Règle whole-dataframe (vectorisée : décision par groupby+shift, pas de boucle
# Python imbriquée par groupe)
# ══════════════════════════════════════════════════════════════════════════════

def check_temporal_continuity(df: pd.DataFrame, cfg: NumericCoherenceConfig,
                               no_activity_mask: pd.Series) -> pd.DataFrame:
    """
    Rule 2. Exclut les lignes 'sans activité' du chaînage (leurs champs identifiants
    sont tous NA — elles ne désignent aucun compte précis et corrompraient les
    séquences de comptes distincts si elles étaient chaînées ensemble).
    """
    working = df.loc[~no_activity_mask].copy()
    if working.empty:
        return pd.DataFrame(columns=_ANOMALY_COLUMNS)

    working["_parsed_date"] = _to_date_series(working[cfg.date_fin])
    working = working[working["_parsed_date"].notna()]

    group_cols = [c for c in cfg.grouping_key if c in working.columns]
    if not group_cols or working.empty:
        return pd.DataFrame(columns=_ANOMALY_COLUMNS)

    working["_solde_fin_num"] = _to_float_series(working[cfg.solde_fin])
    working["_solde_debut_num"] = _to_float_series(working[cfg.solde_debut])

    frames_dup = []

    # Doublons (même compte + même date) — détectés sur tout le working set en une passe.
    dup_report_mask = working.duplicated(subset=group_cols + ["_parsed_date"], keep=False)
    if dup_report_mask.any():
        # Une ligne d'anomalie par groupe de doublons, construite d'un bloc :
        # `transform("size")` donne la taille du groupe sur chaque ligne, et le
        # masque "première occurrence" sélectionne le représentant du groupe.
        # (Avant : une itération par groupe + une Series construite à chaque tour.)
        cle = group_cols + ["_parsed_date"]
        tailles = working.groupby(cle, dropna=False)["_parsed_date"].transform("size")
        premier = dup_report_mask & ~working.duplicated(subset=cle, keep="first")
        idx = working.index[premier]
        details = (tailles[premier].astype(str) + " lignes pour la même date "
                    + working.loc[premier, "_parsed_date"].astype(str) + " (compte ambigu)")
        frames_dup.append(_frame(working, cfg, idx, "TEMPORAL_CONTINUITY", details))

        # Ne garde que le "premier" de chaque doublon (comme l'ancien drop_duplicates
        # keep="first") pour que le chaînage J/J+1 continue avec cette occurrence.
        dup_drop_mask = working.duplicated(subset=cle, keep="first")
        working = working.loc[~dup_drop_mask].copy()

    working = working.sort_values(group_cols + ["_parsed_date"])
    grouped = working.groupby(group_cols, dropna=False, sort=False)
    prev_solde_fin = grouped["_solde_fin_num"].shift(1)
    prev_date = grouped["_parsed_date"].shift(1)

    valid_pair = prev_solde_fin.notna() & prev_date.notna() & working["_solde_debut_num"].notna()
    delta = (working["_solde_debut_num"] - prev_solde_fin).round(2)
    gap_days = (working["_parsed_date"] - prev_date).dt.days

    continuity_error = valid_pair & (delta.abs() > cfg.tolerance_abs)
    continuity_warning = valid_pair & ~continuity_error & (gap_days > 1)

    # Avant : une boucle par anomalie avec QUATRE recherches .loc[idx] distinctes
    # (ligne, solde précédent, date précédente, delta) plus la construction d'une
    # Series à chaque tour. Tout est désormais construit d'un bloc.
    if continuity_error.any():
        idx = working.index[continuity_error]
        details = ("SoldeDebutJournee=" + working.loc[idx, "_solde_debut_num"].astype(str)
                    + " != SoldeFinJournee(J-1)=" + prev_solde_fin[idx].astype(str)
                    + " (relevé précédent " + prev_date[idx].dt.date.astype(str) + ")")
        frames_dup.append(_frame(working, cfg, idx, "TEMPORAL_CONTINUITY", details,
                                  delta=delta[idx].to_numpy()))
    if continuity_warning.any():
        idx = working.index[continuity_warning]
        details = ("Écart de " + gap_days[idx].astype(int).astype(str)
                    + " jours depuis le relevé précédent ("
                    + prev_date[idx].dt.date.astype(str) + ")")
        frames_dup.append(_frame(working, cfg, idx, "TEMPORAL_CONTINUITY", details,
                                  delta=0.0, severity="WARNING"))

    return pd.concat(frames_dup, ignore_index=True)[list(_ANOMALY_COLUMNS)] if frames_dup \
        else pd.DataFrame(columns=_ANOMALY_COLUMNS)


def _col(df: pd.DataFrame, col: str, idx) -> pd.Series:
    """Colonne restreinte aux lignes voulues, ou chaîne vide si absente —
    équivalent vectorisé de `row.get(col, "")`."""
    return df.loc[idx, col] if col in df.columns else pd.Series("", index=idx)


def _frame(df: pd.DataFrame, cfg: NumericCoherenceConfig, idx, rule: str,
            details, delta=None, severity="ERROR") -> pd.DataFrame:
    """Lignes d'anomalies construites d'un bloc (mêmes colonnes que
    _build_anomaly_row, qui reste la référence ligne à ligne)."""
    return pd.DataFrame({
        "NumCompte": _col(df, cfg.num_compte, idx).to_numpy(),
        "RefBanque": _col(df, cfg.ref_banque, idx).to_numpy(),
        "DateFinJournee": _col(df, cfg.date_fin, idx).to_numpy(),
        "dtCr": _col(df, cfg.dt_cr, idx).to_numpy(),
        "SoldeDebutJournee": _col(df, cfg.solde_debut, idx).to_numpy(),
        "TotalMvtsDebiteursJournee": _col(df, cfg.mvts_debiteurs, idx).to_numpy(),
        "TotalMvtsCrediteurs": _col(df, cfg.mvts_crediteurs, idx).to_numpy(),
        "SoldeFinJournee": _col(df, cfg.solde_fin, idx).to_numpy(),
        "Rule": rule,
        "Detail": np.asarray(details, dtype=object),
        "Delta": delta if delta is not None else None,
        "Severity": severity,
    })


def _noms_manquants(idx, paires) -> pd.Series:
    """Pour chaque ligne, la liste des colonnes non numériques/manquantes, jointe
    par ", " — équivalent vectorisé de la compréhension de check_arithmetic_row
    (l'ordre des noms est préservé)."""
    parts = pd.Series([""] * len(idx), index=idx, dtype=object)
    for nom, serie in paires:
        manquant = serie.isna()
        if manquant.any():
            sep = parts[manquant].where(parts[manquant] == "", parts[manquant] + ", ")
            parts[manquant] = sep + nom
    return parts


def _frame_arithmetic(df, cfg, mask, missing_mask, debut, deb, cred, fin, computed, delta):
    idx = df.index[mask]
    manque = missing_mask[mask]

    detail = pd.Series(index=idx, dtype=object)
    if manque.any():
        noms = _noms_manquants(idx[manque], [
            (cfg.solde_debut, debut[mask][manque]), (cfg.mvts_debiteurs, deb[mask][manque]),
            (cfg.mvts_crediteurs, cred[mask][manque]), (cfg.solde_fin, fin[mask][manque]),
        ])
        detail[manque] = "Champ(s) non numérique(s)/manquant(s) : " + noms
    if (~manque).any():
        ok = idx[~manque]
        detail[~manque] = (
            "SoldeFinJournee=" + fin[ok].astype(str)
            + " != SoldeDebutJournee(" + debut[ok].astype(str)
            + ") + MvtsCrediteurs(" + cred[ok].astype(str)
            + ") - MvtsDebiteurs(" + deb[ok].astype(str)
            + ") = " + computed[ok].astype(str)
            + " (écart=" + delta[ok].astype(str) + ")"
        )
    return _frame(df, cfg, idx, "ARITHMETIC", detail, delta=delta[mask].to_numpy())


def _frame_no_activity(df, cfg, mask, partial_na, nom_na, devise_na, num_compte_na,
                        debut, deb, cred, fin):
    idx = df.index[mask]
    partielle = partial_na[mask]

    detail = pd.Series(index=idx, dtype=object)
    if partielle.any():
        sub = idx[partielle]
        champs = [(cfg.nom_correspondant, nom_na), (cfg.devise, devise_na)]
        if cfg.num_compte:
            champs.append((cfg.num_compte, num_compte_na))
        # Colonnes identifiantes NON-NA alors que d'autres le sont.
        non_na = _noms_manquants(sub, [(nom, ~serie[sub]) for nom, serie in champs])
        detail[partielle] = ("NA partielle : " + non_na
                              + " non-NA alors que d'autres champs identifiants le sont")
    if (~partielle).any():
        sub = idx[~partielle]
        nonzero = _noms_manquants(sub, [
            (c, s[sub].isna() | (s[sub].abs() > _NO_ACTIVITY_EPSILON))
            for c, s in ((cfg.solde_debut, debut), (cfg.mvts_debiteurs, deb),
                          (cfg.mvts_crediteurs, cred), (cfg.solde_fin, fin))
        ])
        detail[~partielle] = ("Ligne 'sans activité' (NA) mais champ(s) non nul(s)/non "
                               "numérique(s) : " + nonzero)
    return _frame(df, cfg, idx, "NO_ACTIVITY_CONFORMITY", detail)


def _frame_date_validity(df, cfg, mask, date_fin_parsed, dt_cr_parsed):
    idx = df.index[mask]
    fin_ko = date_fin_parsed[mask].isna()
    cr_ko = ~fin_ko & dt_cr_parsed[mask].isna()
    posterieure = ~fin_ko & ~cr_ko

    detail = pd.Series(index=idx, dtype=object)
    if fin_ko.any():
        detail[fin_ko] = (f"{cfg.date_fin} non parsable : "
                           + _col(df, cfg.date_fin, idx)[fin_ko].map(repr))
    if cr_ko.any():
        detail[cr_ko] = (f"{cfg.dt_cr} non parsable : "
                          + _col(df, cfg.dt_cr, idx)[cr_ko].map(repr))
    if posterieure.any():
        detail[posterieure] = (
            f"{cfg.date_fin}=" + date_fin_parsed[idx][posterieure].dt.date.astype(str)
            + f" postérieure à {cfg.dt_cr}=" + dt_cr_parsed[idx][posterieure].dt.date.astype(str)
        )
    return _frame(df, cfg, idx, "DATE_VALIDITY", detail)


def run_all_rules(df: pd.DataFrame, cfg: NumericCoherenceConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Exécute les 4 règles. Retourne (df_annotated, anomalies_df).
    Une ligne en échec sur plusieurs règles produit plusieurs lignes dans
    anomalies_df — jamais masquer une anomalie derrière une autre (ex: une ligne
    faussement taguée 'sans activité' avec un solde non nul échoue à la fois
    ARITHMETIC et NO_ACTIVITY_CONFORMITY, les deux sont rapportées).

    La décision (quelles lignes sont anomales) est vectorisée sur les colonnes
    entières ; check_arithmetic_row/check_no_activity_conformity_row/
    check_date_validity_row ne sont rappelées (via .iterrows()) que sur le
    sous-ensemble déjà identifié comme anomale, pour construire le texte Detail —
    jamais sur le DataFrame complet.
    """
    df = df.copy()
    n = len(df)

    solde_debut = _to_float_series(df[cfg.solde_debut])
    mvts_deb    = _to_float_series(df[cfg.mvts_debiteurs])
    mvts_cred   = _to_float_series(df[cfg.mvts_crediteurs])
    solde_fin   = _to_float_series(df[cfg.solde_fin])

    # --- Rule 1 : ARITHMETIC ---
    arithmetic_missing = solde_debut.isna() | mvts_deb.isna() | mvts_cred.isna() | solde_fin.isna()
    computed = solde_debut + mvts_cred - mvts_deb
    arithmetic_delta = (solde_fin - computed).round(2)
    arithmetic_anomaly = arithmetic_missing | (arithmetic_delta.abs() > cfg.tolerance_abs)

    # --- Rule 4 : DATE_VALIDITY ---
    # Comparaison au JOUR, heure ignorée des deux côtés : `dtCr` porte un
    # horodatage de chargement, `DateFinJournee` est une date métier — comparer
    # les heures ferait dépendre le verdict de l'instant d'insertion en base.
    # C'est déjà ce que fait la fonction scalaire de référence to_date_safe(),
    # qui renvoie des `date` ; la décision vectorisée en divergeait.
    date_fin_parsed = _to_date_series(df[cfg.date_fin]).dt.normalize()
    dt_cr_parsed = _to_date_series(df[cfg.dt_cr]).dt.normalize()
    date_missing = date_fin_parsed.isna() | dt_cr_parsed.isna()
    date_anomaly = date_missing | (date_fin_parsed > dt_cr_parsed)

    # --- Rule 3 : NO_ACTIVITY_CONFORMITY ---
    nom_na = _is_na_series(df[cfg.nom_correspondant])
    devise_na = _is_na_series(df[cfg.devise])
    num_compte_na = _is_na_series(df[cfg.num_compte]) if cfg.num_compte else pd.Series(False, index=df.index)
    n_na_fields = 3 if cfg.num_compte else 2
    na_count = nom_na.astype(int) + devise_na.astype(int) + num_compte_na.astype(int)
    is_no_activity_row = na_count == n_na_fields
    partial_na = (na_count > 0) & (na_count < n_na_fields)

    numeric_stack = pd.concat([solde_debut, mvts_deb, mvts_cred, solde_fin], axis=1)
    any_nonzero_or_missing = numeric_stack.isna().any(axis=1) | (numeric_stack.abs() > _NO_ACTIVITY_EPSILON).any(axis=1)
    no_activity_conformity_anomaly = partial_na | (is_no_activity_row & any_nonzero_or_missing)

    conforme = ~(arithmetic_anomaly | no_activity_conformity_anomaly | date_anomaly)

    df["_NC_is_no_activity_row"] = is_no_activity_row.to_numpy()
    df["_NC_row_conforme"] = conforme.to_numpy()

    # --- Construction du texte Detail, VECTORISÉE ---
    # Les colonnes numériques/dates sont déjà parsées ci-dessus : les re-parser
    # valeur par valeur pour composer le texte (ce que faisait la version
    # précédente) coûtait un appel Python par ligne anomale, avec des parseurs
    # scalaires essayant plusieurs formats à coups d'exceptions. Sur un Initial
    # Load où beaucoup de lignes sont anomales, c'était le poste dominant.
    frames = []
    if arithmetic_anomaly.any():
        frames.append(_frame_arithmetic(df, cfg, arithmetic_anomaly, arithmetic_missing,
                                         solde_debut, mvts_deb, mvts_cred, solde_fin,
                                         computed, arithmetic_delta))
    if no_activity_conformity_anomaly.any():
        frames.append(_frame_no_activity(df, cfg, no_activity_conformity_anomaly, partial_na,
                                          nom_na, devise_na, num_compte_na,
                                          solde_debut, mvts_deb, mvts_cred, solde_fin))
    if date_anomaly.any():
        frames.append(_frame_date_validity(df, cfg, date_anomaly, date_fin_parsed, dt_cr_parsed))

    no_activity_mask = pd.Series(is_no_activity_row.to_numpy(), index=df.index)
    temporal_df = check_temporal_continuity(df, cfg, no_activity_mask)

    row_anomalies_df = pd.concat(frames, ignore_index=True)[list(_ANOMALY_COLUMNS)] if frames \
        else pd.DataFrame(columns=_ANOMALY_COLUMNS)

    anomalies_df = pd.concat([row_anomalies_df, temporal_df], ignore_index=True) if not temporal_df.empty \
        else row_anomalies_df

    return df, anomalies_df


# ══════════════════════════════════════════════════════════════════════════════
# FieldProcessor
# ══════════════════════════════════════════════════════════════════════════════

class NumericCoherenceProcessor(FieldProcessor):
    def __init__(self, field_name: str, cfg: NumericCoherenceConfig):
        self.field_name = field_name
        self.cfg = cfg

    def process(self, df: pd.DataFrame, api_id: str) -> FieldResult:
        annotated, anomalies_df = run_all_rules(df, self.cfg)
        n_rows = len(df)
        n_error = int((anomalies_df["Severity"] == "ERROR").sum()) if not anomalies_df.empty else 0
        n_warning = int((anomalies_df["Severity"] == "WARNING").sum()) if not anomalies_df.empty else 0
        stats = {
            "n_rows": n_rows,
            "n_anomalies_error": n_error,
            "n_anomalies_warning": n_warning,
            "taux_conformite_pct": round(100 * annotated["_NC_row_conforme"].sum() / max(n_rows, 1), 2),
        }
        return FieldResult(
            df=annotated,
            classification_df=None,
            outliers_df=anomalies_df,
            exclude_from_export=["_NC_is_no_activity_row", "_NC_row_conforme"],
            stats=stats,
            sheet_names={"outliers": "Anomalies_Numeriques"},
        )

    def instructions_rows(self, outliers_df: pd.DataFrame) -> pd.DataFrame:
        # Pas de cache warm-start pour la cohérence numérique — rien à préremplir.
        return pd.DataFrame(columns=["Champ", "Input", "Label_Attendu"])

    def sheet_columns(self) -> list:
        """Projection lean pour la feuille Anomalies_Numeriques — voir SHEET_COLUMNS."""
        return SHEET_COLUMNS


def build_numeric_coherence_processor(field_cfg: dict) -> NumericCoherenceProcessor:
    """Factory : construit le FieldProcessor cohérence numérique depuis le bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    tolerance = field_cfg.get("tolerance", {}).get("absolute", 0.01)
    cfg = NumericCoherenceConfig(
        solde_debut=cols["solde_debut"],
        mvts_debiteurs=cols["mvts_debiteurs"],
        mvts_crediteurs=cols["mvts_crediteurs"],
        solde_fin=cols["solde_fin"],
        date_fin=cols["date_fin"],
        dt_cr=cols["dt_cr"],
        ref_banque=cols["ref_banque"],
        nom_correspondant=cols["nom_correspondant"],
        devise=cols["devise"],
        num_compte=cols["num_compte"],
        grouping_key=field_cfg.get(
            "grouping_key_temporal_continuity",
            ["RefBanque", "NomCorrespondant", "NumCompte", "Devise"],
        ),
        tolerance_abs=tolerance,
        outlier_tag=field_cfg.get("outlier_tag", "OUTLIER"),
    )
    return NumericCoherenceProcessor(field_cfg["name"], cfg)
