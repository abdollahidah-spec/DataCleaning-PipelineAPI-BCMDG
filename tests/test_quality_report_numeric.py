"""
Vérifie que le rapport de qualité incorpore bien les anomalies de cohérence
numérique dans le taux de conformité/outliers global — pas seulement les
outliers des champs catégoriels (bug trouvé et corrigé : _NC_row_conforme,
posé par NumericCoherenceProcessor sur l'index d'origine, doit être utilisé).
"""
from datetime import datetime

import pandas as pd

from shared.quality_report import compute_quality_report


def test_numeric_anomalies_count_toward_global_conformity():
    df_final = pd.DataFrame({
        "RefBanque": ["B1", "B1", "B2"],
        "Devise_Normalisée": ["USD", "EUR", "USD"],   # aucun OUTLIER catégoriel
        "_NC_row_conforme": [True, False, True],       # 1 ligne non-conforme (numérique)
    })
    report = compute_quality_report(
        api_id="TEST", mode="file",
        started_at=datetime(2026, 1, 1, 10, 0), finished_at=datetime(2026, 1, 1, 10, 1),
        df_final=df_final,
        categorical_fields=[("Devise", "Devise_Normalisée", {"taux_normalisation_pct": 100.0})],
        numeric_anomalies_df=None,
    )

    assert report.n_outlier_rows == 1
    assert report.taux_conformite_pct == round(100 * 2 / 3, 2)


def test_no_numeric_column_falls_back_to_categorical_only():
    df_final = pd.DataFrame({
        "RefBanque": ["B1", "B2"],
        "Devise_Normalisée": ["USD", "OUTLIER"],
    })
    report = compute_quality_report(
        api_id="TEST", mode="file",
        started_at=datetime(2026, 1, 1, 10, 0), finished_at=datetime(2026, 1, 1, 10, 1),
        df_final=df_final,
        categorical_fields=[("Devise", "Devise_Normalisée", {"taux_normalisation_pct": 50.0})],
        numeric_anomalies_df=None,
    )
    assert report.n_outlier_rows == 1
