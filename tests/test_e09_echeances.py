import pandas as pd
import pytest

from e09_pe.fields.echeances import (
    EcheancesConfig,
    check_amount_positive_row,
    check_date_validity_row,
    run_all_rules,
    to_date_safe,
    to_float_safe,
)


@pytest.fixture
def cfg() -> EcheancesConfig:
    return EcheancesConfig(
        montant_echeance="MontantEcheance",
        date_echeance="DateEcheance",
        dt_cr="dtCr",
        ref_banque="RefBanque",
        num_credoc="NumCredoc",
    )


def _row(**kwargs) -> pd.Series:
    base = {
        "RefBanque": "BANK01", "NumCredoc": "CD0001",
        "MontantEcheance": 1000.0, "DateEcheance": "2026-08-20", "dtCr": "2026-08-10",
    }
    base.update(kwargs)
    return pd.Series(base)


# ---- Rule 1 : AMOUNT_POSITIVE ------------------------------------------------------

def test_amount_positive_ok(cfg):
    row = _row(MontantEcheance=100.0)
    res = check_amount_positive_row(row, cfg)
    assert res["is_anomaly"] is False


def test_amount_zero_is_anomaly(cfg):
    row = _row(MontantEcheance=0)
    res = check_amount_positive_row(row, cfg)
    assert res["is_anomaly"] is True
    assert "<= 0" in res["detail"]


def test_amount_negative_is_anomaly(cfg):
    row = _row(MontantEcheance=-50.0)
    res = check_amount_positive_row(row, cfg)
    assert res["is_anomaly"] is True


def test_amount_non_numeric_is_anomaly(cfg):
    row = _row(MontantEcheance="abc")
    res = check_amount_positive_row(row, cfg)
    assert res["is_anomaly"] is True
    assert "MontantEcheance" in res["detail"]


def test_amount_missing_is_anomaly(cfg):
    row = _row(MontantEcheance="")
    res = check_amount_positive_row(row, cfg)
    assert res["is_anomaly"] is True


def test_amount_comma_decimal_is_parsed(cfg):
    row = _row(MontantEcheance="100,5")
    res = check_amount_positive_row(row, cfg)
    assert res["is_anomaly"] is False


# ---- Rule 2 : DATE_VALIDITY (échéance strictement postérieure à dtCr) --------------

def test_date_validity_future_is_ok(cfg):
    row = _row(DateEcheance="2026-08-20", dtCr="2026-08-10")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is False


def test_date_validity_equal_dtcr_is_anomaly(cfg):
    """Contrairement à E11 (<=), ici c'est '>' strict : une échéance le même jour
    que la création n'est pas une échéance future valide."""
    row = _row(DateEcheance="2026-08-10", dtCr="2026-08-10")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is True


def test_date_validity_before_dtcr_is_anomaly(cfg):
    row = _row(DateEcheance="2026-08-05", dtCr="2026-08-10")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is True


def test_date_validity_unparsable_is_anomaly(cfg):
    row = _row(DateEcheance="not-a-date")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is True


def test_date_validity_french_format_parsable(cfg):
    row = _row(DateEcheance="20/08/2026", dtCr="10/08/2026")
    assert check_date_validity_row(row, cfg)["is_anomaly"] is False


# ---- run_all_rules : plusieurs règles violées simultanément ------------------------

def test_row_failing_both_rules_reports_both(cfg):
    df = pd.DataFrame([_row(MontantEcheance=-10.0, DateEcheance="2026-08-05", dtCr="2026-08-10")])
    _, anomalies = run_all_rules(df, cfg)
    rules = set(anomalies["Rule"])
    assert "AMOUNT_POSITIVE" in rules
    assert "DATE_VALIDITY" in rules
    assert len(anomalies) == 2


def test_row_failing_no_rule_produces_no_anomaly(cfg):
    df = pd.DataFrame([_row()])
    annotated, anomalies = run_all_rules(df, cfg)
    assert anomalies.empty
    assert annotated["_EC_row_conforme"].iloc[0] == True  # noqa: E712


def test_anomaly_rows_keep_numcredoc_and_refbanque(cfg):
    df = pd.DataFrame([_row(RefBanque="BANK09", NumCredoc="CD9999", MontantEcheance=-1.0)])
    _, anomalies = run_all_rules(df, cfg)
    assert anomalies.iloc[0]["NumCredoc"] == "CD9999"
    assert anomalies.iloc[0]["RefBanque"] == "BANK09"
    assert anomalies.iloc[0]["Severity"] == "ERROR"


# ---- Parsing helpers ----------------------------------------------------------------

def test_to_float_safe_handles_comma_decimal():
    assert to_float_safe("12,5") == 12.5


def test_to_float_safe_none_on_garbage():
    assert to_float_safe("abcd") is None
    assert to_float_safe(None) is None


def test_to_date_safe_iso():
    assert to_date_safe("2026-08-20").isoformat() == "2026-08-20"


def test_to_date_safe_none_on_garbage():
    assert to_date_safe("31/02/2024") is None
