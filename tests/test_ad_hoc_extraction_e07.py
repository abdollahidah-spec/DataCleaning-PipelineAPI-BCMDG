"""
Extraction ad hoc E07_FS (e07_fs/ad_hoc_extraction.py) : même contrat que l'outil
E11 — nettoyage des champs dont les colonnes sont présentes, les autres ignorés.
"""
import pandas as pd

from e07_fs.ad_hoc_extraction import run_ad_hoc_extraction

CONFIG_PATH = "e07_fs/config/E07_FS.yaml"


def test_extraction_depuis_fichier_local(tmp_path):
    out_path = tmp_path / "export.csv"
    result_path = run_ad_hoc_extraction(
        CONFIG_PATH, input_file="tests/fixtures/e07_fs_sample.csv", output_path=str(out_path)
    )

    assert result_path == out_path
    df = pd.read_csv(out_path, sep=";", dtype=str, keep_default_na=False)
    cols = list(df.columns)
    for source, nettoyee in [("TypeSwfit", "TypeSwift_Normalisé"), ("Devise", "Devise_Normalisée"),
                             ("NatureEconomique", "NatureEconomique_Normalisé"), ("Pays", "Pays_Normalisé")]:
        assert cols.index(nettoyee) == cols.index(source) + 1
    assert len(df) == 7


def test_extraction_partielle_ignore_les_champs_absents(tmp_path):
    """Une requête qui ne ramène que Devise et ModeReglement : seuls ces deux champs
    sont nettoyés, et la validation des transactions (colonnes absentes) est ignorée."""
    partiel = tmp_path / "partiel.csv"
    pd.DataFrame({"RefBanque": ["BANK01"], "ReferenceTransaction": ["TX1"],
                  "Devise": ["usd"], "ModeReglement": ["TR"]}).to_csv(partiel, sep=";", index=False)

    out_path = tmp_path / "export_partiel.csv"
    run_ad_hoc_extraction(CONFIG_PATH, input_file=str(partiel), output_path=str(out_path))

    df = pd.read_csv(out_path, sep=";", dtype=str, keep_default_na=False)
    assert df["Devise_Normalisée"].iloc[0] == "USD"
    assert df["ModeReglement_Normalisé"].iloc[0] == "TL"
    assert "Pays_Normalisé" not in df.columns
