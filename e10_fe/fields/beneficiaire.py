"""
e10_fe/fields/beneficiaire.py
===============================
Normalisation du champ Beneficiaire pour E10_FE (Flux Entrants) — porté depuis
DataCleaning-PipelineField-BCMDG/beneficiaire/normalize_beneficiaire.py
(fonction `treating_beneficiaire_dgi`).

Sur un flux ENTRANT, le bénéficiaire est l'entité LOCALE qui reçoit les fonds :
matching contre la base fiscale DGI (NIF exact, puis rapidfuzz + arbitrage
Claude). C'est l'inverse d'E07 (flux sortants), où ce matching DGI s'applique au
donneur d'ordre.

COLONNES AJOUTÉES :
  Beneficiaire_clean       — valeur nettoyée (clean_label)
  Beneficiaire_Normalisé   — nom légal officiel en MAJUSCULES / 'NA' / 'OUTLIER'
  Beneficiaire_method      — voir méthodes ci-dessous
  Beneficiaire_check       — True si OUTLIER

MÉTHODES POSSIBLES :
  WARM                  — cache validated_classif (résolution Claude passée)
  MAP_CIBLE             — le libellé EST déjà un nom légal cible du référentiel
  MAP                   — référentiel Excel validé BCM
  NIF_EXACT             — NifNni valide trouvé tel quel dans la base DGI
  PUBLIC_ENT            — entreprise publique reconnue (public_ent.xlsx)
  PARTICULIER           — nom propre d'un particulier
  ETS_PERSONNEL         — 'ETS/Etablissement + nom propre'
  ETS_OUTLIER           — 'ETS + ...' mais le reste n'est pas un nom propre clair
  DGI_EXACT_NORM        — match exact après hyper-normalisation contre la DGI
  DGI_FUZZY_STRONG      — match rapidfuzz net (score fort + écart net avec le 2e)
  DGI_CLAUDE_ARBITRAGE  — candidats DGI proches, tranché par Claude
  DGI_NO_MATCH          — aucun candidat DGI plausible, pas d'appel Claude
  OUTLIER               — non résolu / valeur de bruit / échec technique Claude

CASCADE (identique à l'ancien repo) :
  1. Warm-start → MAP_CIBLE → référentiel → règle NA (raccourci) → NIF exact (DGI)
     → entreprise publique → outlier évident (raccourci)
  2. Classification locale (particulier / ETS personnel / ETS outlier)
  3. Matching DGI : exact-normalisé → rapidfuzz (fort = direct, faible = OUTLIER,
     ambigu = arbitrage Claude)
  4. Redirection entreprise publique si le match DGI final en désigne une

PERSISTANCE DU CACHE CLAUDE : seules les résolutions d'arbitrage (payantes) sont
écrites dans validated_classif_beneficiaire_e10_fe.json — les résolutions
déterministes sont recalculées à chaque run.

RÈGLE NA — témoin ReferenceTransaction (même convention que les autres champs E10_FE) :
  Beneficiaire == 'NA'  ET  ReferenceTransaction == 'NA'   → 'NA'
  Beneficiaire == 'NA'  ET  ReferenceTransaction != 'NA'   → OUTLIER
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from shared.claude_client import call_claude_dgi_arbitrage_batch
from shared.field_processor import CategoricalFieldProcessor
from shared.na_rule import apply_na_rule_frame

from e10_fe.fields._entity_matching import (
    classify_local,
    clean_label,
    est_outlier_evident,
    hyper_normaliser,
    index_valeurs_cibles,
    load_dgi_base,
    load_public_entities,
    match_public_entity,
    nettoyer_nifnni,
    nif_valide,
    prepare_dgi_index,
    prepare_public_ent_index,
    _dgi_fuzzy_batch,
)

_REFERENTIEL_DIR = Path(__file__).parent.parent / "referentiel"

# Prompt de l'ancien repo (_SYSTEM_PROMPT_BENEF_DGI_ARBITRAGE).
_SYSTEM_PROMPT_BENEF_DGI_ARBITRAGE = (
    "Tu es un expert du registre fiscal mauritanien (DGI - Direction Generale des Impots).\n"
    "Pour chaque item, on te donne un libelle designant le BENEFICIAIRE d'un flux entrant, "
    "saisi par un operateur bancaire, et une liste courte (2-3) de raisons sociales "
    "candidates issues de la base DGI qui lui ressemblent textuellement, avec leur NIF "
    "et forme juridique.\n"
    "Determine SI ET SEULEMENT SI le libelle designe sans ambiguite l'une de ces raisons "
    "sociales candidates (la MEME entite legale, pas seulement un nom proche ou une entite "
    "homonyme differente).\n"
    "REGLES STRICTES :\n"
    "- Si un candidat correspond avec certitude, reponds avec son numero (1, 2 ou 3).\n"
    "- Si aucun candidat ne correspond avec certitude, ou si plusieurs candidats sont "
    "plausibles sans element distinctif suffisant pour trancher (homonymie, cas ambigu), "
    "reponds exactement OUTLIER. Ne devine JAMAIS.\n"
    "- Tu reponds UNIQUEMENT avec des lignes au format exact : N. REPONSE (ou REPONSE est "
    "un numero de candidat ou OUTLIER).\n"
    "- Une ligne par item, dans le meme ordre. Zero explication, zero ligne vide."
)


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
    ref_col:      str  = "ReferenceTransaction",
    nif_col:      str  = "NifNni",
    ref:          dict = None,
    dgi_index:    dict = None,
    public_index: dict = None,
    api_id:       str  = "E10_FE",
    cfg:          dict = None,
    warm_start:   bool = True,
    verbose:      bool = False,
) -> pd.DataFrame:
    """Normalise la colonne Beneficiaire. Traitement sur paires (label, nif) uniques."""
    df = df.copy()

    if ref is None:
        ref = load_referentiel(_REFERENTIEL_DIR / "beneficiaire_referentiel_E10.json")
    ref_cibles = index_valeurs_cibles(ref)

    if dgi_index is None:
        dgi_index = prepare_dgi_index(load_dgi_base())

    if public_index is None:
        public_index = prepare_public_ent_index(load_public_entities())

    cfg = cfg or {}
    matching_cfg     = cfg.get("matching", {})
    strong_threshold = matching_cfg.get("strong_threshold", 92)
    strong_gap       = matching_cfg.get("strong_gap", 8)
    arbitrage_min    = matching_cfg.get("arbitrage_min", 70)
    fuzzy_batch_size = matching_cfg.get("batch_size", 300)

    ws_cache: dict = load_warm_start(api_id) if warm_start else {}
    if verbose and warm_start:
        print(f"  Warm-start Beneficiaire {api_id} : {len(ws_cache)} modalités connues")

    # Nettoyage sur valeurs uniques puis Series.map(dict) : jamais d'appel Python
    # par ligne sur les ~3 M de lignes de la table.
    labels_uniques = df[corr_col].dropna().unique()
    label_clean_col = df[corr_col].map({v: clean_label(v) for v in labels_uniques}).fillna("")
    df["Beneficiaire_clean"] = label_clean_col
    if nif_col in df.columns:
        nifs_uniques = df[nif_col].dropna().unique()
        nif_clean_col = df[nif_col].map({v: nettoyer_nifnni(v) for v in nifs_uniques}).fillna("")
    else:
        nif_clean_col = pd.Series([""] * len(df), index=df.index)

    unique_pairs = pd.DataFrame({"label": label_clean_col, "nif": nif_clean_col}).drop_duplicates()

    result_by_pair: dict = {}
    to_fuzzy: dict = {}  # dict utilisé comme set ordonné (clé = libellé nettoyé)

    for label, nif in unique_pairs.itertuples(index=False):
        key = (label, nif)

        if not label:
            result_by_pair[key] = (None, None); continue

        if warm_start and label in ws_cache:
            result_by_pair[key] = (ws_cache[label], "WARM"); continue

        if label in ref_cibles:
            result_by_pair[key] = (ref_cibles[label], "MAP_CIBLE"); continue

        if label in ref:
            result_by_pair[key] = (ref[label], "MAP"); continue

        if label.upper() == "NA":
            result_by_pair[key] = ("OUTLIER", "OUTLIER"); continue

        if nif and nif_valide(nif) and nif in dgi_index["nif_index"]:
            result_by_pair[key] = (dgi_index["nif_index"][nif], "NIF_EXACT"); continue

        pub_short = match_public_entity(label, public_index)
        if pub_short is not None:
            result_by_pair[key] = (pub_short, "PUBLIC_ENT"); continue

        if est_outlier_evident(label):
            result_by_pair[key] = ("OUTLIER", "OUTLIER"); continue

        normalized, method = classify_local(label)
        if normalized is not None:
            result_by_pair[key] = (normalized, method); continue

        result_by_pair[key] = None
        to_fuzzy.setdefault(label, None)

    # ── Matching DGI sur les libellés uniques restants ──────────────────────────
    fuzzy_labels = list(to_fuzzy.keys())
    label_resolution: dict = {}
    still_fuzzy: list = []

    for lab in fuzzy_labels:
        hn = hyper_normaliser(lab)
        if hn and hn in dgi_index["dgi_exact"]:
            label_resolution[lab] = (dgi_index["dgi_exact"][hn], "DGI_EXACT_NORM")
        else:
            still_fuzzy.append(lab)

    fuzzy_scores = _dgi_fuzzy_batch(still_fuzzy, dgi_index, batch_size=fuzzy_batch_size, top_k=3)

    a_arbitrage: dict = {}  # label -> [(dgi_clean_label, score), ...]
    for lab in still_fuzzy:
        candidats = fuzzy_scores.get(lab, [])
        if not candidats:
            label_resolution[lab] = ("OUTLIER", "DGI_NO_MATCH"); continue

        top1_lab, top1_score = candidats[0]
        top2_score = candidats[1][1] if len(candidats) > 1 else 0.0
        gap = top1_score - top2_score

        if top1_score >= strong_threshold and gap >= strong_gap:
            label_resolution[lab] = (dgi_index["clean_to_orig"][top1_lab], "DGI_FUZZY_STRONG")
        elif top1_score < arbitrage_min:
            label_resolution[lab] = ("OUTLIER", "DGI_NO_MATCH")
        else:
            a_arbitrage[lab] = candidats[:3]

    # ── Arbitrage Claude sur les cas ambigus (candidats proches) ────────────────
    keys = list(a_arbitrage.keys())
    items = []
    for lab in keys:
        cand_meta = []
        for cand_clean, _score in a_arbitrage[lab]:
            meta = dgi_index["clean_to_meta"].get(cand_clean, {})
            cand_meta.append({
                "raison_sociale":  dgi_index["clean_to_orig"].get(cand_clean, cand_clean),
                "nif":             meta.get("nif", ""),
                "forme_juridique": meta.get("forme_juridique", ""),
            })
        items.append({"label": lab, "candidates": cand_meta})

    arbitrage_batch_size = cfg.get("llm", {}).get("batch_size", 20)
    arbitrage_resultats: dict = {}
    failed_techniquement: set = set()
    total = len(keys)
    for debut in range(0, total, arbitrage_batch_size):
        batch_keys  = keys[debut:debut + arbitrage_batch_size]
        batch_items = items[debut:debut + arbitrage_batch_size]
        reponses    = call_claude_dgi_arbitrage_batch(
            batch_items, cfg, system_prompt=_SYSTEM_PROMPT_BENEF_DGI_ARBITRAGE
        )
        if reponses is None:
            print(f"  [CLAUDE] échec technique sur le batch d'arbitrage {debut}-{debut + len(batch_keys)} "
                  f"-> OUTLIER temporaire (non mis en cache, réessayé au prochain run)")
            for lab in batch_keys:
                failed_techniquement.add(lab)
            continue
        for k, lab in enumerate(batch_keys):
            arbitrage_resultats[lab] = reponses[k]
        if verbose:
            print(f"  [CLAUDE arbitrage DGI] {min(debut + arbitrage_batch_size, total)}/{total} modalités", end="\r")
    if total and verbose:
        print()

    for lab in keys:
        if lab in failed_techniquement:
            label_resolution[lab] = ("OUTLIER", "OUTLIER")
            continue
        idx = arbitrage_resultats.get(lab)
        if idx is None:
            label_resolution[lab] = ("OUTLIER", "DGI_CLAUDE_ARBITRAGE")
            continue
        candidats = a_arbitrage[lab]
        if 1 <= idx <= len(candidats):
            chosen_clean = candidats[idx - 1][0]
            label_resolution[lab] = (dgi_index["clean_to_orig"][chosen_clean], "DGI_CLAUDE_ARBITRAGE")
        else:
            label_resolution[lab] = ("OUTLIER", "DGI_CLAUDE_ARBITRAGE")

    # ── Redirection entreprises publiques ────────────────────────────────────────
    for lab, (val, meth) in list(label_resolution.items()):
        if val != "OUTLIER" and meth in ("DGI_EXACT_NORM", "DGI_FUZZY_STRONG", "DGI_CLAUDE_ARBITRAGE"):
            pub_short = match_public_entity(clean_label(val), public_index)
            if pub_short is not None:
                label_resolution[lab] = (pub_short, "PUBLIC_ENT")

    cacheable = {
        lab: label_resolution[lab][0]
        for lab in keys
        if lab not in failed_techniquement
    }
    if cacheable:
        save_warm_start(api_id, cacheable, verbose=verbose)

    # ── Assemblage final (jointure sur les paires uniques, pas de boucle par ligne) ─
    for key, val in result_by_pair.items():
        if val is None:
            result_by_pair[key] = label_resolution.get(key[0], ("OUTLIER", "OUTLIER"))

    resolution = pd.DataFrame(
        [(lab, nif, v[0], v[1]) for (lab, nif), v in result_by_pair.items()],
        columns=["label", "nif", "Beneficiaire_Normalisé", "Beneficiaire_method"],
    ).astype({"label": object, "nif": object})
    lignes = pd.DataFrame({"label": label_clean_col.astype(object).to_numpy(),
                           "nif": nif_clean_col.astype(object).to_numpy()})
    joint = lignes.merge(resolution, on=["label", "nif"], how="left")
    df["Beneficiaire_Normalisé"] = joint["Beneficiaire_Normalisé"].to_numpy()
    df["Beneficiaire_method"]    = joint["Beneficiaire_method"].to_numpy()
    df["_ws_hit"] = df["Beneficiaire_method"] == "WARM"

    if ref_col in df.columns:
        iso, mth = apply_na_rule_frame(
            df, corr_col, ref_col, "Beneficiaire_Normalisé", "Beneficiaire_method"
        )
        df["Beneficiaire_Normalisé"] = iso
        df["Beneficiaire_method"]    = mth

    df["Beneficiaire_check"] = df["Beneficiaire_Normalisé"] == "OUTLIER"
    return df


def build_full_classification_beneficiaire(ref: dict, api_id: str, col_in: str, col_out: str) -> pd.DataFrame:
    """Table de classification CUMULATIVE (référentiel + cache warm-start Claude
    d'arbitrage) — les résolutions DGI déterministes ne sont pas incluses : elles
    sont recalculées à chaque run à partir des données du run lui-même."""
    combined = dict(ref)
    combined.update(load_warm_start(api_id))

    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_beneficiaire_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor Beneficiaire à partir du bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    ref = load_referentiel(Path(field_cfg["referentiel_path"]))
    dgi_index = prepare_dgi_index(load_dgi_base())
    public_index = prepare_public_ent_index(load_public_entities())

    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_beneficiaire,
        treating_kwargs={
            "corr_col": cols["field"],
            "ref_col": cols["ref_transaction"],
            "nif_col": cols.get("nif_nni", "NifNni"),
            "ref": ref,
            "dgi_index": dgi_index,
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
