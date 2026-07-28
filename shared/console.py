"""
shared/console.py
===================
Force stdout/stderr en UTF-8 dès le démarrage des CLI.

Sur Windows, la console par défaut (cp1252/cp850 selon la locale du poste)
ne peut pas encoder tous les caractères utilisés dans les messages (accents,
symboles) — sans ce correctif, un simple print() peut faire planter un run
planifié (Task Scheduler) sans que personne ne le voie avant le lundi suivant.
"""
from __future__ import annotations

import sys


def force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
