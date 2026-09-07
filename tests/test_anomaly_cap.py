"""
Plafonnement des onglets d'anomalies volumineux.

Contexte : E09 remonte 5,1 M lignes d'historique. Écrire des millions de lignes
d'anomalies en Excel coûte des dizaines de minutes (mesuré : ~2 min par 2,4 M
cellules, identique en pandas et en xlsxwriter direct), dépasse la limite d'Excel
et produit un onglet inexploitable. L'onglet est donc plafonné, et le détail
INTÉGRAL est déversé en CSV — aucune donnée n'est perdue.
"""
import pandas as pd
import pytest

from shared.base_api_pipeline import BaseApiPipeline


class _Pipeline(BaseApiPipeline):
    def _build_field_processors(self, cfg):
        return []


def _cfg(tmp_path, cap):
    ref = tmp_path / "ref.json"
    ref.write_text("{}", encoding="utf-8")
    return {
        "api_id": "E09_PE",
        "input": {"table_name": "T"},
        "fields": [{"name": "Devise", "type": "categorical",
                    "columns": {"field": "Devise", "field_out": "Devise_N",
                                "ref_transaction": "NumCredoc", "ref_banque": "RefBanque"},
                    "referentiel_path": str(ref)}],
        "output": {"local_dir": str(tmp_path / "out"), "classification_path": "x.xlsx"},
        "reports": {"max_anomaly_rows_excel": cap},
    }


@pytest.fixture(autouse=True)
def _no_output_base(monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", "")


def _anomalies(n):
    return pd.DataFrame({"NumCredoc": [f"CD{i}" for i in range(n)], "Rule": "DATE_VALIDITY"})


def test_sous_le_plafond_rien_ne_change(tmp_path):
    pipeline = _Pipeline(_cfg(tmp_path, cap=100), config_source="test")
    df = _anomalies(50)

    assert len(pipeline._cap_anomaly_sheet("Anomalies_Echeances", df)) == 50
    assert not list((tmp_path / "out").glob("*.csv"))


def test_au_dessus_du_plafond_onglet_tronque_et_csv_complet(tmp_path):
    pipeline = _Pipeline(_cfg(tmp_path, cap=100), config_source="test")
    df = _anomalies(2500)

    onglet = pipeline._cap_anomaly_sheet("Anomalies_Echeances", df)

    assert len(onglet) == 100                       # onglet Excel : extrait
    csv = tmp_path / "out" / "E09_PE_Anomalies_Echeances_complet.csv"
    assert csv.exists(), "le detail integral doit etre depose en CSV"
    assert len(pd.read_csv(csv, sep=";")) == 2500   # CSV : AUCUNE perte


def test_plafond_a_zero_desactive_le_mecanisme(tmp_path):
    pipeline = _Pipeline(_cfg(tmp_path, cap=0), config_source="test")
    df = _anomalies(5000)

    assert len(pipeline._cap_anomaly_sheet("Anomalies_Echeances", df)) == 5000
    assert not list((tmp_path / "out").glob("*.csv"))
