"""
e07_fs/fields/mode_reglement.py
=================================
Normalisation du champ ModeReglement pour E07_FS — portée depuis l'ancien repo
field-based (mode_reglement/normalize_mode_reglement.py), même cascade.

Référentiel : 3 codes valides (CD, RD, TL), alias (TR -> TL), bruit connu.

COLONNES AJOUTÉES ({col} = colonne source, ex. ModeReglement) :
  {col}_clean               — valeur nettoyée (sans espaces, majuscules)
  ModeReglement_Normalisé   — code normalisé / 'NA' / 'OUTLIER'
  {col}_method              — 'WARM' / 'MAP' / 'ALIAS' / 'NOISE' / 'NA' / 'OUTLIER'
  {col}_check               — True si OUTLIER

Cascade (sur valeurs uniques) :
  A. Vide / NaN                                  -> non résolu (OUTLIER via la règle NA)
  B. Cache warm-start (brut, strip, rstrip, clé) -> WARM
  C. Bruit connu                                 -> OUTLIER (NOISE)
  D. Code valide                                 -> MAP
  E. Alias                                       -> ALIAS
  F. Non identifié                               -> OUTLIER

RÈGLE NA — témoin ReferenceTransaction (shared/na_rule.py), comme tous les champs
catégoriels d'E07 : un 'NA' n'est légitime que sur un message sans activité
(ReferenceTransaction == 'NA', gabarit du ticket) ; sinon OUTLIER.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from shared.field_processor import CategoricalFieldProcessor
from shared.na_rule import apply_na_rule_frame

_REFERENTIEL_DIR = Path(__file__).parent.parent / "referentiel"
_RE_SPACES = re.compile(r"\s+")


@dataclass
class ModeReglementReferentiel:
    version: str
    valid:   set  = field(default_factory=set)
    aliases: dict = field(default_factory=dict)
    noise:   set  = field(default_factory=set)


def clean_mode_reglement(raw) -> str:
    """Trim + suppression des espaces internes + majuscules."""
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    return _RE_SPACES.sub("", s).upper()


def load_mode_reglement_referentiel(path) -> ModeReglementReferentiel:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return ModeReglementReferentiel(
        version=data.get("version", "unknown"),
        valid={clean_mode_reglement(v) for v in data.get("valid_modes", [])},
        aliases={clean_mode_reglement(k): v for k, v in data.get("aliases", {}).items()},
        noise={clean_mode_reglement(n) for n in data.get("known_noise", [])},
    )


def _warm_start_path(api_id: str) -> Path:
    return _REFERENTIEL_DIR / f"validated_classif_mode_reglement_{api_id.lower()}.json"


def load_warm_start_mode_reglement(api_id: str = "E07_FS") -> dict:
    path = _warm_start_path(api_id)
    if not path.exists():
        return {}
    return json.load(open(path, encoding="utf-8")).get("classif", {})


def save_warm_start_mode_reglement(api_id: str, new_entries: dict, verbose: bool = False) -> None:
    """Fusionne des corrections manuelles (apply_corrections.py) dans le cache."""
    if not new_entries:
        return
    path = _warm_start_path(api_id)
    data = {"version": "1.0.0", "classif": {}}
    if path.exists():
        data = json.load(open(path, encoding="utf-8"))
        data.setdefault("classif", {})
    data["classif"].update(new_entries)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    if verbose:
        print(f"  Warm-start ModeReglement {api_id} : +{len(new_entries)} modalité(s) mise(s) en cache")


def _resolve_mode_reglement(raw, ref: ModeReglementReferentiel) -> tuple:
    cle = clean_mode_reglement(raw)
    if not cle:
        return None, None
    if cle in ref.noise:
        return "OUTLIER", "NOISE"
    if cle in ref.valid:
        return cle, "MAP"
    if cle in ref.aliases:
        return ref.aliases[cle], "ALIAS"
    return "OUTLIER", "OUTLIER"


def treating_mode_reglement(
    df:         pd.DataFrame,
    mode_col:   str                      = "ModeReglement",
    out_col:    str                      = "ModeReglement_Normalisé",
    ref_col:    str                      = "ReferenceTransaction",
    ref:        ModeReglementReferentiel = None,
    api_id:     str                      = "E07_FS",
    warm_start: bool                     = True,
) -> pd.DataFrame:
    """Normalise la colonne ModeReglement. Traitement sur valeurs uniques."""
    df = df.copy()
    if ref is None:
        ref = load_mode_reglement_referentiel(_REFERENTIEL_DIR / "mode_reglement_referentiel.json")

    method_col = f"{mode_col}_method"
    ws_cache = load_warm_start_mode_reglement(api_id) if warm_start else {}
    ws_par_cle = {clean_mode_reglement(k): v for k, v in ws_cache.items()}

    uniques = df[mode_col].dropna().unique()
    df[f"{mode_col}_clean"] = df[mode_col].map({v: clean_mode_reglement(v) for v in uniques}).fillna("")

    resolu: dict = {}
    for v in uniques:
        s = str(v)
        if warm_start:
            trouve = next((ws_cache[k] for k in (s, s.strip(), s.rstrip()) if k in ws_cache), None)
            if trouve is None:
                cle = clean_mode_reglement(s)
                trouve = ws_par_cle.get(cle) if cle else None
            if trouve is not None:
                resolu[v] = (trouve, "WARM"); continue
        resolu[v] = _resolve_mode_reglement(s, ref)

    # Series.map(dict) : recherche au niveau C de pandas, sans appel Python par ligne.
    df[out_col] = df[mode_col].map({k: r[0] for k, r in resolu.items()})
    df[method_col] = df[mode_col].map({k: r[1] for k, r in resolu.items()})
    df["_ws_hit"] = df[method_col] == "WARM"

    if ref_col in df.columns:
        iso, mth = apply_na_rule_frame(df, mode_col, ref_col, out_col, method_col)
        df[out_col] = iso
        df[method_col] = mth

    df[f"{mode_col}_check"] = df[out_col] == "OUTLIER"
    return df


def build_full_classification_mode_reglement(ref: ModeReglementReferentiel, api_id: str,
                                             col_in: str, col_out: str) -> pd.DataFrame:
    """Table de classification CUMULATIVE (référentiel + cache), indépendante du delta."""
    combined: dict = {v: v for v in ref.valid}
    combined.update(ref.aliases)
    combined.update({n: "OUTLIER" for n in ref.noise})
    combined.update(load_warm_start_mode_reglement(api_id))
    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_mode_reglement_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor ModeReglement depuis le bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    ref = load_mode_reglement_referentiel(Path(field_cfg["referentiel_path"]))
    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_mode_reglement,
        treating_kwargs={
            "mode_col": cols["field"],
            "out_col": cols["field_out"],
            "ref_col": cols["ref_transaction"],
            "ref": ref,
            "api_id": None,   # rempli dynamiquement par CategoricalFieldProcessor.process()
            "warm_start": True,
        },
        col_in=cols["field"],
        col_out=cols["field_out"],
        ref_banque_col=cols.get("ref_banque", "RefBanque"),
        outlier_tag=field_cfg.get("outlier_tag", "OUTLIER"),
        exclude_suffixes=("_clean", "_method", "_check"),
        clean_fn=clean_mode_reglement,
        save_warm_start_fn=save_warm_start_mode_reglement,
        classification_fn=lambda api_id: build_full_classification_mode_reglement(
            ref, api_id, cols["field"], cols["field_out"]
        ),
    )
