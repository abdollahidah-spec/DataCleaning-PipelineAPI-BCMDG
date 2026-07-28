import pandas as pd
import pytest

from e11_rdcc.apply_corrections import apply_corrections
from e11_rdcc.fields.devise import load_warm_start_devise, treating_devise


CONFIG_PATH = "e11_rdcc/config/E11_RDCC.yaml"


def _write_instructions_xlsx(path, rows):
    df = pd.DataFrame(rows, columns=["Champ", "Input", "Label_Attendu"])
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="Instructions", index=False)


def test_apply_corrections_routes_by_champ_and_updates_devise_cache(tmp_path, isolated_referentiel_dir):
    xlsx = tmp_path / "E11_RDCC_mapping_outliers_test.xlsx"
    _write_instructions_xlsx(xlsx, [
        {"Champ": "Devise", "Input": "ZZZ", "Label_Attendu": "EUR"},
    ])

    applied = apply_corrections(xlsx, "E11_RDCC", CONFIG_PATH)

    assert applied["Devise"] == {"ZZZ": "EUR"}
    cache = load_warm_start_devise("E11_RDCC")
    assert cache["ZZZ"] == "EUR"


def test_apply_corrections_round_trip_resolves_as_warm(tmp_path, isolated_referentiel_dir):
    xlsx = tmp_path / "E11_RDCC_mapping_outliers_test.xlsx"
    _write_instructions_xlsx(xlsx, [
        {"Champ": "Devise", "Input": "ZZZ", "Label_Attendu": "EUR"},
    ])
    apply_corrections(xlsx, "E11_RDCC", CONFIG_PATH)

    df = pd.DataFrame({"Devise": ["ZZZ"], "NomCorrespondant": ["NA"]})
    out = treating_devise(df, api_id="E11_RDCC", warm_start=True)

    assert out.loc[0, "Devise_Normalisée"] == "EUR"
    assert out.loc[0, "Devise_method"] == "WARM"


def test_apply_corrections_skips_unknown_champ(tmp_path, isolated_referentiel_dir, capsys):
    xlsx = tmp_path / "E11_RDCC_mapping_outliers_test.xlsx"
    _write_instructions_xlsx(xlsx, [
        {"Champ": "ChampInconnu", "Input": "X", "Label_Attendu": "Y"},
        {"Champ": "Devise", "Input": "ZZZ", "Label_Attendu": "EUR"},
    ])

    applied = apply_corrections(xlsx, "E11_RDCC", CONFIG_PATH)

    assert "ChampInconnu" not in applied
    assert applied["Devise"] == {"ZZZ": "EUR"}
    captured = capsys.readouterr()
    assert "Champ inconnu" in captured.out


def test_apply_corrections_skips_numeric_family(tmp_path, isolated_referentiel_dir, capsys):
    xlsx = tmp_path / "E11_RDCC_mapping_outliers_test.xlsx"
    _write_instructions_xlsx(xlsx, [
        {"Champ": "SoldesRDCC", "Input": "X", "Label_Attendu": "Y"},
    ])

    applied = apply_corrections(xlsx, "E11_RDCC", CONFIG_PATH)

    assert applied.get("SoldesRDCC") is None
    captured = capsys.readouterr()
    assert "warm-start" in captured.out


def test_apply_corrections_missing_columns_raises(tmp_path):
    xlsx = tmp_path / "bad.xlsx"
    df = pd.DataFrame({"Input": ["a"], "Label_Attendu": ["b"]})  # Champ manquant
    with pd.ExcelWriter(xlsx, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="Instructions", index=False)

    with pytest.raises(ValueError, match="Colonnes manquantes"):
        apply_corrections(xlsx, "E11_RDCC", CONFIG_PATH)
