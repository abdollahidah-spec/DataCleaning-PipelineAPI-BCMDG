"""
shared/corrections_history.py
===============================
Historique des corrections manuelles appliquées via `{api}/apply_corrections.py`
— traçabilité de qui a corrigé quoi, et quand.

UN FICHIER PAR API (jamais partagé entre APIs, comme les caches warm-start) :
    {api_package}/referentiel/corrections_history_{api_id}.json

Versionné avec le référentiel (et non dans `state/`, gitignored) : l'historique
des validations métier fait partie de la connaissance livrée avec le repo, au
même titre que les caches warm-start `validated_classif_*.json`.

C'est ce contenu qui alimente l'onglet "Instructions" du classeur de sortie, en
LECTURE SEULE : vide au tout premier run (personne n'a encore rien corrigé),
puis il s'enrichit à chaque `apply_corrections`. Ce n'est donc PAS le fichier à
remplir pour soumettre des corrections — `apply_corrections.py` accepte n'importe
quel fichier Excel comportant un onglet "Instructions" (Champ/Input/Label_Attendu).

Format :
    {"version": "1.0.0",
     "corrections": [
        {"date": "2026-08-29T14:32:11", "champ": "Devise",
         "input": "XYZ", "label_attendu": "EUR"}, ...]}
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

HISTORY_COLS = ["Date", "Champ", "Input", "Label_Attendu"]
_VERSION = "1.0.0"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def history_path(api_id: str) -> Path:
    """
    `{api_package}/referentiel/corrections_history_{api_id}.json`. Le nom du
    package d'une API est son api_id en minuscules (E11_RDCC -> e11_rdcc/) —
    même convention que les caches warm-start (voir
    e11_rdcc/fields/nomcorrespondant.py::_warm_start_path).
    """
    slug = api_id.lower()
    return _REPO_ROOT / slug / "referentiel" / f"corrections_history_{slug}.json"


def load_history(api_id: str) -> list[dict]:
    """Liste des corrections déjà appliquées (ordre chronologique) ; [] si aucune."""
    path = history_path(api_id)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Un historique illisible ne doit jamais faire échouer un run : c'est un
        # affichage de traçabilité, pas une donnée dont dépend le traitement.
        return []
    corrections = data.get("corrections", [])
    return corrections if isinstance(corrections, list) else []


def load_history_df(api_id: str) -> pd.DataFrame:
    """Historique au format de l'onglet "Instructions" — DataFrame vide (colonnes
    seules) si aucune correction n'a encore jamais été appliquée pour cette API."""
    rows = [
        {
            "Date": entry.get("date", ""),
            "Champ": entry.get("champ", ""),
            "Input": entry.get("input", ""),
            "Label_Attendu": entry.get("label_attendu", ""),
        }
        for entry in load_history(api_id)
    ]
    return pd.DataFrame(rows, columns=HISTORY_COLS)


def _write(path: Path, corrections: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": _VERSION, "corrections": corrections}

    # Écriture atomique (fichier temporaire + remplacement), même garde que
    # shared/state_store.py : un historique de validations métier ne doit jamais
    # être corrompu par une interruption en cours d'écriture.
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def append_corrections(api_id: str, applied: dict[str, dict]) -> int:
    """
    Ajoute à l'historique les corrections effectivement appliquées.
    `applied` : {champ: {input_nettoyé: label_attendu}} (sortie d'apply_corrections).

    Une correction strictement identique (même champ, même input, même label)
    qu'une entrée déjà présente n'est PAS ré-enregistrée — relancer
    apply_corrections sur le même fichier ne duplique donc pas l'historique.
    Une correction qui CHANGE le label d'un input déjà corrigé est en revanche
    bien ajoutée : c'est précisément la traçabilité recherchée.

    Retourne le nombre d'entrées réellement ajoutées.
    """
    existing = load_history(api_id)
    seen = {(e.get("champ"), e.get("input"), e.get("label_attendu")) for e in existing}

    stamp = datetime.now().isoformat(timespec="seconds")
    added = []
    for champ, corrections in sorted(applied.items()):
        for input_value, label in sorted(corrections.items()):
            if (champ, input_value, label) in seen:
                continue
            added.append({"date": stamp, "champ": champ, "input": input_value, "label_attendu": label})
            seen.add((champ, input_value, label))

    if added:
        _write(history_path(api_id), existing + added)
    return len(added)
