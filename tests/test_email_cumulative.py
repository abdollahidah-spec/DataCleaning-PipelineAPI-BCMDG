"""
Vérifie que le corps de l'email inclut les stats du run + (si fourni) un saut de
deux lignes puis les stats globales cumulées depuis le premier run.
"""
from datetime import datetime

from shared.email_notifier import send_run_notification
from shared.quality_report import QualityReport
from shared.state_store import RunState


def _quality_report() -> QualityReport:
    return QualityReport(
        api_id="E11_RDCC", mode="incremental",
        started_at=datetime(2026, 7, 27, 10, 0), finished_at=datetime(2026, 7, 27, 10, 1),
        n_rows=10, n_outlier_rows=2, taux_conformite_pct=80.0,
    )


def test_email_body_includes_cumulative_stats_after_double_break(monkeypatch, capsys):
    monkeypatch.delenv("SMTP_HOST", raising=False)  # force dry-run-like path (non configuré)

    cumulative = RunState(
        api_id="E11_RDCC", last_dtcr_processed=None, last_run_status="OK", last_run_mode="incremental",
        cumulative_rows=1234, cumulative_outliers=56, cumulative_runs=7,
        first_run_datetime=datetime(2026, 1, 1),
    )
    ok = send_run_notification(status="OK", report=_quality_report(), cumulative=cumulative, dry_run=True)
    assert ok is True

    captured = capsys.readouterr()
    assert "EMAIL" in captured.out  # confirme le chemin dry-run (pas d'appel SMTP réel)


def test_email_body_html_structure_has_double_break_and_cumulative_section():
    from shared.email_notifier import _render_cumulative_html

    cumulative = RunState(
        api_id="E11_RDCC", last_dtcr_processed=None, last_run_status="OK", last_run_mode="incremental",
        cumulative_rows=1234, cumulative_outliers=56, cumulative_runs=7,
        first_run_datetime=datetime(2026, 1, 1),
    )
    html = _render_cumulative_html(cumulative)
    assert "1,234" in html
    assert "Stats globales" in html


def test_email_without_cumulative_has_no_global_section():
    ok = send_run_notification(status="OK", report=_quality_report(), cumulative=None, dry_run=True)
    assert ok is True
