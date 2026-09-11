"""
e07_fs/fields/typeswift.py
============================
Normalisation du champ TypeSwift pour E07_FS — portée depuis l'ancien repo
field-based (typeswift/normalize_typeswift.py), même cascade. La colonne source
s'appelle `TypeSwfit` (faute d'orthographe du schéma BCM, conservée telle quelle :
c'est le vrai nom en base) ; la colonne normalisée est `TypeSwift_Normalisé`.

Référentiel : codes SWIFT valides PAR FLUX (`valid_fs` pour les Flux Sortants
d'E07), stockés sous forme canonique avec espaces (« MT 103 ») et comparés sans
espaces (« MT103 »).

COLONNES AJOUTÉES ({col} = colonne source, ex. TypeSwfit) :
  {col}_clean          — clé de lookup (sans espaces, majuscules)
  TypeSwift_Normalisé  — code SWIFT canonique / 'NA' / 'OUTLIER'
  {col}_method         — 'WARM' / 'MAP' / 'PREFIX' / 'NOISE' / 'NA' / 'OUTLIER'
  {col}_check          — True si OUTLIER

Cascade (sur valeurs uniques, jamais ligne par ligne) :
  A. Vide / NaN                              -> non résolu (OUTLIER via la règle NA)
  B. Cache warm-start (brut, rstrip, clé)    -> WARM
  C. Bruit connu / ressemble à un montant    -> OUTLIER (NOISE)
  D. Code valide du flux                     -> MAP
  E. Numéro seul à 3 chiffres (« 103 »)      -> MT{num} s'il est valide (PREFIX)
  F. Non identifié                           -> OUTLIER

RÈGLE NA — témoin ReferenceTransaction (shared/na_rule.py) :
  TypeSwfit == 'NA' ET ReferenceTransaction == 'NA'  -> 'NA' (message sans activité)
  TypeSwfit == 'NA' ET ReferenceTransaction != 'NA'  -> OUTLIER
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
_RE_MONTANT = re.compile(r"[\d,\.]{5,}|,")
_RE_3CHIFFRES = re.compile(r"^\d{3}$")


@dataclass
class TypeSwiftReferentiel:
    version: str
    flux: str
    lookup: dict = field(default_factory=dict)   # CLÉ SANS ESPACES -> forme canonique
    noise: set = field(default_factory=set)


def _cle(valeur) -> str:
    return _RE_SPACES.sub("", str(valeur)).upper()


def clean_typeswift(raw) -> str:
    """Clé de lookup : espaces supprimés, majuscules (« MT 103 + » -> « MT103+ »)."""
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    return _cle(s)


def load_typeswift_referentiel(path, flux: str = "FS") -> TypeSwiftReferentiel:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    codes = data.get("valid_fs" if str(flux).upper() == "FS" else "valid_fe", [])
    return TypeSwiftReferentiel(
        version=data.get("version", "unknown"),
        flux=str(flux).upper(),
        lookup={_cle(c): c for c in codes},
        noise={_cle(n) for n in data.get("known_noise", [])},
    )


def _warm_start_path(api_id: str) -> Path:
    return _REFERENTIEL_DIR / f"validated_classif_typeswift_{api_id.lower()}.json"


def load_warm_start_typeswift(api_id: str = "E07_FS") -> dict:
    path = _warm_start_path(api_id)
    if not path.exists():
        return {}
    return json.load(open(path, encoding="utf-8")).get("classif", {})


def save_warm_start_typeswift(api_id: str, new_entries: dict, verbose: bool = False) -> None:
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
        print(f"  Warm-start TypeSwift {api_id} : +{len(new_entries)} modalité(s) mise(s) en cache")


def _resolve_typeswift(raw, ref: TypeSwiftReferentiel) -> tuple:
    cle = clean_typeswift(raw)
    if not cle:
        return None, None
    if cle in ref.noise or _RE_MONTANT.search(cle):
        return "OUTLIER", "NOISE"
    if cle in ref.lookup:
        return ref.lookup[cle], "MAP"
    if _RE_3CHIFFRES.match(cle) and f"MT{cle}" in ref.lookup:
        return ref.lookup[f"MT{cle}"], "PREFIX"
    return "OUTLIER", "OUTLIER"


def treating_typeswift(
    df:         pd.DataFrame,
    swift_col:  str                  = "TypeSwfit",
    out_col:    str                  = "TypeSwift_Normalisé",
    ref_col:    str                  = "ReferenceTransaction",
    ref:        TypeSwiftReferentiel = None,
    api_id:     str                  = "E07_FS",
    warm_start: bool                 = True,
) -> pd.DataFrame:
    """Normalise la colonne TypeSwift. Traitement sur valeurs uniques."""
    df = df.copy()
    if ref is None:
        ref = load_typeswift_referentiel(_REFERENTIEL_DIR / "typeswift_referentiel.json")

    method_col = f"{swift_col}_method"
    ws_cache = load_warm_start_typeswift(api_id) if warm_start else {}
    ws_par_cle = {_cle(k): v for k, v in ws_cache.items()}

    uniques = df[swift_col].dropna().unique()
    df[f"{swift_col}_clean"] = df[swift_col].map({v: clean_typeswift(v) for v in uniques}).fillna("")

    resolu: dict = {}
    for v in uniques:
        s = str(v)
        if warm_start:
            if s in ws_cache:
                resolu[v] = (ws_cache[s], "WARM"); continue
            if s.rstrip() in ws_cache:
                resolu[v] = (ws_cache[s.rstrip()], "WARM"); continue
            cle = clean_typeswift(s)
            if cle and cle in ws_par_cle:
                resolu[v] = (ws_par_cle[cle], "WARM"); continue
        resolu[v] = _resolve_typeswift(s, ref)

    # Series.map(dict) : recherche au niveau C de pandas, sans appel Python par ligne.
    df[out_col] = df[swift_col].map({k: r[0] for k, r in resolu.items()})
    df[method_col] = df[swift_col].map({k: r[1] for k, r in resolu.items()})
    df["_ws_hit"] = df[method_col] == "WARM"

    if ref_col in df.columns:
        iso, mth = apply_na_rule_frame(df, swift_col, ref_col, out_col, method_col)
        df[out_col] = iso
        df[method_col] = mth

    df[f"{swift_col}_check"] = df[out_col] == "OUTLIER"
    return df


def build_full_classification_typeswift(ref: TypeSwiftReferentiel, api_id: str,
                                        col_in: str, col_out: str) -> pd.DataFrame:
    """Table de classification CUMULATIVE (référentiel + cache), indépendante du
    delta traité — même raisonnement que e07_fs/fields/devise.py."""
    combined: dict = {code: code for code in ref.lookup.values()}
    combined.update({n: "OUTLIER" for n in ref.noise})
    combined.update(load_warm_start_typeswift(api_id))
    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_typeswift_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor TypeSwift depuis le bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    ref = load_typeswift_referentiel(Path(field_cfg["referentiel_path"]),
                                     flux=field_cfg.get("flux", "FS"))
    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_typeswift,
        treating_kwargs={
            "swift_col": cols["field"],
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
        clean_fn=clean_typeswift,
        save_warm_start_fn=save_warm_start_typeswift,
        classification_fn=lambda api_id: build_full_classification_typeswift(
            ref, api_id, cols["field"], cols["field_out"]
        ),
    )
