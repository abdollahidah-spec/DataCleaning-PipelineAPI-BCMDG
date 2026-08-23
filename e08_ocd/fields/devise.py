"""
e08_ocd/fields/devise.py
===========================
Normalisation du champ Devise pour E08_OCD — logique IDENTIQUE à
e11_rdcc/fields/devise.py / e09_pe/fields/devise.py (même cascade déterministe,
même référentiel ISO 4217), dupliquée ici par choix architectural (decision H :
chaque API est autonome, pas de dépendance croisée entre packages). Seul le
cache warm-start diffère : scopé à E08_OCD (validated_classif_devise_e08_ocd.json).

COLONNES AJOUTÉES :
  Devise_clean       — valeur nettoyée (sans espaces, sans .0, majuscules)
  Devise_Normalisée  — code ISO 4217 alpha-3 / 'NA' / 'OUTLIER'
  Devise_method      — 'MAP' / 'NUM' / 'ALIAS' / 'STRIP' / 'NA' / 'OUTLIER' / 'WARM'
  Devise_check       — True si OUTLIER

RÈGLE NA — témoin NumCredoc (confirmé, ancien repo devise/config/E08_OCD.yaml :
ref_transaction: "NumCredoc") :
  Devise == 'NA'  ET  NumCredoc == 'NA'   → 'NA'
  Devise == 'NA'  ET  NumCredoc != 'NA'   → OUTLIER
  Devise vide / null                       → OUTLIER
  Valeur non identifiée                    → OUTLIER
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from shared.field_processor import CategoricalFieldProcessor
from shared.na_rule import apply_na_rule

_REFERENTIEL_DIR = Path(__file__).parent.parent / "referentiel"
_RE_FLOAT_SUF = re.compile(r"\.0+$")
_RE_SPACES    = re.compile(r"\s+")
_RE_SYMBOLS   = re.compile(r"[^A-Za-z0-9]")
_RE_3DIGITS   = re.compile(r"^\d{3}$")


@dataclass
class DeviseReferentiel:
    version: str
    valid:   set  = field(default_factory=set)
    num_map: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)
    noise:   set  = field(default_factory=set)


def load_devise_referentiel(path) -> DeviseReferentiel:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return DeviseReferentiel(
        version = data.get("version", "unknown"),
        valid   = set(data.get("valid_iso4217", [])),
        num_map = data.get("num_to_iso", {}),
        aliases = data.get("aliases", {}),
        noise   = set(data.get("known_noise", [])),
    )


def _warm_start_path(api_id: str) -> Path:
    return _REFERENTIEL_DIR / f"validated_classif_devise_{api_id.lower()}.json"


def load_warm_start_devise(api_id: str = "E08_OCD") -> dict:
    """Cache validé, scopé par API : clé = valeur brute (ou rstrip), valeur = code ISO 4217."""
    path = _warm_start_path(api_id)
    if not path.exists():
        return {}
    return json.load(open(path, encoding="utf-8")).get("classif", {})


def save_warm_start_devise(api_id: str, new_entries: dict, verbose: bool = False) -> None:
    """Fusionne de nouvelles résolutions (corrections manuelles via apply_corrections.py)
    dans le cache warm-start Devise de cette API."""
    if not new_entries:
        return
    path = _warm_start_path(api_id)
    data = {"_comment": "Cache warm-start NA exclu", "version": "1.0.0", "classif": {}}
    if path.exists():
        data = json.load(open(path, encoding="utf-8"))
        data.setdefault("classif", {})
    data["classif"].update(new_entries)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    if verbose:
        print(f"  Warm-start Devise {api_id} : +{len(new_entries)} modalité(s) mise(s) en cache")


def clean_devise(raw) -> str:
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    s = _RE_FLOAT_SUF.sub("", s)
    s = _RE_SPACES.sub("", s)
    return s.upper().strip()


def _strip_description(s):
    return s[:s.index("(")].strip() if "(" in s else s


def _alphanum_only(s):
    return _RE_SYMBOLS.sub("", s)


def _resolve_devise(raw_value, ref: DeviseReferentiel):
    if pd.isna(raw_value) or str(raw_value).strip() == "":
        return None, None
    cleaned = clean_devise(str(raw_value))
    if not cleaned:
        return None, None
    if cleaned in ref.noise:
        return "OUTLIER", "NOISE"
    if cleaned in ref.valid:
        return cleaned, "MAP"
    if _RE_3DIGITS.match(cleaned) and cleaned in ref.num_map:
        return ref.num_map[cleaned], "NUM"
    if cleaned in ref.aliases:
        return ref.aliases[cleaned], "ALIAS"
    stripped = _strip_description(cleaned)
    if stripped != cleaned:
        if stripped in ref.valid:   return stripped, "STRIP"
        if stripped in ref.aliases: return ref.aliases[stripped], "STRIP"
    anum = _alphanum_only(cleaned)
    if anum in ref.valid:   return anum, "STRIP"
    if anum in ref.aliases: return ref.aliases[anum], "STRIP"
    if len(anum) > 3:
        prefix = anum[:3]
        if prefix in ref.valid:   return prefix, "STRIP"
        if prefix in ref.aliases: return ref.aliases[prefix], "STRIP"
    return "OUTLIER", "OUTLIER"


def treating_devise(
    df:         pd.DataFrame,
    devise_col: str              = "Devise",
    ref_col:    str              = "NumCredoc",
    ref:        DeviseReferentiel = None,
    api_id:     str              = "E08_OCD",
    warm_start: bool             = True,
) -> pd.DataFrame:
    """
    Normalise la colonne Devise.

    warm_start : si True, résout d'abord via le cache validated_classif_devise_{api}.json
                 (clé brute ou rstrip) avant la cascade normale.
    """
    df = df.copy()

    if ref is None:
        ref = load_devise_referentiel(_REFERENTIEL_DIR / "devise_referentiel.json")

    ws_cache: dict = {}
    if warm_start:
        ws_cache = load_warm_start_devise(api_id)

    unique_vals = df[devise_col].dropna().unique()
    clean_map   = {v: clean_devise(str(v)) for v in unique_vals}
    df["Devise_clean"] = df[devise_col].map(clean_map).fillna("")

    iso_map: dict = {}
    for v in unique_vals:
        if warm_start:
            if str(v) in ws_cache:
                iso_map[v] = (ws_cache[str(v)], "WARM")
                continue
            key_rs = str(v).rstrip()
            if key_rs in ws_cache:
                iso_map[v] = (ws_cache[key_rs], "WARM")
                continue
        iso_map[v] = _resolve_devise(str(v), ref)

    df["Devise_Normalisée"] = df[devise_col].map(lambda v: iso_map.get(v, (None, None))[0])
    df["Devise_method"]     = df[devise_col].map(lambda v: iso_map.get(v, (None, None))[1])

    df["_ws_hit"] = df["Devise_method"] == "WARM"

    if ref_col in df.columns:
        fixed = df.apply(
            lambda row: apply_na_rule(
                row, devise_col, ref_col, "Devise_Normalisée", "Devise_method"
            ),
            axis=1,
            result_type="expand",
        )
        df["Devise_Normalisée"] = fixed[0]
        df["Devise_method"]     = fixed[1]

    df["Devise_check"] = df["Devise_Normalisée"] == "OUTLIER"
    return df


def build_full_classification_devise(ref: DeviseReferentiel, api_id: str, col_in: str, col_out: str) -> pd.DataFrame:
    """
    Table de classification CUMULATIVE (référentiel + cache warm-start), PAS les
    lignes du run en cours — indispensable en mode incrémental : un label vu la
    semaine dernière mais absent du delta de cette semaine ne doit jamais disparaître
    du classeur BI (chemin stable, écrasé à chaque run). Le cache prime sur le
    référentiel (une correction manuelle doit pouvoir surcharger une entrée statique).
    """
    combined: dict = {v: v for v in ref.valid}
    combined.update(ref.num_map)
    combined.update(ref.aliases)
    combined.update({v: "OUTLIER" for v in ref.noise})
    combined.update(load_warm_start_devise(api_id))

    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_devise_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor Devise à partir du bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    referentiel_path = Path(field_cfg["referentiel_path"])
    ref = load_devise_referentiel(referentiel_path)

    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_devise,
        treating_kwargs={
            "devise_col": cols["field"],
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
        clean_fn=clean_devise,
        save_warm_start_fn=save_warm_start_devise,
        classification_fn=lambda api_id: build_full_classification_devise(
            ref, api_id, cols["field"], cols["field_out"]
        ),
    )
