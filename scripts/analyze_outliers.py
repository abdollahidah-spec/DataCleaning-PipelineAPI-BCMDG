"""
scripts/analyze_outliers.py
==============================
Diagnostic à lancer APRÈS un run réel, sur le classeur de sortie
(E11_RDCC_classification.xlsx), pour comprendre la répartition des outliers
plutôt que de juger un taux global seul :

  - NomCorrespondant / Devise : distingue les valeurs déjà tranchées par le
    référentiel BCM statique (validé à l'avance, jamais un problème) de celles
    tranchées par Claude en OUTLIER (déjà traité, mais éventuellement à
    survoler de temps en temps si le volume surprend). Il ne peut PAS y avoir
    de troisième catégorie "jamais vu" ici : le classeur de classification est
    construit uniquement à partir du référentiel + du cache, donc toute valeur
    qui y figure a nécessairement déjà été tranchée par l'un des deux.
  - Anomalies_Numeriques : répartition par règle violée — là, chaque ligne EST
    une détection fraîche sur les données actuelles, aucun concept de "déjà
    connu" ne s'applique (contrairement aux deux champs catégoriels).

Usage (depuis la racine du repo) :
    python -m scripts.analyze_outliers --file e11_rdcc/outputs/E11_RDCC_classification.xlsx
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from shared.console import force_utf8_console


def _load_json(path: Path) -> dict:
    if not path.exists():
        print(f"  [WARN] Référentiel/cache introuvable : {path} (analyse partielle)")
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _split_known_vs_claude(raw_values, ref: dict, cache: dict) -> tuple[list, list, list]:
    """
    Retourne (connu_referentiel, resolu_cache, jamais_vu).

    Dans le classeur RÉEL, une valeur OUTLIER vient toujours de `ref` ou de `cache`
    (le classeur est construit comme {**ref, **cache}, cache prioritaire) — le 3e
    groupe (`jamais_vu`) devrait donc toujours être vide sur un vrai fichier ; il
    existe comme garde-fou explicite (plutôt qu'un défaut silencieux vers "connu")
    au cas où ce fichier viendrait d'ailleurs (ancienne version, édité à la main).
    """
    connu_referentiel, resolu_cache, jamais_vu = [], [], []
    for raw in raw_values:
        if raw in cache:
            resolu_cache.append(raw)
        elif raw in ref:
            connu_referentiel.append(raw)
        else:
            jamais_vu.append(raw)
    return connu_referentiel, resolu_cache, jamais_vu


def _print_reste(jamais_vu: list) -> None:
    if jamais_vu:
        print(f"  ⚠ Ni dans le référentiel ni dans le cache (inattendu sur un vrai fichier, "
              f"à examiner en priorité) : {len(jamais_vu)} — {jamais_vu[:15]}")


def analyze_nomcorrespondant(df: pd.DataFrame) -> None:
    ref = _load_json(Path("e11_rdcc/referentiel/nomcorrespondant_referentiel_E11.json")).get("mapping", {})
    cache = _load_json(Path("e11_rdcc/referentiel/validated_classif_nomcorrespondant_e11_rdcc.json")).get("classif", {})

    outliers = df.loc[df["NomCorrespondant_Normalisé"] == "OUTLIER", "NomCorrespondant"].tolist()
    connu, claude, reste = _split_known_vs_claude(outliers, ref, cache)

    print(f"\n=== NomCorrespondant : {len(outliers)} valeur(s) distincte(s) en OUTLIER ===")
    print(f"  Référentiel BCM statique (validé à l'avance, PAS un problème) : {len(connu)}")
    print(f"  Tranchées OUTLIER par Claude (déjà traité — à survoler si le nombre surprend) : {len(claude)}")
    if claude:
        print(f"  Exemples (jusqu'à 15 sur {len(claude)}) :", claude[:15])
    _print_reste(reste)


def analyze_devise(df: pd.DataFrame) -> None:
    ref_data = _load_json(Path("e11_rdcc/referentiel/devise_referentiel.json"))
    ref = {v: "OUTLIER" for v in ref_data.get("known_noise", [])}
    cache = _load_json(Path("e11_rdcc/referentiel/validated_classif_devise_e11_rdcc.json")).get("classif", {})

    outliers = df.loc[df["Devise_Normalisée"] == "OUTLIER", "Devise"].tolist()
    connu, corrige, reste = _split_known_vs_claude(outliers, ref, cache)

    print(f"\n=== Devise : {len(outliers)} valeur(s) distincte(s) en OUTLIER ===")
    print(f"  Bruit référencé statique (known_noise, PAS un problème) : {len(connu)}")
    print(f"  Confirmées OUTLIER via une correction manuelle passée : {len(corrige)}")
    if corrige:
        print(f"  Exemples (jusqu'à 15 sur {len(corrige)}) :", corrige[:15])
    _print_reste(reste)


def analyze_numeric_anomalies(df: pd.DataFrame) -> None:
    print(f"\n=== Anomalies_Numeriques : {len(df)} ligne(s) en anomalie ===")
    if df.empty:
        print("  Aucune anomalie numérique.")
        return
    by_rule = df["Rule"].value_counts()
    for rule, n in by_rule.items():
        print(f"  {rule:30s} {n:>8,} ligne(s)")
    print("\n  Pas de notion de 'déjà connu' ici : chaque ligne est une détection fraîche")
    print("  sur les données actuelles (aucun référentiel de 'combinaisons acceptées').")
    if "TEMPORAL_CONTINUITY" in by_rule.index and by_rule["TEMPORAL_CONTINUITY"] > 0.2 * len(df):
        print("\n  ⚠ TEMPORAL_CONTINUITY domine — si ce volume surprend, ça peut indiquer que la clé")
        print("    de groupement [RefBanque, NumCompte] (grouping_key_temporal_continuity dans le")
        print("    YAML, encore non validée définitivement avec le métier) n'est pas tout à fait la")
        print("    bonne, plutôt qu'un vrai problème de données sur chaque ligne listée.")


def main() -> None:
    force_utf8_console()
    parser = argparse.ArgumentParser(description="Analyse la répartition des outliers d'un run E11_RDCC")
    parser.add_argument("--file", default="e11_rdcc/outputs/E11_RDCC_classification.xlsx")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Fichier introuvable : {path}")

    xls = pd.ExcelFile(path)
    if "NomCorrespondant" in xls.sheet_names:
        analyze_nomcorrespondant(pd.read_excel(xls, "NomCorrespondant", dtype=str, keep_default_na=False))
    if "Devise" in xls.sheet_names:
        analyze_devise(pd.read_excel(xls, "Devise", dtype=str, keep_default_na=False))
    if "Anomalies_Numeriques" in xls.sheet_names:
        analyze_numeric_anomalies(pd.read_excel(xls, "Anomalies_Numeriques", dtype=str, keep_default_na=False))


if __name__ == "__main__":
    main()
