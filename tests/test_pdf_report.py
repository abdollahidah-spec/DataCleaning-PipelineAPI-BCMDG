"""
Génération du rapport (shared/pdf_report.py) : le PDF est accompagné de son
Markdown source, écrit à côté avec le même nom.

Le rapport est composé en Markdown puis converti ; conserver cette source permet de
comparer deux runs sans ouvrir de PDF, et de reprendre le rapport dans un autre
support sans recalculer les chiffres.
"""
import pytest

from shared.pdf_report import markdown_to_pdf

RAPPORT = """# Rapport de qualité des traitements

**E10_FE** — Initial Load

| Indicateur | Valeur |
|---|---|
| Lignes traitées | 2 981 229 |
| Taux de données conformes | 62,4 % |
"""


def test_pdf_et_markdown_ecrits_cote_a_cote(tmp_path):
    pdf = markdown_to_pdf(RAPPORT, tmp_path / "Rapport_Qualite_Outliers_E10_FE_20261009.pdf")
    md = pdf.with_suffix(".md")

    assert pdf.exists() and pdf.stat().st_size > 0
    assert md.exists()
    assert md.read_text(encoding="utf-8") == RAPPORT
    assert pdf.read_bytes()[:4] == b"%PDF"


def test_markdown_desactivable(tmp_path):
    pdf = markdown_to_pdf(RAPPORT, tmp_path / "rapport.pdf", keep_markdown=False)

    assert pdf.exists()
    assert not pdf.with_suffix(".md").exists()


def test_dossier_cree_au_besoin(tmp_path):
    pdf = markdown_to_pdf(RAPPORT, tmp_path / "Rapport" / "rapport.pdf")

    assert pdf.exists() and pdf.with_suffix(".md").exists()


def test_rendu_invalide_signale(tmp_path, monkeypatch):
    """Une erreur de rendu doit être signalée explicitement : le PDF produit ne serait
    pas exploitable."""
    import shared.pdf_report as pdf_report

    class _Resultat:
        err = 2

    monkeypatch.setattr(pdf_report.pisa, "CreatePDF", lambda *a, **k: _Resultat())
    with pytest.raises(RuntimeError, match="Échec de génération du PDF"):
        markdown_to_pdf(RAPPORT, tmp_path / "rapport.pdf")
