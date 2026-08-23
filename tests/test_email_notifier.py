"""
Vérifie le gabarit d'email EXACT validé par le Business Analyst : corps texte brut
(pas HTML) avec les indicateurs delta pour l'OK, et pour le KO l'absence totale du
texte brut de l'exception/traceback dans le corps — seule la `category` (courte,
non technique) doit apparaître (shared/errors.py).
"""
from datetime import datetime

from shared.email_notifier import send_run_notification
from shared.errors import ConfigError
from shared.quality_report import QualityReport


def _quality_report(**overrides) -> QualityReport:
    defaults = dict(
        api_id="E11_RDCC", mode="incremental",
        started_at=datetime(2026, 7, 27, 10, 0), finished_at=datetime(2026, 7, 27, 10, 1, 30),
        n_rows=10, n_outlier_rows=2, taux_conformite_pct=80.0,
    )
    defaults.update(overrides)
    return QualityReport(**defaults)


def test_ok_email_dry_run_returns_true(capsys):
    ok = send_run_notification(status="OK", report=_quality_report(), dry_run=True)
    assert ok is True
    assert "EMAIL" in capsys.readouterr().out


def test_ok_email_body_matches_ba_template_structure():
    from shared.email_notifier import _build_ok_body, _build_ok_subject

    report = _quality_report()
    subject = _build_ok_subject("E11 – RDCC", "27/07/2026")
    body = _build_ok_body(report, "E11 – RDCC", "27/07/2026", pdf_paths=[])

    assert subject == "Pipeline E11 – RDCC : Rapport de qualité et rapport des outliers – 27/07/2026"
    assert body.startswith("Bonjour,")
    assert "INDICATEURS DE L'EXÉCUTION (DELTA)" in body
    assert "Nombre de lignes traitées (delta) : 10" in body
    assert "Taux de données conformes (delta) : 80,0 %" in body
    assert "Temps d'exécution (delta) : 01 min 30 s" in body
    assert body.strip().endswith("Cordialement,")


def test_ok_email_body_lists_pdf_report_names():
    from pathlib import Path

    from shared.email_notifier import _build_ok_body

    pdfs = [Path("Rapport_Qualite_E11_RDCC_20260727.pdf"), Path("Rapport_Outliers_E11_RDCC_20260727.pdf")]
    body = _build_ok_body(_quality_report(), "E11 – RDCC", "27/07/2026", pdf_paths=pdfs)

    assert "Rapport_Qualite_E11_RDCC_20260727.pdf" in body
    assert "Rapport_Outliers_E11_RDCC_20260727.pdf" in body


def test_ok_email_initial_mode_has_no_delta_suffix():
    from shared.email_notifier import _build_ok_body

    report = _quality_report(mode="initial")
    body = _build_ok_body(report, "E11 – RDCC", "27/07/2026", pdf_paths=[])

    assert "INDICATEURS DE L'EXÉCUTION\n" in body
    assert "(delta)" not in body


def test_ko_email_never_includes_raw_exception_text():
    from shared.email_notifier import _build_ko_body

    exc = ConfigError("Clé obligatoire manquante à la racine du YAML : 'input'. Chemin secret: C:/prod/db.conf")
    body = _build_ko_body("E11 – RDCC", "27/07/2026", exc)

    assert "Clé obligatoire manquante" not in body
    assert "db.conf" not in body
    assert ConfigError.category in body


def test_ko_email_falls_back_to_generic_category_for_plain_exceptions():
    from shared.email_notifier import _build_ko_body

    exc = RuntimeError("boom, some internal detail")
    body = _build_ko_body("E11 – RDCC", "27/07/2026", exc)

    assert "boom" not in body
    assert "Erreur inattendue lors du traitement" in body


def test_ko_email_dry_run_returns_true():
    ok = send_run_notification(status="KO", report=None, exc=ConfigError("detail"), dry_run=True)
    assert ok is True


def test_new_outliers_counter_is_not_hardcoded_to_e11_rule_names():
    """Régression : le comptage des "nouveaux outliers" doit fonctionner pour
    N'IMPORTE QUELLE clé de règle non-catégorielle dans outliers_by_champ (pas
    une liste figée des 4 règles d'E11) — sinon les règles d'une future API (ex:
    AMOUNT_POSITIVE pour E09) sont silencieusement absentes du corps de l'email."""
    from shared.email_notifier import _new_values_counters

    report = _quality_report(
        per_field_stats={"Devise": {"n_new_distinct": 3, "n_new_normalized": 2, "n_new_outliers": 1}},
        outliers_by_champ={"Devise": 20, "AMOUNT_POSITIVE": 5, "DATE_VALIDITY": 8},
    )
    n_new_distinct, n_new_normalized, n_new_outliers = _new_values_counters(report)
    assert n_new_outliers == 1 + 5 + 8
