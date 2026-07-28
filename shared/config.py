"""
shared/config.py
==================
Chargement et fusion de la configuration d'une API : config_base.yaml (commun
à toutes les APIs) + le YAML spécifique à l'API (ex: e11_rdcc/config/E11_RDCC.yaml).
Les sections dict sont fusionnées en profondeur ; le YAML spécifique surcharge
uniquement les clés qu'il redéfinit.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_BASE_CFG = Path(__file__).parent / "config_base.yaml"


def load_config(config_path: str | Path) -> dict:
    with open(_BASE_CFG, encoding="utf-8") as f:
        base = yaml.safe_load(f)
    with open(config_path, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    cfg = _deep_merge(base, spec)
    cfg["_config_path"] = str(Path(config_path).resolve())
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged
