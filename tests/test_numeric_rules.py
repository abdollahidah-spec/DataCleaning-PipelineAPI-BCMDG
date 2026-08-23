import pandas as pd
import pytest

from e11_rdcc.fields.numeric_coherence import (
    NumericCoherenceConfig,
    check_arithmetic_row,
    check_date_validity_row,
    check_no_activity_conformity_row,
    check_temporal_continuity,
    run_all_rules,
    to_date_safe,
    to_float_safe,
)


@pytest.fixture
def cfg() -> NumericCoherenceConfig:
    return NumericCoherenceConfig(
        solde_debut="SoldeDebutJournee",
        mvts_debiteurs="TotalMvtsDebiteursJournee",
        mvts_crediteurs="TotalMvtsCrediteurs",
        solde_fin="SoldeFinJournee",
        date_fin="DateFinJournee",
        dt_cr="dtCr",
        ref_banque="RefBanque",
        nom_correspondant="NomCorrespondant",
        devise="Devise",
        num_compte="NumCompte",
        grouping_key=["RefBanque", "NomCorrespondant", "Devise"],
        tolerance_abs=0.01,
    )


def _row(**kwargs) -> pd.Series:
    base = {
        "RefBanque": "BANK01", "NomCorrespondant": "SOME BANK", "Devise": "USD",
        "NumCompte": "CPT001", "DateFinJournee": "2026-07-20", "dtCr": "2026-07-21",
        "SoldeDebutJournee": 100.0, "TotalMvtsDebiteursJournee": 0.0,
        "TotalMvtsCrediteurs": 0.0, "SoldeFinJournee": 100.0,
    }
    base.update(kwargs)
    return pd.Series(base)


# ---- to_float_safe / to_date_safe -----------------------------------------------

def test_to_float_safe_handles_comma_decimal():
    assert to_float_safe("12,5") == 12.5


def test_to_float_safe_none_on_garbage():
    assert to_float_safe("abcd") is None
    assert to_float_safe(None) is None
    assert to_float_safe("") is None


def test_to_date_safe_iso():
    assert to_date_safe("2026-07-20").isoformat() == "2026-07-20"


def test_to_date_safe_none_on_garbage():
    assert to_date_safe("31/02/2024") is None
    assert to_date_safe("abcd") is None


# ---- Rule 1 : ARITHMETIC ----------------------------------------------------------

def test_arithmetic_ok(cfg):
    row = _row(SoldeDebutJournee=1000, TotalMvtsCrediteurs=150, TotalMvtsDebiteursJournee=200, SoldeFinJournee=950)
    res = check_arithmetic_row(row, cfg)
    assert res["is_anomaly"] is False


def test_arithmetic_mismatch(cfg):
    row = _row(SoldeDebutJournee=1000, TotalMvtsCrediteurs=150, TotalMvtsDebiteursJournee=200, SoldeFinJournee=999)
    res = check_arithmetic_row(row, cfg)
    assert res["is_anomaly"] is True
    assert res["delta"] == pytest.approx(49.0)


def test_arithmetic_missing_field(cfg):
    row = _row(SoldeFinJournee="")
    res = check_arithmetic_row(row, cfg)
    assert res["is_anomaly"] is True
    assert "SoldeFinJournee" in res["detail"]


# ---- Rule 3 : NO_ACTIVITY_CONFORMITY ----------------------------------------------

def test_no_activity_valid_row(cfg):
    row = _row(NomCorrespondant="NA", Devise="NA", NumCompte="NA",
               SoldeDebutJournee=0, TotalMvtsDebiteursJournee=0, TotalMvtsCrediteurs=0, SoldeFinJournee=0)
    res = check_no_activity_conformity_row(row, cfg)
    assert res["is_anomaly"] is False
    assert res["is_no_activity_row"] is True


def test_no_activity_declared_but_nonzero(cfg):
    row = _row(NomCorrespondant="NA", Devise="NA", NumCompte="NA",
               SoldeDebutJournee=0, TotalMvtsDebiteursJournee=0, TotalMvtsCrediteurs=0, SoldeFinJournee=5)
    res = check_no_activity_conformity_row(row, cfg)
    assert res["is_anomaly"] is True
    assert res["is_no_activity_row"] is True


def test_no_activity_partial_na(cfg):
    row = _row(NomCorrespondant="NA", Devise="USD", NumCompte="CPT001", SoldeDebutJournee=150)
    res = check_no_activity_conformity_row(row, cfg)
    assert res["is_anomaly"] is True
    assert res["is_no_activity_row"] is False


def test_no_activity_normal_row_untouched(cfg):
    row = _row()
    res = check_no_activity_conformity_row(row, cfg)
    assert res["is_anomaly"] is False
    assert res["is_no_activity_row"] is False


# ---- Rule 4 : DATE_VALIDITY --------------------------------------------------------

def test_date_validity_ok(cfg):
    row = _row(DateFinJournee="2026-07-20", dtCr="2026-07-21")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is False


def test_date_validity_equal_dtcr_is_ok(cfg):
    """<= autorisé (confirmation métier de la coquille du ticket)."""
    row = _row(DateFinJournee="2026-07-21", dtCr="2026-07-21")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is False


def test_date_validity_after_dtcr_is_anomaly(cfg):
    row = _row(DateFinJournee="2026-07-25", dtCr="2026-07-21")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is True


def test_date_validity_unparsable(cfg):
    row = _row(DateFinJournee="not-a-date")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is True


# ---- Rule 2 : TEMPORAL_CONTINUITY --------------------------------------------------

def test_temporal_continuity_ok_chain(cfg):
    df = pd.DataFrame([
        _row(DateFinJournee="2026-07-20", SoldeDebutJournee=1000, SoldeFinJournee=950),
        _row(DateFinJournee="2026-07-21", SoldeDebutJournee=950, SoldeFinJournee=900),
    ])
    no_activity_mask = pd.Series([False, False])
    anomalies = check_temporal_continuity(df, cfg, no_activity_mask)
    assert anomalies.empty


def test_temporal_continuity_mismatch(cfg):
    df = pd.DataFrame([
        _row(DateFinJournee="2026-07-20", SoldeDebutJournee=1000, SoldeFinJournee=950),
        _row(DateFinJournee="2026-07-21", SoldeDebutJournee=900, SoldeFinJournee=850),  # devrait être 950
    ])
    no_activity_mask = pd.Series([False, False])
    anomalies = check_temporal_continuity(df, cfg, no_activity_mask)
    assert len(anomalies) == 1
    assert anomalies.iloc[0]["Rule"] == "TEMPORAL_CONTINUITY"
    assert anomalies.iloc[0]["Severity"] == "ERROR"


def test_temporal_continuity_single_row_no_anomaly(cfg):
    """Pas de J+1 -> rien à comparer, aucune anomalie."""
    df = pd.DataFrame([_row(DateFinJournee="2026-07-20")])
    anomalies = check_temporal_continuity(df, cfg, pd.Series([False]))
    assert anomalies.empty


def test_temporal_continuity_gap_is_warning(cfg):
    df = pd.DataFrame([
        _row(DateFinJournee="2026-07-20", SoldeDebutJournee=1000, SoldeFinJournee=950),
        _row(DateFinJournee="2026-07-25", SoldeDebutJournee=950, SoldeFinJournee=900),  # écart de 5 jours, cohérent
    ])
    anomalies = check_temporal_continuity(df, cfg, pd.Series([False, False]))
    assert len(anomalies) == 1
    assert anomalies.iloc[0]["Severity"] == "WARNING"


def test_temporal_continuity_duplicate_day_is_error(cfg):
    df = pd.DataFrame([
        _row(DateFinJournee="2026-07-20", SoldeDebutJournee=1000, SoldeFinJournee=950),
        _row(DateFinJournee="2026-07-20", SoldeDebutJournee=1000, SoldeFinJournee=950),
    ])
    anomalies = check_temporal_continuity(df, cfg, pd.Series([False, False]))
    assert len(anomalies) == 1
    assert "DUPLICATE" not in anomalies.iloc[0]["Detail"]  # detail texte libre, mais règle/sévérité stables
    assert anomalies.iloc[0]["Rule"] == "TEMPORAL_CONTINUITY"
    assert anomalies.iloc[0]["Severity"] == "ERROR"


def test_temporal_continuity_two_no_activity_rows_no_false_collision(cfg):
    """Deux comptes distincts, tous deux 'sans activité' le même jour/banque —
    ils sont exclus du chaînage, donc pas de fausse collision de continuité."""
    df = pd.DataFrame([
        _row(RefBanque="BANK01", NomCorrespondant="NA", Devise="NA", NumCompte="NA", DateFinJournee="2026-07-20"),
        _row(RefBanque="BANK01", NomCorrespondant="NA", Devise="NA", NumCompte="NA", DateFinJournee="2026-07-20"),
    ])
    no_activity_mask = pd.Series([True, True])
    anomalies = check_temporal_continuity(df, cfg, no_activity_mask)
    assert anomalies.empty


# ---- run_all_rules : plusieurs règles violées simultanément ------------------------

def test_row_failing_multiple_rules_reports_all(cfg):
    """Une ligne 'sans activité' déclarée avec un solde non nul échoue ARITHMETIC ET
    NO_ACTIVITY_CONFORMITY — les deux doivent apparaître, aucune masquée par l'autre."""
    df = pd.DataFrame([
        _row(NomCorrespondant="NA", Devise="NA", NumCompte="NA",
             SoldeDebutJournee=0, TotalMvtsDebiteursJournee=0, TotalMvtsCrediteurs=0, SoldeFinJournee=5),
    ])
    _, anomalies = run_all_rules(df, cfg)
    rules = set(anomalies["Rule"])
    assert "ARITHMETIC" in rules
    assert "NO_ACTIVITY_CONFORMITY" in rules


# ---- Cas limites (tolérance exacte, signe, formats de date/heure) ------------------

def test_arithmetic_exactly_at_tolerance_boundary_is_not_anomaly(cfg):
    """Le test est '> tolerance', pas '>= tolerance' — un écart EXACTEMENT égal à
    la tolérance ne doit pas déclencher d'anomalie (bord inclus côté conforme)."""
    row = _row(SoldeDebutJournee=1000, TotalMvtsCrediteurs=0, TotalMvtsDebiteursJournee=0,
               SoldeFinJournee=1000 + cfg.tolerance_abs)
    res = check_arithmetic_row(row, cfg)
    assert res["is_anomaly"] is False


def test_arithmetic_just_over_tolerance_boundary_is_anomaly(cfg):
    # +0.02 (pas +0.001) : le delta est arrondi à 2 décimales avant comparaison
    # (voir check_arithmetic_row), un écart trop fin serait absorbé par l'arrondi.
    row = _row(SoldeDebutJournee=1000, TotalMvtsCrediteurs=0, TotalMvtsDebiteursJournee=0,
               SoldeFinJournee=1000 + cfg.tolerance_abs + 0.02)
    res = check_arithmetic_row(row, cfg)
    assert res["is_anomaly"] is True


def test_arithmetic_negative_delta_is_symmetric(cfg):
    """Un solde de fin TROP BAS (delta négatif) doit être détecté au même titre
    qu'un solde trop haut — la comparaison est sur la valeur absolue de l'écart."""
    row = _row(SoldeDebutJournee=1000, TotalMvtsCrediteurs=0, TotalMvtsDebiteursJournee=0, SoldeFinJournee=900)
    res = check_arithmetic_row(row, cfg)
    assert res["is_anomaly"] is True
    assert res["delta"] == pytest.approx(-100.0)


def test_date_validity_accepts_datetime_with_time_component(cfg):
    """Les dates issues de SQL Server peuvent arriver avec une composante horaire
    (ex: '2026-07-20 00:00:00') — doit rester parsable et comparable normalement."""
    row = _row(DateFinJournee="2026-07-20 00:00:00", dtCr="2026-07-21 08:30:00")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is False


def test_date_validity_french_format_parsable(cfg):
    row = _row(DateFinJournee="20/07/2026", dtCr="21/07/2026")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is False


def test_to_float_safe_handles_native_types():
    assert to_float_safe(150) == 150.0
    assert to_float_safe(150.5) == 150.5
    assert to_float_safe(True) is None   # bool exclu explicitement (sous-classe de int en Python)


def test_to_date_safe_handles_native_datetime_and_date_objects():
    from datetime import date, datetime
    assert to_date_safe(datetime(2026, 7, 20, 10, 30)) == date(2026, 7, 20)
    assert to_date_safe(date(2026, 7, 20)) == date(2026, 7, 20)


def test_temporal_continuity_grouping_by_numcompte_only(cfg):
    """Config réelle (E11_RDCC.yaml) : grouping_key = [RefBanque, NumCompte] — deux
    comptes différents du même correspondant/devise ne doivent PAS être chaînés
    ensemble (contrairement à l'ancien placeholder [RefBanque, NomCorrespondant, Devise])."""
    cfg_numcompte = NumericCoherenceConfig(
        **{**cfg.__dict__, "grouping_key": ["RefBanque", "NumCompte"]}
    )
    df = pd.DataFrame([
        _row(NumCompte="CPT_A", DateFinJournee="2026-07-20", SoldeDebutJournee=1000, SoldeFinJournee=950),
        _row(NumCompte="CPT_B", DateFinJournee="2026-07-20", SoldeDebutJournee=500, SoldeFinJournee=500),
        _row(NumCompte="CPT_A", DateFinJournee="2026-07-21", SoldeDebutJournee=950, SoldeFinJournee=900),
    ])
    anomalies = check_temporal_continuity(df, cfg_numcompte, pd.Series([False, False, False]))
    assert anomalies.empty  # CPT_A chaîne correctement (950->950), CPT_B seul (rien à comparer)
