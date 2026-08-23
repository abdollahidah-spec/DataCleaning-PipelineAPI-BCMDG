"""
e09_pe/apply_corrections.py
===============================
Applique les corrections manuelles saisies dans l'onglet "Instructions" d'un
fichier E09_PE_classification.xlsx vers les caches warm-start des champs
concernés. La colonne "Champ" route chaque ligne vers le bon champ — seul
Devise a un cache warm-start ; Echeances n'a pas de concept de correction
(anomalie de validation, pas de valeur à mapper) et est ignoré avec un avertissement.

La correction prime sur le référentiel dès le run suivant, sans jamais
retoucher le fichier Excel source.

Usage (depuis la racine du repo) :
    python -m e09_pe.apply_corrections --file "e09_pe/outputs/E09_PE_classification.xlsx"
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from shared.config import load_config
from shared.console import force_utf8_console

from e09_pe.pipeline import build_e09_field_processors

_REQUIRED_COLS = {"Champ", "Input", "Label_Attendu"}
_RE_SPACES = re.compile(r"\s+")
_VALID_API_IDS = ("E09_PE",)


def _clean_label_attendu(label) -> str:
    return _RE_SPACES.sub(" ", str(label).strip()).strip().upper()


def apply_corrections(file_path: str | Path, api_id: str, config_path: str | Path) -> dict:
    """Retourne {champ: {clean_input: clean_label}} pour les champs effectivement corrigés."""
    df = pd.read_excel(file_path, sheet_name="Instructions", dtype=str, keep_default_na=False)
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"Colonnes manquantes dans l'onglet Instructions : {missing} "
            f"(colonnes trouvées : {list(df.columns)})"
        )

    cfg = load_config(config_path)
    processors = {p.field_name: p for p in build_e09_field_processors(cfg)}

    applied: dict = {}
    for champ, group in df.groupby("Champ"):
        champ = champ.strip()
        if not champ:
            continue

        processor = processors.get(champ)
        if processor is None:
            print(f"  [WARN] Champ inconnu dans Instructions : {champ!r} — ligne(s) ignorée(s)")
            continue
        if getattr(processor, "clean_fn", None) is None:
            print(f"  [WARN] {champ} n'a pas de cache warm-start (champ numérique ?) — ligne(s) ignorée(s)")
            continue

        corrections: dict = {}
        ignorees = 0
        for _, row in group.iterrows():
            clean_in = processor.clean_fn(row.get("Input", ""))
            clean_lbl = _clean_label_attendu(row.get("Label_Attendu", ""))
            if not clean_in or not clean_lbl:
                if clean_in or clean_lbl:
                    ignorees += 1
                continue
            corrections[clean_in] = clean_lbl

        if corrections:
            processor.apply_correction(api_id, corrections)
        applied[champ] = corrections
        suffix = f", {ignorees} ligne(s) ignorée(s) (incomplète)" if ignorees else ""
        print(f"  [{champ}] {len(corrections)} correction(s) appliquée(s){suffix}")

    return applied


def _guess_api_id(filename: str) -> str | None:
    upper = filename.upper()
    for api_id in _VALID_API_IDS:
        if api_id in upper:
            return api_id
    return None


if __name__ == "__main__":
    force_utf8_console()
    parser = argparse.ArgumentParser(description="Applique les corrections manuelles (onglet Instructions)")
    parser.add_argument("--file", required=True, help="Fichier E09_PE_classification.xlsx")
    parser.add_argument("--api-id", default=None, choices=_VALID_API_IDS)
    parser.add_argument("--config", default="e09_pe/config/E09_PE.yaml")
    args = parser.parse_args()

    resolved_api_id = args.api_id or _guess_api_id(Path(args.file).name)
    if resolved_api_id is None:
        parser.error(f"Impossible de déduire --api-id du nom de fichier — le préciser explicitement "
                      f"parmi {_VALID_API_IDS}")

    apply_corrections(args.file, resolved_api_id, args.config)
