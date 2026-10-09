"""
shared/dgi_cache.py
=====================
Mémoire locale des résolutions DÉTERMINISTES du rapprochement contre la base
fiscale DGI (`DGI_EXACT_NORM`, `DGI_FUZZY_STRONG`, `DGI_NO_MATCH`).

Pourquoi : comparer les libellés nouveaux aux ~52 000 raisons sociales DGI
(rapidfuzz, tous cœurs) est l'étape la plus longue d'un chargement — mesuré le
17/09/2026 : ~25 min pour 8 000 libellés. Ce résultat ne dépend QUE du libellé et
du contenu de la base DGI : le recalculer à chaque run est du temps perdu, en
particulier pendant une recette où la même pipeline est relancée plusieurs fois.

Garde-fou : le fichier porte une EMPREINTE de la base DGI utilisée. Si la base
change (nouvelle livraison DGI), l'empreinte ne correspond plus et tout est
recalculé — jamais de résolution servie à partir d'une base périmée.

Ne sont PAS mémorisés ici :
  - les arbitrages Claude (payants) : ils vont dans le cache warm-start du champ
    (`validated_classif_*.json`), qui a priorité sur tout le reste ;
  - les redirections vers une entreprise publique, recalculées à chaque run
    (elles dépendent d'un autre fichier, `public_ent.xlsx`).

Emplacement : `{STATE_DIR}/dgi_resolution_{api}_{champ}.json` (par défaut
`state/`, non versionné) — donnée dérivée, reproductible, propre à la machine.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VERSION = "1.0.0"

# Seules ces méthodes sont déterministes à base DGI constante.
METHODES_MEMORISABLES = ("DGI_EXACT_NORM", "DGI_FUZZY_STRONG", "DGI_NO_MATCH")


def _dossier() -> Path:
    valeur = os.getenv("STATE_DIR", "").strip()
    return Path(valeur) if valeur else _REPO_ROOT / "state"


def chemin(api_id: str, champ: str) -> Path:
    return _dossier() / f"dgi_resolution_{api_id.lower()}_{champ.lower()}.json"


def empreinte_dgi(dgi_index: dict) -> str:
    """Empreinte stable du contenu DGI utilisé pour le rapprochement."""
    libelles = dgi_index.get("dgi_clean") or []
    total = sum(len(l) for l in libelles)
    return f"{len(libelles)}:{total}"


def charger(api_id: str, champ: str, empreinte: str) -> dict:
    """{libellé nettoyé -> (valeur, méthode)} ; vide si absent ou base DGI différente."""
    p = chemin(api_id, champ)
    if not p.exists():
        return {}
    try:
        data = json.load(open(p, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if data.get("empreinte") != empreinte:
        print(f"  [DGI] base fiscale différente de celle du cache local ({p.name}) : "
              f"rapprochement recalculé.")
        return {}
    return {label: (valeur, methode) for label, (valeur, methode) in data.get("resolutions", {}).items()}


def enregistrer(api_id: str, champ: str, empreinte: str, resolutions: dict) -> int:
    """Écrit les résolutions mémorisables ; retourne le nombre d'entrées écrites."""
    retenues = {label: [valeur, methode] for label, (valeur, methode) in resolutions.items()
                if methode in METHODES_MEMORISABLES}
    if not retenues:
        return 0
    p = chemin(api_id, champ)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"version": _VERSION, "empreinte": empreinte, "resolutions": retenues},
                  f, ensure_ascii=False, indent=2, sort_keys=True)
    return len(retenues)
