from datetime import datetime
from pathlib import Path

import pytest

from shared.state_store import (
    _should_update_watermark,
    _state_dir,
    get_run_state,
    record_run_result,
    seed_initial_state,
)


def test_state_dir_treats_empty_string_as_unset(monkeypatch):
    """Bug réel trouvé en prod : STATE_DIR="" présente-mais-vide dans .env doit se
    comporter comme si la variable était absente, pas comme un chemin "" (= CWD)."""
    monkeypatch.setenv("STATE_DIR", "")
    assert _state_dir() == Path("e11_rdcc/state")


def test_state_dir_uses_explicit_value_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    assert _state_dir() == tmp_path


def test_watermark_advances_on_ok_with_rows():
    assert _should_update_watermark("OK", datetime(2026, 7, 20)) is True


def test_watermark_does_not_advance_on_ko():
    assert _should_update_watermark("KO", datetime(2026, 7, 20)) is False


def test_watermark_does_not_advance_on_ok_with_no_rows():
    assert _should_update_watermark("OK", None) is False


def test_watermark_does_not_advance_on_ko_with_no_rows():
    assert _should_update_watermark("KO", None) is False


@pytest.fixture
def isolated_state_dir(tmp_path, monkeypatch):
    """Fichier JSON local, jamais la vraie base — voir shared/state_store.py."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    return tmp_path


def test_get_run_state_none_when_no_file(isolated_state_dir):
    assert get_run_state("UNKNOWN_API") is None


def test_record_and_read_run_state(isolated_state_dir):
    state = record_run_result("TEST_API", "initial", "OK", datetime(2026, 7, 20), 42, outliers_this_run=5)
    assert state.last_run_status == "OK"
    assert state.cumulative_rows == 42
    assert state.cumulative_outliers == 5
    assert state.cumulative_runs == 1
    assert state.last_dtcr_processed == datetime(2026, 7, 20)
    assert state.first_run_datetime is not None

    reloaded = get_run_state("TEST_API")
    assert reloaded == state


def test_cumulative_counters_increment_across_runs(isolated_state_dir):
    record_run_result("TEST_API", "initial", "OK", datetime(2026, 7, 20), 100, outliers_this_run=10)
    second = record_run_result("TEST_API", "incremental", "OK", datetime(2026, 7, 27), 20, outliers_this_run=3)

    assert second.cumulative_rows == 120
    assert second.cumulative_outliers == 13
    assert second.cumulative_runs == 2
    assert second.last_dtcr_processed == datetime(2026, 7, 27)


def test_ko_run_does_not_advance_watermark_but_counts_as_a_run(isolated_state_dir):
    record_run_result("TEST_API", "initial", "OK", datetime(2026, 7, 20), 100, outliers_this_run=10)
    after_ko = record_run_result("TEST_API", "incremental", "KO", None, 0, outliers_this_run=0)

    assert after_ko.last_dtcr_processed == datetime(2026, 7, 20)  # inchangé
    assert after_ko.cumulative_rows == 100                          # inchangé
    assert after_ko.cumulative_runs == 2                             # compte quand même comme un run


def test_seed_initial_state_does_not_overwrite_existing(isolated_state_dir):
    record_run_result("TEST_API", "initial", "OK", datetime(2026, 7, 20), 100, outliers_this_run=10)
    seed_initial_state("TEST_API", datetime(2020, 1, 1))  # ne doit rien changer

    state = get_run_state("TEST_API")
    assert state.last_dtcr_processed == datetime(2026, 7, 20)


def test_seed_initial_state_creates_when_absent(isolated_state_dir):
    seed_initial_state("NEW_API", datetime(2026, 7, 1))
    state = get_run_state("NEW_API")
    assert state.last_dtcr_processed == datetime(2026, 7, 1)
    assert state.last_run_status == "OK"
    assert state.last_run_mode == "INITIAL"
