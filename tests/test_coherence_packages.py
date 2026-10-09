"""
Anti-divergence entre packages d'API.

Chaque API est volontairement auto-contenue (décision H) : les champs communs sont
donc des COPIES. Un correctif appliqué à une copie et oublié dans les autres passe
inaperçu — c'est arrivé : l'étape `MAP_CIBLE` et les lots Claude parallèles
n'existaient que dans E07/E10, si bien que sur E08 et E11 une valeur déjà conforme
au référentiel partait en appel payant et pouvait revenir OUTLIER.

Ces tests ne jugent pas le métier : ils vérifient que les garanties transverses
sont présentes PARTOUT où elles s'appliquent.
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Champs qui résolvent une valeur contre un référentiel {brut -> valeur normalisée} :
# une valeur déjà égale à la valeur normalisée doit être résolue par elle-même.
CHAMPS_AVEC_REFERENTIEL = [
    "e07_fs/fields/nomdonneurordre.py",
    "e07_fs/fields/beneficiaire.py",
    "e08_ocd/fields/nomdonneurordre.py",
    "e08_ocd/fields/beneficiaire.py",
    "e08_ocd/fields/nomcorrespondant.py",
    "e08_ocd/fields/produits.py",
    "e10_fe/fields/nomdonneurordre.py",
    "e10_fe/fields/beneficiaire.py",
    "e11_rdcc/fields/nomcorrespondant.py",
]

# Tous les champs qui appellent Claude, quel qu'en soit le moteur.
CHAMPS_AVEC_CLAUDE = CHAMPS_AVEC_REFERENTIEL + [
    "e07_fs/fields/nature_economique.py",
    "e07_fs/fields/pays.py",
    "e08_ocd/fields/pays.py",
    "e10_fe/fields/nature_economique.py",
    "e10_fe/fields/pays.py",
]

COPIES_IDENTIQUES = [
    ("fields/_entity_matching.py", ["e07_fs", "e08_ocd", "e10_fe"]),
    ("fields/_keywords.py", ["e07_fs", "e08_ocd", "e10_fe"]),
    ("fields/transactions.py", ["e07_fs", "e10_fe"]),
]


def _source(chemin: str) -> str:
    return (REPO / chemin).read_text(encoding="utf-8")


@pytest.mark.parametrize("chemin", CHAMPS_AVEC_REFERENTIEL)
def test_etape_valeur_deja_conforme_presente(chemin):
    assert "MAP_CIBLE" in _source(chemin), (
        f"{chemin} : étape MAP_CIBLE absente — une valeur déjà égale à la valeur "
        f"normalisée du référentiel partirait en résolution payante")


@pytest.mark.parametrize("chemin", CHAMPS_AVEC_CLAUDE)
def test_lots_claude_paralleles(chemin):
    src = _source(chemin)
    assert "resolve_in_batches" in src, f"{chemin} : lots Claude non parallélisés"
    assert "for debut in range(" not in src, (
        f"{chemin} : boucle de lots séquentielle résiduelle (les appels Claude doivent "
        f"passer par shared/claude_batches.py)")


@pytest.mark.parametrize("fichier,paquets", COPIES_IDENTIQUES)
def test_copies_restent_identiques(fichier, paquets):
    """
    Ces fichiers sont des copies : leur CODE doit rester identique d'un package à
    l'autre. La comparaison porte sur la structure du code (arbre syntaxique, sans
    docstrings ni commentaires) : chaque copie documente légitimement son API
    (« Flux Sortants » / « Flux Entrants »), mais la logique, elle, ne doit pas
    diverger en silence.
    """
    import ast

    def structure(paquet: str) -> str:
        src = _source(f"{paquet}/{fichier}").replace(paquet, "PKG")
        arbre = ast.parse(src)
        for noeud in ast.walk(arbre):
            if isinstance(noeud, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                corps = noeud.body
                if corps and isinstance(corps[0], ast.Expr) and isinstance(corps[0].value, ast.Constant) \
                        and isinstance(corps[0].value.value, str):
                    noeud.body = corps[1:] or [ast.Pass()]
        return ast.dump(arbre)

    reference = structure(paquets[0])
    for paquet in paquets[1:]:
        assert structure(paquet) == reference, (
            f"{paquet}/{fichier} a divergé de {paquets[0]}/{fichier} : reporter le "
            f"correctif dans toutes les copies")


def test_regle_na_partagee_et_jamais_recopiee():
    """La règle NA est la seule logique métier commune autorisée dans shared/ : aucun
    package ne doit en avoir sa propre version (bug de 2026-09-17 sur les NULL,
    corrigé une seule fois pour les 5 APIs)."""
    import shared.na_rule  # noqa: F401

    for paquet in ("e07_fs", "e08_ocd", "e09_pe", "e10_fe", "e11_rdcc"):
        for module in (REPO / paquet / "fields").glob("*.py"):
            src = module.read_text(encoding="utf-8")
            assert "def apply_na_rule" not in src, f"{module} redéfinit la règle NA"
