"""
e08_ocd/fields/nomcorrespondant.py
=====================================
Normalisation du champ NomCorrespondant (nom légal des banques correspondantes)
pour E08_OCD — porté depuis DataCleaning-PipelineField-BCMDG/nomcorrespondant/
normalize_nomcorrespondant.py, sans changement de logique métier (identique à
e11_rdcc/fields/nomcorrespondant.py, decision H : dupliqué, pas partagé).

Le cache warm-start `validated_classif_nomcorrespondant_e08_ocd.json` copié
depuis l'ancien repo contient les résolutions Claude déjà validées historiquement
pour E08 (68 entrées au moment du port) — pas reparti de zéro, pour ne pas
re-payer Claude sur des modalités déjà connues.

COLONNES AJOUTÉES :
  NomCorrespondant_clean      — valeur nettoyée (espaces superflus supprimés, casse conservée)
  NomCorrespondant_Normalisé  — nom légal officiel en MAJUSCULES / 'NA' / 'OUTLIER'
  NomCorrespondant_method     — 'MAP' / 'WARM' / 'CLAUDE' / 'NA' / 'OUTLIER'
  NomCorrespondant_check      — True si OUTLIER

CASCADE :
  1. Warm-start (validated_classif_nomcorrespondant_e08_ocd.json) → WARM
  2. Référentiel exact (déjà validé BCM)                          → MAP
  3. Fallback API Claude                                          → CLAUDE si résolu, sinon OUTLIER

RÈGLE NA — témoin NumCredoc (ancien repo : ref_transaction: "NumCredoc") :
  NomCorrespondant == 'NA'  ET  NumCredoc == 'NA'   → 'NA'
  NomCorrespondant == 'NA'  ET  NumCredoc != 'NA'   → OUTLIER
  Vide / NaN / None                                  → OUTLIER
  Valeur non identifiée (Claude inclus)              → OUTLIER
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from shared.claude_client import call_claude_nomcorrespondant_batch
from shared.field_processor import CategoricalFieldProcessor
from shared.na_rule import apply_na_rule

_REFERENTIEL_DIR = Path(__file__).parent.parent / "referentiel"
_RE_SPACES = re.compile(r"\s+")


def clean_nomcorrespondant(raw) -> str:
    """Supprime les espaces superflus (début/fin + doublons internes). Casse conservée."""
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    return _RE_SPACES.sub(" ", s).strip()


def load_nomcorrespondant_referentiel(path: str | Path) -> dict:
    """Structure attendue : {"version": ..., "mapping": {"<brut>": "<normalisé>"}}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("mapping", {})


def _warm_start_path(api_id: str) -> Path:
    return _REFERENTIEL_DIR / f"validated_classif_nomcorrespondant_{api_id.lower()}.json"


def load_warm_start_nomcorrespondant(api_id: str) -> dict:
    path = _warm_start_path(api_id)
    if not path.exists():
        return {}
    return json.load(open(path, encoding="utf-8")).get("classif", {})


def save_warm_start_nomcorrespondant(api_id: str, new_entries: dict, verbose: bool = False) -> None:
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
        print(f"  Warm-start NomCorrespondant {api_id} : +{len(new_entries)} modalité(s) mise(s) en cache")


def treating_nomcorrespondant(
    df:         pd.DataFrame,
    corr_col:   str  = "NomCorrespondant",
    ref_col:    str  = "NumCredoc",
    ref:        dict = None,
    api_id:     str  = "E08_OCD",
    cfg:        dict = None,
    warm_start: bool = True,
    verbose:    bool = False,
) -> pd.DataFrame:
    """Normalise la colonne NomCorrespondant. Traitement sur valeurs uniques."""
    df = df.copy()

    if ref is None:
        ref = load_nomcorrespondant_referentiel(_REFERENTIEL_DIR / "nomcorrespondant_referentiel_E08.json")

    if cfg is None:
        cfg = {}

    ws_cache: dict = {}
    if warm_start:
        ws_cache = load_warm_start_nomcorrespondant(api_id)
        if verbose:
            print(f"  Warm-start NomCorrespondant {api_id} : {len(ws_cache)} modalités connues")

    unique_vals = df[corr_col].dropna().unique()
    clean_map   = {v: clean_nomcorrespondant(str(v)) for v in unique_vals}
    df["NomCorrespondant_clean"] = df[corr_col].map(clean_map).fillna("")

    result_map: dict = {}
    to_resolve_claude: dict = {}

    for v in unique_vals:
        clean = clean_map[v]

        if not clean:
            result_map[v] = (None, None)
            continue

        if warm_start:
            s = str(v)
            if s in ws_cache:
                result_map[v] = (ws_cache[s], "WARM"); continue
            if s.rstrip() in ws_cache:
                result_map[v] = (ws_cache[s.rstrip()], "WARM"); continue
            if clean in ws_cache:
                result_map[v] = (ws_cache[clean], "WARM"); continue

        if clean in ref:
            result_map[v] = (ref[clean], "MAP")
            continue

        if clean.upper() == "NA":
            result_map[v] = ("OUTLIER", "OUTLIER")
            continue

        result_map[v] = None
        to_resolve_claude.setdefault(clean, None)

    a_claude   = list(to_resolve_claude.keys())
    batch_size = cfg.get("llm", {}).get("batch_size", 20)
    claude_resultats: dict = {}
    failed_techniquement: set = set()
    total = len(a_claude)
    for debut in range(0, total, batch_size):
        batch_val = a_claude[debut:debut + batch_size]
        reponses  = call_claude_nomcorrespondant_batch(batch_val, cfg)
        if reponses is None:
            print(f"  [CLAUDE] échec technique sur le batch {debut}-{debut+len(batch_val)} "
                  f"-> OUTLIER temporaire (non mis en cache, réessayé au prochain run)")
            for modalite in batch_val:
                claude_resultats[modalite] = None
                failed_techniquement.add(modalite)
            continue
        for k, modalite in enumerate(batch_val):
            claude_resultats[modalite] = reponses[k]
        if verbose:
            print(f"  [CLAUDE] {min(debut + batch_size, total)}/{total} modalités", end="\r")
    if total and verbose:
        print()

    cacheable = {
        modalite: (lbl or "OUTLIER")
        for modalite, lbl in claude_resultats.items()
        if modalite not in failed_techniquement
    }
    if cacheable:
        save_warm_start_nomcorrespondant(api_id, cacheable, verbose=verbose)

    for v in unique_vals:
        if result_map[v] is None:
            lbl = claude_resultats.get(clean_map[v])
            result_map[v] = (lbl, "CLAUDE") if lbl else ("OUTLIER", "OUTLIER")

    df["NomCorrespondant_Normalisé"] = df[corr_col].map(lambda v: result_map.get(v, (None, None))[0])
    df["NomCorrespondant_method"]    = df[corr_col].map(lambda v: result_map.get(v, (None, None))[1])
    df["_ws_hit"] = df["NomCorrespondant_method"] == "WARM"

    if ref_col in df.columns:
        fixed = df.apply(
            lambda row: apply_na_rule(
                row, corr_col, ref_col, "NomCorrespondant_Normalisé", "NomCorrespondant_method"
            ),
            axis=1,
            result_type="expand",
        )
        df["NomCorrespondant_Normalisé"] = fixed[0]
        df["NomCorrespondant_method"]    = fixed[1]

    df["NomCorrespondant_check"] = df["NomCorrespondant_Normalisé"] == "OUTLIER"
    return df


def build_full_classification_nomcorrespondant(ref: dict, api_id: str, col_in: str, col_out: str) -> pd.DataFrame:
    """
    Table de classification CUMULATIVE (référentiel BCM + cache warm-start), PAS les
    lignes du run en cours — indispensable en mode incrémental. Le cache (Claude
    passé + corrections manuelles) prime sur le référentiel statique.
    """
    combined = dict(ref)
    combined.update(load_warm_start_nomcorrespondant(api_id))

    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_nomcorrespondant_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor NomCorrespondant à partir du bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    referentiel_path = Path(field_cfg["referentiel_path"])
    ref = load_nomcorrespondant_referentiel(referentiel_path)

    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_nomcorrespondant,
        treating_kwargs={
            "corr_col": cols["field"],
            "ref_col": cols["ref_transaction"],
            "ref": ref,
            "api_id": None,          # rempli dynamiquement par CategoricalFieldProcessor.process()
            "cfg": field_cfg,
            "warm_start": True,      # Claude payant — toujours actif, cf. ancien repo
            "verbose": False,
        },
        col_in=cols["field"],
        col_out=cols["field_out"],
        ref_banque_col=cols.get("ref_banque", "RefBanque"),
        outlier_tag=field_cfg.get("outlier_tag", "OUTLIER"),
        exclude_suffixes=("_clean", "_method", "_check"),
        clean_fn=clean_nomcorrespondant,
        save_warm_start_fn=save_warm_start_nomcorrespondant,
        classification_fn=lambda api_id: build_full_classification_nomcorrespondant(
            ref, api_id, cols["field"], cols["field_out"]
        ),
    )
