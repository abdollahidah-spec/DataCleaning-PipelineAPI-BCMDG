"""
e10_fe/fields/nomdonneurordre.py
==================================
Normalisation du champ NomDonneurOrdre pour E10_FE (Flux Entrants) — porté depuis
DataCleaning-PipelineField-BCMDG/nomdonneurordre/normalize_nomdonneurordre.py
(fonction `treating_nomdonneurordre_e10`).

Sur un flux ENTRANT, le donneur d'ordre est l'émetteur ÉTRANGER du paiement : la
base fiscale DGI mauritanienne n'est pas pertinente — fallback Claude + recherche
web. C'est l'inverse d'E07 (flux sortants), où le bénéficiaire est étranger et le
donneur d'ordre local. Même socle de nettoyage/classification que Beneficiaire
(e10_fe/fields/_entity_matching.py).

COLONNES AJOUTÉES :
  NomDonneurOrdre_clean       — valeur nettoyée (clean_label)
  NomDonneurOrdre_Normalisé   — nom légal officiel en MAJUSCULES / 'NA' / 'OUTLIER'
  NomDonneurOrdre_method      — 'WARM' / 'MAP_CIBLE' / 'MAP' / 'PUBLIC_ENT' / 'PARTICULIER' /
                                'ETS_PERSONNEL' / 'ETS_OUTLIER' / 'CLAUDE' / 'OUTLIER'
  NomDonneurOrdre_check       — True si OUTLIER

CASCADE (identique à l'ancien repo) :
  1. Warm-start → valeur déjà cible du référentiel (MAP_CIBLE) → référentiel
     → règle NA (raccourci) → entreprise publique → outlier évident (raccourci)
  2. Classification locale (particulier / ETS personnel / ETS outlier)
  3. Fallback Claude (recherche web réelle) → CLAUDE si résolu, sinon OUTLIER

PERSISTANCE DU CACHE CLAUDE : seules les résolutions payantes (recherche web)
sont écrites dans validated_classif_nomdonneurordre_e10_fe.json.

RÈGLE NA — témoin ReferenceTransaction (même convention que les autres champs E10_FE) :
  NomDonneurOrdre == 'NA'  ET  ReferenceTransaction == 'NA'   → 'NA'
  NomDonneurOrdre == 'NA'  ET  ReferenceTransaction != 'NA'   → OUTLIER
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from shared.claude_client import call_claude_beneficiaire_web_batch
from shared.field_processor import CategoricalFieldProcessor
from shared.na_rule import apply_na_rule_frame

from e10_fe.fields._entity_matching import (
    classify_local,
    clean_label,
    est_outlier_evident,
    index_valeurs_cibles,
    load_public_entities,
    match_public_entity,
    prepare_public_ent_index,
)

_REFERENTIEL_DIR = Path(__file__).parent.parent / "referentiel"

# Prompt de l'ancien repo (_SYSTEM_PROMPT_NDO_E10) : identique à celui du
# bénéficiaire étranger d'E07/E08, seul l'objet du libellé change.
_SYSTEM_PROMPT_NDO_E10 = (
    "Tu es un expert en identification d'entreprises (registres du commerce, bases "
    "d'immatriculation officielles, codes SWIFT/BIC) partout dans le monde.\n"
    "Pour chaque libelle brut fourni (nom d'entreprise saisi par un operateur bancaire "
    "mauritanien, souvent abrege, tronque ou mal orthographie), utilise la recherche web "
    "pour determiner s'il designe sans ambiguite une VRAIE entreprise existante, et "
    "retrouver sa raison sociale legale exacte telle que documentee officiellement.\n"
    "REGLES STRICTES :\n"
    "- Si tu identifies l'entreprise avec certitude (source officielle ou fiable trouvee "
    "via la recherche web), reponds avec sa raison sociale legale complete, en MAJUSCULES, "
    "sans commentaire.\n"
    "- Si le libelle est ambigu, trop vague, ou ne correspond a aucune entreprise reelle "
    "verifiable, reponds exactement OUTLIER.\n"
    "- Mefie-toi particulierement des libelles COURTS ou CRYPTIQUES (acronymes de "
    "3-6 lettres, codes sans mot reconnaissable) : de tels libelles correspondent "
    "souvent PAR HASARD a plusieurs entreprises sans rapport entre elles. Ne resous "
    "un tel libelle QUE si une source fiable le lie explicitement et sans ambiguite "
    "a CE libelle precis (ex : un vrai code SWIFT/BIC officiel qui correspond "
    "exactement). Un acronyme plausible mais non verifie doit rester OUTLIER.\n"
    "- Ne reponds JAMAIS par un nom invente, une simple reformulation du libelle brut, "
    "ou une entreprise 'la plus proche' sans lien documente certain : en cas de doute, "
    "OUTLIER. Un faux positif est pire qu'un OUTLIER manque.\n"
    "- Tu reponds UNIQUEMENT avec des lignes au format exact : N. REPONSE\n"
    "- Une ligne par item, dans le meme ordre. Zero explication, zero ligne vide."
)
_USER_INTRO = "Libellés de donneurs d'ordre à identifier"


def load_referentiel(path: str | Path) -> dict:
    """Structure attendue : {"mapping": {"<brut>": "<normalisé>"}}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("mapping", {})


def _warm_start_path(api_id: str) -> Path:
    return _REFERENTIEL_DIR / f"validated_classif_nomdonneurordre_{api_id.lower()}.json"


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
        print(f"  Warm-start NomDonneurOrdre {api_id} : +{len(new_entries)} modalité(s) mise(s) en cache")


def treating_nomdonneurordre(
    df:           pd.DataFrame,
    corr_col:     str  = "NomDonneurOrdre",
    ref_col:      str  = "ReferenceTransaction",
    ref:          dict = None,
    public_index: dict = None,
    api_id:       str  = "E10_FE",
    cfg:          dict = None,
    warm_start:   bool = True,
    verbose:      bool = False,
) -> pd.DataFrame:
    """Normalise la colonne NomDonneurOrdre. Traitement sur valeurs uniques."""
    df = df.copy()

    if ref is None:
        ref = load_referentiel(_REFERENTIEL_DIR / "nomdonneurordre_referentiel_E10.json")
    ref_cibles = index_valeurs_cibles(ref)

    if public_index is None:
        public_index = prepare_public_ent_index(load_public_entities())

    cfg = cfg or {}

    ws_cache: dict = load_warm_start(api_id) if warm_start else {}
    if verbose and warm_start:
        print(f"  Warm-start NomDonneurOrdre {api_id} : {len(ws_cache)} modalités connues")

    unique_vals = df[corr_col].dropna().unique()
    clean_map   = {v: clean_label(v) for v in unique_vals}
    df["NomDonneurOrdre_clean"] = df[corr_col].map(clean_map).fillna("")

    result_map: dict = {}
    to_resolve_claude: dict = {}

    for v in unique_vals:
        clean = clean_map[v]

        if not clean:
            result_map[v] = (None, None); continue

        if warm_start and clean in ws_cache:
            result_map[v] = (ws_cache[clean], "WARM"); continue

        if clean in ref_cibles:
            result_map[v] = (ref_cibles[clean], "MAP_CIBLE"); continue

        if clean in ref:
            result_map[v] = (ref[clean], "MAP"); continue

        if clean.upper() == "NA":
            result_map[v] = ("OUTLIER", "OUTLIER"); continue

        pub_short = match_public_entity(clean, public_index)
        if pub_short is not None:
            result_map[v] = (pub_short, "PUBLIC_ENT"); continue

        if est_outlier_evident(clean):
            result_map[v] = ("OUTLIER", "OUTLIER"); continue

        normalized, method = classify_local(clean)
        if normalized is not None:
            result_map[v] = (normalized, method); continue

        result_map[v] = None
        to_resolve_claude.setdefault(clean, None)

    a_claude   = list(to_resolve_claude.keys())
    batch_size = cfg.get("llm", {}).get("batch_size", 5)
    claude_resultats: dict = {}
    failed_techniquement: set = set()
    total = len(a_claude)
    for debut in range(0, total, batch_size):
        batch_val = a_claude[debut:debut + batch_size]
        reponses  = call_claude_beneficiaire_web_batch(
            batch_val, cfg, system_prompt=_SYSTEM_PROMPT_NDO_E10, user_intro=_USER_INTRO
        )
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

    # Deux dictionnaires plats + Series.map(dict) : la recherche se fait au niveau C
    # de pandas, sans appel Python par ligne.
    df["NomDonneurOrdre_Normalisé"] = df[corr_col].map({k: v[0] for k, v in result_map.items()})
    df["NomDonneurOrdre_method"]    = df[corr_col].map({k: v[1] for k, v in result_map.items()})
    df["_ws_hit"] = df["NomDonneurOrdre_method"] == "WARM"

    if ref_col in df.columns:
        iso, mth = apply_na_rule_frame(
            df, corr_col, ref_col, "NomDonneurOrdre_Normalisé", "NomDonneurOrdre_method"
        )
        df["NomDonneurOrdre_Normalisé"] = iso
        df["NomDonneurOrdre_method"]    = mth

    df["NomDonneurOrdre_check"] = df["NomDonneurOrdre_Normalisé"] == "OUTLIER"
    return df


def build_full_classification_nomdonneurordre(ref: dict, api_id: str, col_in: str, col_out: str) -> pd.DataFrame:
    """Table de classification CUMULATIVE (référentiel + cache warm-start Claude)."""
    combined = dict(ref)
    combined.update(load_warm_start(api_id))

    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_nomdonneurordre_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor NomDonneurOrdre à partir du bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    ref = load_referentiel(Path(field_cfg["referentiel_path"]))
    public_index = prepare_public_ent_index(load_public_entities())

    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_nomdonneurordre,
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
        classification_fn=lambda api_id: build_full_classification_nomdonneurordre(
            ref, api_id, cols["field"], cols["field_out"]
        ),
    )
