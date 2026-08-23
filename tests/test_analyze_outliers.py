"""
Vérifie la logique de scripts/analyze_outliers.py qui distingue, pour un run
réel, les outliers déjà connus du référentiel statique de ceux tranchés par
Claude/une correction (dans le cache) — voir la demande utilisateur : un outlier
déjà classé comme tel dans le référentiel n'est pas un problème à signaler.
"""
from scripts.analyze_outliers import _split_known_vs_claude


def test_split_known_vs_claude_separates_referentiel_from_cache():
    ref = {"BCM": "OUTLIER", "TEST2": "OUTLIER"}
    cache = {"UNKNOWN BANK XYZ": "OUTLIER", "ANOTHER ONE": "OUTLIER"}

    connu, claude, reste = _split_known_vs_claude(["BCM", "UNKNOWN BANK XYZ", "TEST2"], ref, cache)

    assert connu == ["BCM", "TEST2"]
    assert claude == ["UNKNOWN BANK XYZ"]
    assert reste == []


def test_split_known_vs_claude_cache_takes_priority_when_in_both():
    """Une correction manuelle plus récente doit primer sur le référentiel statique
    (même logique que le cache warm-start prime sur le référentiel dans le pipeline)."""
    ref = {"X": "OUTLIER"}
    cache = {"X": "OUTLIER"}

    connu, claude, reste = _split_known_vs_claude(["X"], ref, cache)

    assert connu == []
    assert claude == ["X"]
    assert reste == []


def test_split_known_vs_claude_flags_values_absent_from_both():
    """Ne doit JAMAIS retomber silencieusement dans 'connu' — cas normalement
    impossible sur le vrai fichier (garde-fou explicite, voir docstring)."""
    connu, claude, reste = _split_known_vs_claude(["MYSTERE"], {"AUTRE": "OUTLIER"}, {})

    assert connu == []
    assert claude == []
    assert reste == ["MYSTERE"]


def test_split_known_vs_claude_empty_input():
    assert _split_known_vs_claude([], {}, {}) == ([], [], [])
