"""
Règle NA/OUTLIER : équivalence STRICTE entre la version ligne à ligne (référence
historique) et la version vectorisée qui la remplace dans le traitement.

La vectorisation supprime un appel Python par ligne sur l'intégralité du jeu de
données — poste de coût dominant du traitement catégoriel. Ces tests garantissent
que le gain de performance ne s'accompagne d'aucun changement de comportement.
"""
import numpy as np
import pandas as pd

from shared.na_rule import apply_na_rule, apply_na_rule_frame

ISO, MTH = "Val_Normalisee", "Val_method"
FIELD, REF = "Val", "Ref"


def _row_by_row(df: pd.DataFrame) -> pd.DataFrame:
    """Application de la règle d'origine, ligne par ligne — la référence."""
    out = df.apply(lambda r: apply_na_rule(r, FIELD, REF, ISO, MTH), axis=1, result_type="expand")
    return pd.DataFrame({"iso": out[0], "mth": out[1]})


def _vectorized(df: pd.DataFrame) -> pd.DataFrame:
    iso, mth = apply_na_rule_frame(df, FIELD, REF, ISO, MTH)
    return pd.DataFrame({"iso": iso, "mth": mth})


def _assert_same(df: pd.DataFrame) -> None:
    attendu, obtenu = _row_by_row(df), _vectorized(df)
    pd.testing.assert_frame_equal(obtenu, attendu, check_dtype=False)


def test_equivalence_sur_tous_les_cas_nominaux():
    df = pd.DataFrame({
        FIELD: ["NA", "NA", "USD", "", "nan", "None", "NULL", "XYZ", "  na  ", "EUR"],
        REF:   ["NA", "REF1", "REF1", "REF1", "REF1", "REF1", "REF1", "REF1", "NA", "REF1"],
        ISO:   ["USD", "USD", "USD", "USD", "USD", "USD", "USD", "OUTLIER", "USD", "OUTLIER"],
        MTH:   ["MAP", "MAP", "MAP", "MAP", "MAP", "MAP", "MAP", "NOISE", "MAP", None],
    })
    _assert_same(df)


def test_methode_preservee_quand_deja_outlier():
    """Le correctif du bug latent : un OUTLIER déjà connu du référentiel garde sa
    méthode (NOISE) au lieu d'être écrasé en 'OUTLIER' générique."""
    df = pd.DataFrame({FIELD: ["BRUIT"], REF: ["REF1"], ISO: ["OUTLIER"], MTH: ["NOISE"]})
    iso, mth = apply_na_rule_frame(df, FIELD, REF, ISO, MTH)
    assert list(iso) == ["OUTLIER"]
    assert list(mth) == ["NOISE"]
    _assert_same(df)


def test_methode_chaine_vide_retombe_sur_outlier():
    """Une méthode vide ("") est falsy -> remplacée par 'OUTLIER'.

    Nuance conservée à l'identique de la version d'origine : une méthode
    manquante devient NaN à la construction du DataFrame, or NaN est *truthy* en
    Python — elle est donc préservée telle quelle, pas remplacée. Comportement
    inchangé par la vectorisation (c'est précisément ce que vérifie
    test_equivalence_sur_jeu_aleatoire_volumineux)."""
    df = pd.DataFrame({
        FIELD: ["X", "Y"], REF: ["R", "R"],
        ISO: ["OUTLIER", "OUTLIER"], MTH: ["", "MAP"],
    })
    _, mth = apply_na_rule_frame(df, FIELD, REF, ISO, MTH)
    assert list(mth) == ["OUTLIER", "MAP"]
    _assert_same(df)


def test_equivalence_sur_jeu_aleatoire_volumineux():
    """Comparaison exhaustive sur 5 000 lignes tirées au hasard dans l'espace des
    combinaisons possibles — filet de sécurité contre un cas non anticipé."""
    rng = np.random.default_rng(20260907)
    n = 5000
    df = pd.DataFrame({
        FIELD: rng.choice(["NA", "na", " NA ", "", "nan", "None", "NULL", "USD", "EUR", "ZZZ"], n),
        REF:   rng.choice(["NA", "na", "REF1", "REF2", ""], n),
        ISO:   rng.choice(["USD", "EUR", "OUTLIER", "NA"], n),
        MTH:   rng.choice(["MAP", "WARM", "NOISE", "CLAUDE", "OUTLIER", None, ""], n),
    })
    _assert_same(df)


def test_index_non_trivial_est_respecte():
    """Le DataFrame traité peut avoir un index quelconque (filtrage amont) — les
    Series retournées doivent rester alignées dessus."""
    df = pd.DataFrame(
        {FIELD: ["NA", "USD"], REF: ["NA", "R"], ISO: ["X", "USD"], MTH: ["MAP", "MAP"]},
        index=[17, 42],
    )
    iso, mth = apply_na_rule_frame(df, FIELD, REF, ISO, MTH)
    assert list(iso.index) == [17, 42]
    assert list(mth.index) == [17, 42]
    _assert_same(df)
