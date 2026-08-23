"""
e08_ocd/fields/beneficiaire.py
==================================
Normalisation du champ Beneficiaire pour E08_OCD — porté depuis
DataCleaning-PipelineField-BCMDG/beneficiaire/normalize_beneficiaire.py
(fonction `treating_beneficiaire_web`, seule variante utilisée par E08).

Pour un crédit documentaire IMPORT (E08_OCD), le bénéficiaire est une entité
ÉTRANGÈRE (le fournisseur à l'étranger) : pas de base fiscale DGI mauritanienne
pertinente ici (contrairement à NomDonneurOrdre) — fallback Claude + recherche
web au lieu du matching DGI. Réutilise le même socle de nettoyage/classification
que NomDonneurOrdre (e08_ocd/fields/_entity_matching.py) — les deux champs
restent ainsi toujours alignés sur la même logique.

COLONNES AJOUTÉES :
  Beneficiaire_clean       — valeur nettoyée (clean_label)
  Beneficiaire_Normalisé   — nom légal officiel en MAJUSCULES / 'NA' / 'OUTLIER'
  Beneficiaire_method      — 'WARM' / 'MAP' / 'PUBLIC_ENT' / 'PARTICULIER' /
                               'ETS_PERSONNEL' / 'ETS_OUTLIER' / 'CLAUDE' / 'OUTLIER'
  Beneficiaire_check       — True si OUTLIER

CASCADE :
  1. Warm-start → référentiel → règle NA (raccourci) → entreprise publique
     → outlier évident (raccourci)
  2. Classification locale (particulier / ETS personnel / ETS outlier)
  3. Fallback Claude (recherche web réelle) → CLAUDE si résolu, sinon OUTLIER

PERSISTANCE DU CACHE CLAUDE : seules les résolutions payantes (recherche web)
sont écrites dans validated_classif_beneficiaire_e08_ocd.json.

RÈGLE NA — témoin NumCredoc (même convention que les autres champs E08_OCD) :
  Beneficiaire == 'NA'  ET  NumCredoc == 'NA'   → 'NA'
  Beneficiaire == 'NA'  ET  NumCredoc != 'NA'   → OUTLIER
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from shared.claude_client import call_claude_beneficiaire_web_batch
from shared.field_processor import CategoricalFieldProcessor
from shared.na_rule import apply_na_rule

from e08_ocd.fields._entity_matching import (
    classify_local,
    clean_label,
    est_outlier_evident,
    load_public_entities,
    match_public_entity,
    prepare_public_ent_index,
)

_REFERENTIEL_DIR = Path(__file__).parent.parent / "referentiel"


def load_referentiel(path: str | Path) -> dict:
    """Structure attendue : {"mapping": {"<brut>": "<normalisé>"}}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("mapping", {})


def _warm_start_path(api_id: str) -> Path:
    return _REFERENTIEL_DIR / f"validated_classif_beneficiaire_{api_id.lower()}.json"


def load_warm_start(api_id: str) -> dict:
    path = _warm_start_path(api_id)
    if not path.exists():
        return {}
    return json.load(open(path, encoding="utf-8")).get("classif", {})


def save_warm_start(api_id: str, new_entries: dict, verbose: bool = False) -> None:
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
        print(f"  Warm-start Beneficiaire {api_id} : +{len(new_entries)} modalité(s) mise(s) en cache")


def treating_beneficiaire(
    df:           pd.DataFrame,
    corr_col:     str  = "Beneficiaire",
    ref_col:      str  = "NumCredoc",
    ref:          dict = None,
    public_index: dict = None,
    api_id:       str  = "E08_OCD",
    cfg:          dict = None,
    warm_start:   bool = True,
    verbose:      bool = False,
) -> pd.DataFrame:
    """Normalise la colonne Beneficiaire. Traitement sur valeurs uniques."""
    df = df.copy()

    if ref is None:
        ref = load_referentiel(_REFERENTIEL_DIR / "beneficiaire_referentiel_E08.json")

    if public_index is None:
        public_index = prepare_public_ent_index(load_public_entities())

    cfg = cfg or {}

    ws_cache: dict = {}
    if warm_start:
        ws_cache = load_warm_start(api_id)
        if verbose:
            print(f"  Warm-start Beneficiaire {api_id} : {len(ws_cache)} modalités connues")

    unique_vals = df[corr_col].dropna().unique()
    clean_map   = {v: clean_label(v) for v in unique_vals}
    df["Beneficiaire_clean"] = df[corr_col].map(clean_map).fillna("")

    result_map: dict = {}
    to_resolve_claude: dict = {}

    for v in unique_vals:
        clean = clean_map[v]

        if not clean:
            result_map[v] = (None, None)
            continue

        if warm_start and clean in ws_cache:
            result_map[v] = (ws_cache[clean], "WARM")
            continue

        if clean in ref:
            result_map[v] = (ref[clean], "MAP")
            continue

        if clean.upper() == "NA":
            result_map[v] = ("OUTLIER", "OUTLIER")
            continue

        pub_short = match_public_entity(clean, public_index)
        if pub_short is not None:
            result_map[v] = (pub_short, "PUBLIC_ENT")
            continue

        if est_outlier_evident(clean):
            result_map[v] = ("OUTLIER", "OUTLIER")
            continue

        normalized, method = classify_local(clean)
        if normalized is not None:
            result_map[v] = (normalized, method)
            continue

        result_map[v] = None
        to_resolve_claude.setdefault(clean, None)

    a_claude   = list(to_resolve_claude.keys())
    batch_size = cfg.get("llm", {}).get("batch_size", 5)
    claude_resultats: dict = {}
    failed_techniquement: set = set()
    total = len(a_claude)
    for debut in range(0, total, batch_size):
        batch_val = a_claude[debut:debut + batch_size]
        reponses  = call_claude_beneficiaire_web_batch(batch_val, cfg)
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
        save_warm_start(api_id, cacheable, verbose=verbose)

    for v in unique_vals:
        if result_map[v] is None:
            lbl = claude_resultats.get(clean_map[v])
            result_map[v] = (lbl, "CLAUDE") if lbl else ("OUTLIER", "OUTLIER")

    df["Beneficiaire_Normalisé"] = df[corr_col].map(lambda v: result_map.get(v, (None, None))[0])
    df["Beneficiaire_method"]    = df[corr_col].map(lambda v: result_map.get(v, (None, None))[1])
    df["_ws_hit"] = df["Beneficiaire_method"] == "WARM"

    if ref_col in df.columns:
        fixed = df.apply(
            lambda row: apply_na_rule(
                row, corr_col, ref_col, "Beneficiaire_Normalisé", "Beneficiaire_method"
            ),
            axis=1,
            result_type="expand",
        )
        df["Beneficiaire_Normalisé"] = fixed[0]
        df["Beneficiaire_method"]    = fixed[1]

    df["Beneficiaire_check"] = df["Beneficiaire_Normalisé"] == "OUTLIER"
    return df


def build_full_classification_beneficiaire(ref: dict, api_id: str, col_in: str, col_out: str) -> pd.DataFrame:
    """Table de classification CUMULATIVE (référentiel + cache warm-start Claude)."""
    combined = dict(ref)
    combined.update(load_warm_start(api_id))

    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_beneficiaire_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor Beneficiaire à partir du bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    referentiel_path = Path(field_cfg["referentiel_path"])
    ref = load_referentiel(referentiel_path)
    public_index = prepare_public_ent_index(load_public_entities())

    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_beneficiaire,
        treating_kwargs={
            "corr_col": cols["field"],
            "ref_col": cols["ref_transaction"],
            "ref": ref,
            "public_index": public_index,
            "api_id": None,          # rempli dynamiquement par CategoricalFieldProcessor.process()
            "cfg": field_cfg,
            "warm_start": True,
            "verbose": False,
        },
        col_in=cols["field"],
        col_out=cols["field_out"],
        ref_banque_col=cols.get("ref_banque", "RefBanque"),
        outlier_tag=field_cfg.get("outlier_tag", "OUTLIER"),
        exclude_suffixes=("_clean", "_method", "_check"),
        clean_fn=clean_label,
        save_warm_start_fn=save_warm_start,
        classification_fn=lambda api_id: build_full_classification_beneficiaire(
            ref, api_id, cols["field"], cols["field_out"]
        ),
    )
