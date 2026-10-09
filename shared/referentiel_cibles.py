"""
shared/referentiel_cibles.py
==============================
Index inverse d'un référentiel : {valeur cible nettoyée -> valeur cible d'origine}.

Un référentiel ne contient que des paires {libellé brut -> valeur normalisée}. Un
libellé qui EST DÉJÀ la valeur normalisée n'en est donc pas une clé : sans cet
index, il partait en résolution payante (Claude) ou coûteuse (rapprochement DGI)
et pouvait revenir OUTLIER, puis être mis en cache définitivement — cas réel de
l'ancien repo : « BRED BANQUE POPULAIRE » sur E11_RDCC, 4 363 lignes.

Cette résolution (méthode `MAP_CIBLE`) intervient juste APRÈS le cache warm-start :
une correction manuelle garde donc la priorité (cf. `{api}/apply_corrections.py`).

Chaque champ passe sa propre fonction de nettoyage : c'est elle qui définit la
forme comparée (`clean_label` pour les entités, `clean_nomcorrespondant` pour les
banques correspondantes, `clean_produits` pour les produits...).
"""
from __future__ import annotations

from typing import Callable


def index_valeurs_cibles(ref: dict, clean_fn: Callable[[str], str]) -> dict:
    """
    {clean_fn(valeur cible).upper() -> valeur cible}, hors OUTLIER et valeurs vides.

    Clés en MAJUSCULES, donc comparaison insensible à la casse : certains champs
    conservent volontairement la casse de la valeur brute (`clean_nomcorrespondant`),
    et « Bred Banque Populaire » désigne bien « BRED BANQUE POPULAIRE ». Les
    appelants comparent donc `clean.upper()` à cet index.
    """
    index: dict = {}
    for valeur in ref.values():
        if not valeur or valeur == "OUTLIER":
            continue
        cle = clean_fn(valeur).upper()
        if cle and cle not in index:
            index[cle] = valeur
    return index
