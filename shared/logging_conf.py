"""
shared/logging_conf.py
========================
Logs structurés (console + fichier) pour chaque run de pipeline. Couvre les
champs demandés par le ticket : début, fin, durée, endpoint, champ traité,
rapport de qualité, erreurs, warnings.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"


def get_logger(api_id: str, log_dir: str | Path = "logs") -> logging.Logger:
    logger = logging.getLogger(f"pipeline.{api_id}")
    if logger.handlers:
        return logger  # déjà configuré (évite les doublons de handlers sur ré-appel)

    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logger.setLevel(level)

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(log_path / f"{api_id}_{ts}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
