import pandas as pd

from shared.writer import INSTRUCTIONS_COLS, empty_instructions_df, write_excel_sheets


def test_write_excel_sheets_only_writes_given_frames(tmp_path):
    frames = {"Mapping": pd.DataFrame({"a": [1, 2]})}
    path = write_excel_sheets(frames, tmp_path / "out.xlsx")

    xls = pd.ExcelFile(path)
    assert xls.sheet_names == ["Mapping"]


def test_write_excel_sheets_includes_instructions_when_given(tmp_path):
    frames = {
        "Mapping": pd.DataFrame({"a": [1]}),
        "Instructions": pd.DataFrame({"Champ": ["Devise"], "Input": ["ZZZ"], "Label_Attendu": [""]}),
    }
    path = write_excel_sheets(frames, tmp_path / "out.xlsx")

    xls = pd.ExcelFile(path)
    assert "Instructions" in xls.sheet_names
    df = pd.read_excel(path, sheet_name="Instructions")
    assert list(df.columns) == ["Champ", "Input", "Label_Attendu"]


def test_empty_instructions_df_has_expected_columns():
    df = empty_instructions_df()
    assert list(df.columns) == INSTRUCTIONS_COLS
    assert df.empty
