"""
e08_ocd/fields/produits.py
==============================
Normalisation du champ Produits pour E08_OCD — champ NOUVEAU (pas de précédent
direct dans l'ancien repo field-based), construit sur le référentiel métier
`req/DataCleaning_E08 OCD_Produits.xlsx` (feuilles RefProduits/mapping_table/Outlier,
converti une fois en JSON par un script de migration ponctuel — voir
e08_ocd/referentiel/produits_referentiel.json).

Même PRINCIPE que nature_economique dans l'ancien repo (demande explicite) :
valeur du delta déjà classée -> résolution directe (référentiel/cache) ; valeur
nouvelle/inconnue -> API Claude pour la faire correspondre à l'une des valeurs
de référence (`shared/claude_client.py::call_claude_match_batch`, liste FERMÉE
de labels — Claude choisit dans la liste ou répond OUTLIER, jamais une valeur
inventée). Contrairement à nature_economique, PAS d'embedding sémantique local
(mpnet) ni d'Ollama — uniquement Claude, cohérent avec le reste du repo.

COLONNES AJOUTÉES :
  Produit_clean       — valeur nettoyée (accents supprimés, espaces réduits, majuscules)
  Produit_Normalisé   — Libelle du référentiel / 'NA' / 'OUTLIER'
  Produit_Categorie   — Categorie associée au Libelle normalisé (lookup, vide si NA/OUTLIER)
  Produit_method       — 'MAP' / 'NOISE' / 'WARM' / 'CLAUDE' / 'NA' / 'OUTLIER'
  Produit_check        — True si OUTLIER

RÈGLE NA — témoin NumCredoc (même convention que les autres champs E08_OCD) :
  Produits == 'NA'  ET  NumCredoc == 'NA'   → 'NA'
  Produits == 'NA'  ET  NumCredoc != 'NA'   → OUTLIER
  Produits vide / null                       → OUTLIER
  Valeur non identifiée (Claude inclus)      → OUTLIER
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from shared.claude_client import call_claude_match_batch
from shared.field_processor import CategoricalFieldProcessor
from shared.na_rule import apply_na_rule

_REFERENTIEL_DIR = Path(__file__).parent.parent / "referentiel"

_SYSTEM_PROMPT_TEMPLATE = (
    "Tu es un expert BCM (Banque Centrale de Mauritanie) spécialisé dans la classification "
    "des produits importés/exportés dans le cadre des crédits documentaires (OCD).\n"
    "Voici la liste FERMÉE des libellés de produits valides (référentiel officiel) :\n"
    "{liste}\n"
    "RÈGLES STRICTES :\n"
    "- Tu choisis TOUJOURS un libellé PARMI CETTE LISTE EXACTE, sans exception — jamais "
    "un libellé inventé, reformulé ou hors liste.\n"
    "- Même si la valeur brute est abrégée, tronquée, en anglais ou mal orthographiée, "
    "tu identifies le libellé sémantiquement le plus proche dans la liste.\n"
    "- Si la valeur est trop vague ou ambiguë pour appartenir clairement à un produit "
    "précis de la liste, réponds OUTLIER.\n"
    "- Tu réponds UNIQUEMENT avec des lignes au format exact : N. LIBELLE\n"
    "- Une ligne par item, dans le même ordre. Zéro explication, zéro ligne vide."
)


@dataclass
class ProduitsReferentiel:
    version:    str
    categories: dict = field(default_factory=dict)   # {Categorie: [Libelle, ...]}
    aliases:    dict = field(default_factory=dict)    # {NORM(brut): Libelle}
    noise:      set  = field(default_factory=set)     # {NORM(brut), ...}
    libelle_vers_categorie: dict = field(default_factory=dict)
    all_libelles: list = field(default_factory=list)


def _norm_key(s) -> str:
    """Normalisation robuste pour le lookup (accents supprimés, espaces réduits,
    majuscules) — identique à celle utilisée pour construire le référentiel
    (voir script de migration), pour que brut DB et clés JSON matchent."""
    s = str(s).strip()
    nfkd = unicodedata.normalize("NFKD", s)
    sans_acc = "".join(c for c in nfkd if not unicodedata.combining(c))
    sans_acc = re.sub(r"\s+", " ", sans_acc).strip()
    return sans_acc.upper()


def clean_produits(raw) -> str:
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    return _norm_key(s)


def load_produits_referentiel(path: str | Path) -> ProduitsReferentiel:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    categories = data.get("categories", {})
    libelle_vers_categorie = {
        lib: cat for cat, libs in categories.items() for lib in libs
    }
    return ProduitsReferentiel(
        version=data.get("version", "unknown"),
        categories=categories,
        aliases=data.get("aliases", {}),
        noise=set(data.get("known_noise", [])),
        libelle_vers_categorie=libelle_vers_categorie,
        all_libelles=list(libelle_vers_categorie.keys()),
    )


def _warm_start_path(api_id: str) -> Path:
    return _REFERENTIEL_DIR / f"validated_classif_produits_{api_id.lower()}.json"


def load_warm_start_produits(api_id: str) -> dict:
    path = _warm_start_path(api_id)
    if not path.exists():
        return {}
    return json.load(open(path, encoding="utf-8")).get("classif", {})


def save_warm_start_produits(api_id: str, new_entries: dict, verbose: bool = False) -> None:
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
        print(f"  Warm-start Produits {api_id} : +{len(new_entries)} modalité(s) mise(s) en cache")


def treating_produits(
    df:          pd.DataFrame,
    produit_col: str                   = "Produits",
    ref_col:     str                   = "NumCredoc",
    ref:         ProduitsReferentiel   = None,
    api_id:      str                   = "E08_OCD",
    cfg:         dict                  = None,
    warm_start:  bool                  = True,
    verbose:     bool                  = False,
) -> pd.DataFrame:
    """Normalise la colonne Produits. Traitement sur valeurs uniques."""
    df = df.copy()

    if ref is None:
        ref = load_produits_referentiel(_REFERENTIEL_DIR / "produits_referentiel.json")

    if cfg is None:
        cfg = {}

    ws_cache: dict = {}
    if warm_start:
        ws_cache = load_warm_start_produits(api_id)
        if verbose:
            print(f"  Warm-start Produits {api_id} : {len(ws_cache)} modalités connues")

    unique_vals = df[produit_col].dropna().unique()
    clean_map   = {v: clean_produits(v) for v in unique_vals}
    df["Produit_clean"] = df[produit_col].map(clean_map).fillna("")

    result_map: dict = {}          # v -> (libelle_ou_OUTLIER_ou_NA, method)
    to_resolve_claude: dict = {}

    for v in unique_vals:
        clean = clean_map[v]

        if not clean:
            result_map[v] = (None, None)
            continue

        if clean == "NA":
            result_map[v] = ("OUTLIER", "OUTLIER")   # corrigé par apply_na_rule si Ref aussi NA
            continue

        if warm_start and clean in ws_cache:
            result_map[v] = (ws_cache[clean], "WARM")
            continue

        if clean in ref.noise:
            result_map[v] = ("OUTLIER", "NOISE")
            continue

        if clean in ref.aliases:
            result_map[v] = (ref.aliases[clean], "MAP")
            continue

        result_map[v] = None
        to_resolve_claude.setdefault(clean, None)

    a_claude   = list(to_resolve_claude.keys())
    batch_size = cfg.get("llm", {}).get("batch_size", 20)
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        liste="\n".join(f"- {l}" for l in ref.all_libelles)
    )
    claude_resultats: dict = {}
    failed_techniquement: set = set()
    total = len(a_claude)
    for debut in range(0, total, batch_size):
        batch_val = a_claude[debut:debut + batch_size]
        reponses  = call_claude_match_batch(batch_val, ref.all_libelles, system_prompt, cfg)
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
        save_warm_start_produits(api_id, cacheable, verbose=verbose)

    for v in unique_vals:
        if result_map[v] is None:
            lbl = claude_resultats.get(clean_map[v])
            result_map[v] = (lbl, "CLAUDE") if lbl else ("OUTLIER", "OUTLIER")

    df["Produit_Normalisé"] = df[produit_col].map(lambda v: result_map.get(v, (None, None))[0])
    df["Produit_method"]    = df[produit_col].map(lambda v: result_map.get(v, (None, None))[1])
    df["_ws_hit"] = df["Produit_method"] == "WARM"

    if ref_col in df.columns:
        fixed = df.apply(
            lambda row: apply_na_rule(
                row, produit_col, ref_col, "Produit_Normalisé", "Produit_method"
            ),
            axis=1,
            result_type="expand",
        )
        df["Produit_Normalisé"] = fixed[0]
        df["Produit_method"]    = fixed[1]

    df["Produit_Categorie"] = df["Produit_Normalisé"].map(
        lambda v: ref.libelle_vers_categorie.get(v, "")
    )
    df["Produit_check"] = df["Produit_Normalisé"] == "OUTLIER"
    return df


def build_full_classification_produits(ref: ProduitsReferentiel, api_id: str, col_in: str, col_out: str) -> pd.DataFrame:
    """
    Table de classification CUMULATIVE (référentiel + cache warm-start), PAS les
    lignes du run en cours — même raisonnement que Devise/NomCorrespondant (mode
    incrémental, chemin stable écrasé à chaque run).
    """
    combined: dict = dict(ref.aliases)
    combined.update({v: "OUTLIER" for v in ref.noise})
    combined.update(load_warm_start_produits(api_id))

    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_produits_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor Produits à partir du bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    referentiel_path = Path(field_cfg["referentiel_path"])
    ref = load_produits_referentiel(referentiel_path)

    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_produits,
        treating_kwargs={
            "produit_col": cols["field"],
            "ref_col": cols["ref_transaction"],
            "ref": ref,
            "api_id": None,          # rempli dynamiquement par CategoricalFieldProcessor.process()
            "cfg": field_cfg,
            "warm_start": True,
            "verbose": False,
        },
        col_in=cols["field"],
        col_out=cols["field_out"],
        ref_banque_col=cols.get("ref_banque", "RefBanque"),
        outlier_tag=field_cfg.get("outlier_tag", "OUTLIER"),
        exclude_suffixes=("_clean", "_method", "_check", "_Categorie"),
        clean_fn=clean_produits,
        save_warm_start_fn=save_warm_start_produits,
        classification_fn=lambda api_id: build_full_classification_produits(
            ref, api_id, cols["field"], cols["field_out"]
        ),
    )
