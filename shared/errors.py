"""
shared/errors.py
==================
Exceptions dédiées, avec message CLAIR et EXPLICITE (nature du problème +
données concernées) — pour qu'on comprenne l'erreur sans avoir à interpréter
une trace Python brute. Chaque exception porte une `category` courte, utilisée
par l'email KO (jamais le message technique brut dans le corps de l'email,
voir shared/email_notifier.py — seuls les logs contiennent le détail complet).
"""
from __future__ import annotations


class PipelineError(Exception):
    category = "Erreur inattendue lors du traitement"


class ConfigError(PipelineError):
    category = "Fichier de configuration invalide ou incomplet"


class DataSourceError(PipelineError):
    category = "Impossible d'accéder aux données source"
