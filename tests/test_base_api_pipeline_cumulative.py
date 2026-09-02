"""
Vérifie que le rapport PDF reçoit des stats CUMULÉES — ensemble de l'historique
déjà persisté (état local) + le run en cours — et pas seulement le delta de la
dernière exécution (retour BA du 26/08/2026 : "les indicateurs doivent être
calculés sur l'ensemble de l'historique traité, et non uniquement sur le delta
de la dernière exécution"). Voir shared/base_api_pipeline.py::_attach_cumulative_stats.

Le corps de l'email, lui, continue d'utiliser les compteurs "cette exécution"
(shared/quality_report.py::compute_quality_report, shared/email_notifier.py) —
volontairement hors scope de ce fichier.
"""
from datetime import datetime

import pandas as pd
import pytest

from shared.base_api_pipeline import BaseApiPipeline
from shared.field_processor import CategoricalFieldProcessor, FieldResult
from shared.quality_report import QualityReport
from shared.state_store import record_run_result


class _MinimalPipeline(BaseApiPipeline):
    def _build_field_processors(self, cfg):
        return []


def _cfg(tmp_path):
    ref_path = tmp_path / "ref.json"
    ref_path.write_text("{}", encoding="utf-8")
    return {
        "api_id": "TEST_API",
        "input": {"table_name": "TestTable"},
        "fields": [{
            "name": "TestField", "type": "categorical",
            "columns": {"field": "TestField", "field_out": "TestField_Normalisé",
                        "ref_transaction": "Ref", "ref_banque": "RefBanque"},
            "referentiel_path": str(ref_path),
        }],
        "output": {"local_dir": str(tmp_path / "out"), "classification_path": "x.xlsx"},
    }


@pytest.fixture
def isolated_state_dir(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(state_dir))
    return state_dir


def _quality(n_rows: int, n_outliers: int) -> QualityReport:
    ts = datetime(2026, 8, 26, 10, 0)
    return QualityReport(api_id="TEST_API", mode="incremental", started_at=ts, finished_at=ts,
                          n_rows=n_rows, n_outlier_rows=n_outliers)


def _categorical_result(pairs: list[tuple[str, str]]):
    proc = CategoricalFieldProcessor(
        field_name="TestField", treating_fn=lambda *a, **k: None, treating_kwargs={},
        col_in="TestField", col_out="TestField_Normalisé", ref_banque_col="RefBanque",
        outlier_tag="OUTLIER", exclude_suffixes=(), clean_fn=lambda x: x,
    )
    df = pd.DataFrame(pairs, columns=["TestField", "TestField_Normalisé"])
    result = FieldResult(df=pd.DataFrame(), classification_df=df, outliers_df=pd.DataFrame(),
                          exclude_from_export=[], stats={}, sheet_names={})
    return proc, result


def test_cumulative_stats_add_persisted_history_to_current_run(tmp_path, isolated_state_dir):
    pipeline = _MinimalPipeline(_cfg(tmp_path), config_source="test")

    # "Semaine 1" déjà persistée : 100 lignes, 10 outliers.
    record_run_result("TEST_API", "initial", "OK", datetime(2026, 8, 19), 100, outliers_this_run=10)

    # "Semaine 2" : delta de 20 lignes / 3 outliers, PAS ENCORE persisté — comme
    # dans run(), _attach_cumulative_stats() s'exécute AVANT persist_state().
    # "A" arrive déjà propre (brut == normalisé), "B" a nécessité un traitement.
    quality = _quality(n_rows=20, n_outliers=3)
    results = [_categorical_result([("A", "A"), ("B", "Y"), ("C", "OUTLIER")])]

    pipeline._attach_cumulative_stats(quality, results, offline=False)

    assert quality.cumulative_n_rows == 120          # 100 + 20
    assert quality.cumulative_n_outlier_rows == 13   # 10 + 3
    assert quality.cumulative_taux_conformite_pct == round(100 * (120 - 13) / 120, 2)
    assert quality.cumulative_n_distinct_total == 3
    assert quality.cumulative_n_distinct_normalized == 2
    assert quality.cumulative_taux_normalisation_pct == round(100 * 2 / 3, 2)
    assert quality.cumulative_n_already_clean == 1          # seule "A" -> "A"
    assert quality.cumulative_taux_deja_propre_pct == round(100 * 1 / 3, 2)
    assert quality.cumulative_taux_nettoyage_pct == round(100 * 1 / 3, 2)          # "B" nettoyée avec succès
    assert quality.cumulative_taux_outliers_distinct_pct == round(100 * 1 / 3, 2)  # "C" outlier


def test_cumulative_stats_first_ever_run_equals_this_run(tmp_path, isolated_state_dir):
    """Aucun état persistant (tout premier run réel) : cumulative == ce run."""
    pipeline = _MinimalPipeline(_cfg(tmp_path), config_source="test")
    quality = _quality(n_rows=3, n_outliers=1)
    results = [_categorical_result([("A", "X")])]

    pipeline._attach_cumulative_stats(quality, results, offline=False)

    assert quality.cumulative_n_rows == 3
    assert quality.cumulative_n_outlier_rows == 1


def test_cumulative_stats_offline_mode_uses_this_run_only(tmp_path, isolated_state_dir):
    """Mode --input (test local, pas de vraie base) : pas d'historique persistant
    réel à additionner — ce run EST considéré comme tout l'historique, pour ne
    jamais afficher un rapport vide/à zéro lors d'un test offline."""
    pipeline = _MinimalPipeline(_cfg(tmp_path), config_source="test")
    record_run_result("TEST_API", "initial", "OK", datetime(2026, 8, 19), 100, outliers_this_run=10)

    quality = _quality(n_rows=9, n_outliers=6)
    results = [_categorical_result([("A", "X")])]

    pipeline._attach_cumulative_stats(quality, results, offline=True)

    assert quality.cumulative_n_rows == 9
    assert quality.cumulative_n_outlier_rows == 6


def test_file_mode_failure_never_sends_a_real_ko_email(tmp_path, isolated_state_dir, monkeypatch):
    """Régression : un run `--input` (test hors ligne) qui échoue envoyait un vrai
    email KO aux destinataires de production. Le mode fichier doit rester 100%
    hors ligne, y compris sur le chemin d'erreur."""
    sent = []

    class _FailingPipeline(_MinimalPipeline):
        def load_data(self, mode, override_input):
            raise RuntimeError("source illisible")

        def notify(self, *args, **kwargs):
            sent.append(kwargs.get("status", args[0] if args else None))

    pipeline = _FailingPipeline(_cfg(tmp_path), config_source="test")

    with pytest.raises(RuntimeError):
        pipeline.run(mode="auto", override_input="un/fichier.csv")

    assert sent == [], "aucune notification ne doit partir en mode fichier"
