"""
e11_rdcc/global_na.py
========================
Détermination GLOBALE de la ligne "sans activité" légitime pour E11_RDCC —
calculée UNE SEULE FOIS à partir de TOUS les champs concernés ensemble
(NomCorrespondant, Devise, NumCompte + les 4 montants), PAS indépendamment
par chaque champ catégoriel avec un seul témoin partiel.

Correction demandée par le Business Analyst (test métier) : le traitement NA
ne peut pas dépendre d'un seul élément — le gabarit "sans activité" du ticket
doit être vérifié dans son ENSEMBLE :
  { "nomCorrespondant":"NA", "numCompte":"NA", "devise":"NA",
    "soldeDebutJournee":0, "totalMvtsDebiteursJournee":0,
    "totalMvtsCrediteurs":0, "soldeFinJournee":0 }
(dateFinJournee exclue — elle varie toujours). Si UN SEUL de ces éléments ne
correspond pas, la ligne n'est PAS "sans activité" légitime, quel que soit le
champ qui affiche NA individuellement (avant ce correctif, NomCorrespondant et
Devise décidaient chacun indépendamment en ne regardant que NumCompte, sans
vérifier l'autre champ ni les montants — un faux négatif était possible).

Le résultat est injecté comme colonne synthétique dans le DataFrame AVANT que
les FieldProcessor ne s'exécutent (voir E11Pipeline.preprocess()), et branché
comme `ref_transaction` pour NomCorrespondant/Devise dans le YAML —
shared/na_rule.py::apply_na_rule (inchangée) compare alors field=='NA' à cette
colonne précalculée plutôt qu'à un témoin partiel isolé.
"""
from __future__ import annotations

import pandas as pd

GLOBAL_NA_COLUMN = "_E11_GlobalNoActivite"

_NA_EPSILON = 1e-9  # bruit flottant uniquement — vérification de gabarit structurel, pas la tolérance métier

REQUIRED_KEYS = (
    "nom_correspondant", "devise", "num_compte",
    "solde_debut", "mvts_debiteurs", "mvts_crediteurs", "solde_fin",
)


def _is_na_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper() == "NA"


def _is_zero_series(s: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(s.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")
    return numeric.notna() & (numeric.abs() <= _NA_EPSILON)


def has_required_columns(df: pd.DataFrame, numeric_cols: dict) -> bool:
    return all(numeric_cols.get(k) in df.columns for k in REQUIRED_KEYS)


def compute_global_no_activity_column(df: pd.DataFrame, numeric_cols: dict) -> pd.Series:
    """
    `numeric_cols` : le bloc `columns` de la config `type: numeric_coherence`
    (mêmes clés que NumericCoherenceConfig).

    Retourne une Series de chaînes : "NA" si le gabarit "sans activité" est
    intégralement respecté sur cette ligne, "" sinon (jamais égale à "NA") —
    pensée pour être branchée directement sur `ref_transaction` : apply_na_rule
    compare déjà field=='NA' à ref=='NA' sans rien connaître de plus sur E11.

    Lève ValueError (message explicite, colonnes nommées) si une colonne
    requise est absente — appelant responsable de vérifier has_required_columns()
    au préalable s'il veut un dégradé silencieux (ex: extraction ad hoc partielle).
    """
    missing = [numeric_cols.get(k) for k in REQUIRED_KEYS if numeric_cols.get(k) not in df.columns]
    if missing:
        raise ValueError(
            f"compute_global_no_activity_column : colonne(s) manquante(s) dans les données : "
            f"{missing} — vérifie le bloc 'columns' du champ 'numeric_coherence' dans le YAML "
            f"(e11_rdcc/config/E11_RDCC.yaml) et les colonnes réellement présentes dans la source."
        )

    all_na = (
        _is_na_series(df[numeric_cols["nom_correspondant"]])
        & _is_na_series(df[numeric_cols["devise"]])
        & _is_na_series(df[numeric_cols["num_compte"]])
    )
    all_zero = (
        _is_zero_series(df[numeric_cols["solde_debut"]])
        & _is_zero_series(df[numeric_cols["mvts_debiteurs"]])
        & _is_zero_series(df[numeric_cols["mvts_crediteurs"]])
        & _is_zero_series(df[numeric_cols["solde_fin"]])
    )
    is_legit_no_activity = all_na & all_zero
    return is_legit_no_activity.map({True: "NA", False: ""})
