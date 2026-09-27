"""
Lecture d'un fichier local (`--input`) : l'encodage et le séparateur réels du fichier
fourni ne sont pas toujours ceux de la config.

Cas réel du 27/09/2026 : un extrait E07 exporté depuis Excel sur un poste Windows
francophone (cp1252) faisait échouer le run avec « 'utf-8' codec can't decode byte
0xf8 ». Un testeur ne doit pas avoir à réencoder son fichier à la main.
"""
import pandas as pd
import pytest

from shared.db_connector import load_file
from shared.errors import DataSourceError

CFG = {"input": {"sep": ";", "encoding": "utf-8-sig"}}
LIGNES = "RefBanque;Beneficiaire\nBANK01;Société Générale\n"


def test_csv_utf8_lu_sans_avertissement(tmp_path, capsys):
    fichier = tmp_path / "extrait.csv"
    fichier.write_text(LIGNES, encoding="utf-8-sig")

    df = load_file(str(fichier), CFG)

    assert df["Beneficiaire"].iloc[0] == "Société Générale"
    assert "AVERTISSEMENT" not in capsys.readouterr().out


def test_csv_cp1252_lu_avec_repli(tmp_path, capsys):
    """Export Excel/SQL Server sur poste francophone : accents en cp1252."""
    fichier = tmp_path / "extrait.csv"
    fichier.write_bytes(LIGNES.encode("cp1252"))

    df = load_file(str(fichier), CFG)

    assert df["Beneficiaire"].iloc[0] == "Société Générale"
    assert "cp1252" in capsys.readouterr().out


def test_csv_octet_invalide_partout_lu_en_dernier_recours(tmp_path):
    """Octet impossible en UTF-8 (0xf8, le cas signalé) : la lecture aboutit quand même."""
    fichier = tmp_path / "extrait.csv"
    fichier.write_bytes(b"RefBanque;Beneficiaire\nBANK01;Soci\xf8t\xe9\n")

    df = load_file(str(fichier), CFG)

    assert list(df.columns) == ["RefBanque", "Beneficiaire"]
    assert len(df) == 1


def test_csv_avec_virgule_comme_separateur(tmp_path, capsys):
    fichier = tmp_path / "extrait.csv"
    fichier.write_text("RefBanque,Beneficiaire\nBANK01,ABC\n", encoding="utf-8")

    df = load_file(str(fichier), CFG)

    assert list(df.columns) == ["RefBanque", "Beneficiaire"]
    assert "séparateur" in capsys.readouterr().out


def test_csv_une_seule_colonne_reste_accepte(tmp_path):
    """Un fichier réellement mono-colonne ne doit pas être re-découpé à tort."""
    fichier = tmp_path / "extrait.csv"
    fichier.write_text("RefBanque\nBANK01\n", encoding="utf-8")

    df = load_file(str(fichier), CFG)

    assert list(df.columns) == ["RefBanque"] and len(df) == 1


def test_format_non_supporte(tmp_path):
    fichier = tmp_path / "extrait.json"
    fichier.write_text("{}", encoding="utf-8")

    with pytest.raises(DataSourceError, match="Format de fichier non supporté"):
        load_file(str(fichier), CFG)


def test_fichier_introuvable():
    with pytest.raises(DataSourceError, match="introuvable"):
        load_file("chemin/inexistant.csv", CFG)
