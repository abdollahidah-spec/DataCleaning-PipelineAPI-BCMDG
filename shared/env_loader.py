"""
shared/env_loader.py
======================
Chargement AUTORITAIRE du fichier .env, à appeler UNE SEULE FOIS au tout début
d'un point d'entrée exécutable ({api}/run_pipeline.py, {api}/apply_corrections.py,
scripts/seed_state.py) — avant tout autre import applicatif.

Pourquoi `override=True` : sans lui, python-dotenv n'écrase pas une variable déjà
présente dans l'environnement du process. Une valeur périmée restée sur la machine
(variable système/utilisateur Windows définie un jour pour un test) primait alors
silencieusement sur .env — bug réel constaté en recette : le mot de passe modifié
dans .env était ignoré, la pipeline continuait à se connecter avec l'ancien.

Pourquoi ICI et pas dans un module de bibliothèque : un `override=True` exécuté à
l'import d'un module (ou à chaque construction de pipeline) réécrit l'environnement
à un moment imprévisible, y compris après qu'un appelant légitime — un test qui
isole OUTPUT_BASE/STATE_DIR vers un dossier temporaire, par exemple — a
volontairement posé sa propre valeur. Le point d'entrée est le seul endroit où
"le .env fait foi" est vrai sans ambiguïté : rien ne s'est encore exécuté.
"""
from __future__ import annotations


def load_env_authoritative() -> None:
    """Charge .env en faisant primer son contenu sur l'environnement existant."""
    from dotenv import load_dotenv

    load_dotenv(override=True)
