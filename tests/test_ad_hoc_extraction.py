"""
Vérifie l'outil d'extraction ad hoc : indépendant du run automatisé, applique le
nettoyage aux champs dont la colonne est présente, ignore silencieusement les
autres, écrit une extraction (colonnes + versions nettoyées).
"""
import pandas as pd

from e11_rdcc.ad_hoc_extraction import run_ad_hoc_extraction

CONFIG_PATH = "e11_rdcc/config/E11_RDCC.yaml"


def test_ad_hoc_extraction_from_local_file(tmp_path):
    out_path = tmp_path / "export.csv"
    result_path = run_ad_hoc_extraction(
        CONFIG_PATH, input_file="tests/fixtures/e11_rdcc_sample.csv", output_path=str(out_path)
    )

    assert result_path == out_path
    df = pd.read_csv(out_path, sep=";", dtype=str, keep_default_na=False)
    assert "NomCorrespondant_Normalisé" in df.columns
    assert "Devise_Normalisée" in df.columns
    # colonne nettoyée juste après la colonne source
    cols = list(df.columns)
    assert cols.index("NomCorrespondant_Normalisé") == cols.index("NomCorrespondant") + 1


def test_ad_hoc_extraction_partial_columns_skips_missing_fields(tmp_path):
    """Une requête qui ne couvre que Devise (pas NomCorrespondant ni les colonnes
    numériques) doit quand même produire une extraction, avec seulement Devise nettoyée."""
    partial_csv = tmp_path / "partial.csv"
    pd.DataFrame({"RefBanque": ["BANK01"], "Devise": ["USD"]}).to_csv(partial_csv, sep=";", index=False)

    out_path = tmp_path / "export_partial.csv"
    run_ad_hoc_extraction(CONFIG_PATH, input_file=str(partial_csv), output_path=str(out_path))

    df = pd.read_csv(out_path, sep=";", dtype=str, keep_default_na=False)
    assert "Devise_Normalisée" in df.columns
    assert "NomCorrespondant_Normalisé" not in df.columns
