"""
Règles Échéances (E09) : équivalence STRICTE entre la construction ligne à ligne
des anomalies (référence) et la version vectorisée qui la remplace.

Contexte : sur l'Initial Load d'E09 (5,1 M lignes), la construction du texte
Detail re-parsait chaque ligne anomale valeur par valeur via `to_date_safe`, qui
essaie jusqu'à 4 formats successifs en s'appuyant sur des exceptions. Résultat :
le run est resté bloqué plus d'une heure sur ce seul champ. La version vectorisée
réutilise les colonnes déjà parsées pour la décision.
"""
import numpy as np
import pandas as pd

from e09_pe.fields.echeances import (
    EcheancesConfig,
    _build_anomaly_row,
    check_amount_positive_row,
    check_date_validity_row,
    run_all_rules,
)
from shared.frame_utils import iter_rows_as_dicts

CFG = EcheancesConfig(
    montant_echeance="MontantEcheance", date_echeance="DateEcheance",
    dt_cr="dtCr", ref_banque="RefBanque", num_credoc="NumCredoc",
)
_COLS = ["NumCredoc", "RefBanque", "MontantEcheance", "DateEcheance", "dtCr",
         "Rule", "Detail", "Severity"]


def _reference(df: pd.DataFrame) -> pd.DataFrame:
    """Ancienne implémentation : décision vectorisée, texte construit ligne à ligne."""
    from e09_pe.fields.echeances import _to_date_series, _to_float_series

    montant = _to_float_series(df[CFG.montant_echeance])
    amount_anomaly = montant.isna() | (montant <= 0)
    date_ech = _to_date_series(df[CFG.date_echeance])
    dt_cr = _to_date_series(df[CFG.dt_cr])
    date_anomaly = date_ech.isna() | dt_cr.isna() | (date_ech <= dt_cr)

    lues = [CFG.num_credoc, CFG.ref_banque, CFG.montant_echeance, CFG.date_echeance, CFG.dt_cr]
    rows = []
    for row in iter_rows_as_dicts(df.loc[amount_anomaly], lues):
        r = check_amount_positive_row(row, CFG)
        rows.append(_build_anomaly_row(row, CFG, r["rule"], r["detail"]))
    for row in iter_rows_as_dicts(df.loc[date_anomaly], lues):
        r = check_date_validity_row(row, CFG)
        rows.append(_build_anomaly_row(row, CFG, r["rule"], r["detail"]))
    return pd.DataFrame(rows, columns=_COLS) if rows else pd.DataFrame(columns=_COLS)


def _assert_same(df: pd.DataFrame) -> None:
    _, obtenu = run_all_rules(df, CFG)
    attendu = _reference(df)
    pd.testing.assert_frame_equal(
        obtenu.reset_index(drop=True).astype(str),
        attendu.reset_index(drop=True).astype(str),
    )


def test_equivalence_sur_tous_les_cas():
    df = pd.DataFrame({
        "NumCredoc": [f"CD{i}" for i in range(8)],
        "RefBanque": ["BMCI"] * 8,
        "MontantEcheance": ["100", "-5", "0", "abc", "", "250,50", "1000", "7"],
        "DateEcheance": ["2027-01-15", "2027-01-15", "2020-01-01", "2027-01-15",
                          "pas une date", "2027-01-15", "", "2027-01-15"],
        "dtCr": ["2026-09-01"] * 7 + ["pas une date"],
    })
    _assert_same(df)


def test_equivalence_quand_presque_tout_est_anomal():
    """Cas de l'Initial Load réel : échéances passées face à un dtCr récent."""
    n = 500
    df = pd.DataFrame({
        "NumCredoc": [f"CD{i}" for i in range(n)],
        "RefBanque": "BMCI",
        "MontantEcheance": "1000",
        "DateEcheance": "2020-01-01",     # toutes antérieures à dtCr
        "dtCr": "2026-09-01",
    })
    _, anomalies = run_all_rules(df, CFG)
    assert len(anomalies) == n
    assert (anomalies["Rule"] == "DATE_VALIDITY").all()
    _assert_same(df)


def test_equivalence_sur_jeu_aleatoire():
    rng = np.random.default_rng(20260907)
    n = 2000
    df = pd.DataFrame({
        "NumCredoc": [f"CD{i}" for i in range(n)],
        "RefBanque": rng.choice(["BMCI", "BNM", "BCI"], n),
        "MontantEcheance": rng.choice(["100", "-1", "0", "abc", "", "9999999"], n),
        "DateEcheance": rng.choice(["2027-06-30", "2020-01-01", "", "n/a", "2026-09-01"], n),
        "dtCr": rng.choice(["2026-09-01", "", "2025-01-01"], n),
    })
    _assert_same(df)


def test_comparaison_au_jour_ignore_l_heure_de_dtcr():
    """L'heure ne doit jamais décider du verdict : `dtCr` porte un horodatage de
    chargement, `DateEcheance` est une date métier. Une échéance au lendemain
    reste conforme même si l'heure de dtCr est plus tardive dans la journée."""
    df = pd.DataFrame({
        "NumCredoc": ["CD1", "CD2"],
        "RefBanque": ["BMCI", "BMCI"],
        "MontantEcheance": ["1000", "1000"],
        "DateEcheance": ["2026-09-02 00:00:00", "2026-09-02 06:00:00"],
        "dtCr": ["2026-09-01 23:59:59", "2026-09-01 23:59:59"],
    })
    _, anomalies = run_all_rules(df, CFG)
    assert anomalies.empty, "une échéance au lendemain est conforme, quelle que soit l'heure"


def test_meme_jour_reste_une_anomalie_quelle_que_soit_l_heure():
    """Règle « strictement postérieure » : même jour = anomalie, y compris quand
    l'heure de l'échéance est plus tardive que celle de dtCr (ce qui, avant la
    comparaison au jour, passait à tort pour conforme)."""
    df = pd.DataFrame({
        "NumCredoc": ["CD1"], "RefBanque": ["BMCI"], "MontantEcheance": ["1000"],
        "DateEcheance": ["2026-09-01 23:00:00"], "dtCr": ["2026-09-01 08:00:00"],
    })
    _, anomalies = run_all_rules(df, CFG)
    assert len(anomalies) == 1
    assert anomalies.iloc[0]["Rule"] == "DATE_VALIDITY"


def test_aucune_anomalie_donne_un_tableau_vide():
    df = pd.DataFrame({
        "NumCredoc": ["CD1"], "RefBanque": ["BMCI"], "MontantEcheance": ["100"],
        "DateEcheance": ["2027-01-15"], "dtCr": ["2026-09-01"],
    })
    annote, anomalies = run_all_rules(df, CFG)
    assert anomalies.empty
    assert list(anomalies.columns) == _COLS
    assert bool(annote["_EC_row_conforme"].iloc[0]) is True
