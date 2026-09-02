"""
shared/pdf_report.py
======================
Conversion Markdown -> PDF, pure Python (aucun binaire externe requis — pas de
wkhtmltopdf/LaTeX à installer sur le poste qui exécute la pipeline) : utilisé
pour les rapports envoyés en pièce jointe email (Rapport_Qualite, Rapport_Outliers,
voir e11_rdcc/reports.py pour le contenu).
"""
from __future__ import annotations

from pathlib import Path

import markdown as _markdown
from xhtml2pdf import pisa

_CSS = """
<style>
  @page { size: A4 portrait; margin: 1.4cm 1.2cm; }
  body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1a1a1a; }
  h1 { font-size: 16pt; margin-bottom: 4pt; }
  h2 { font-size: 12.5pt; margin-top: 14pt; margin-bottom: 4pt; }
  h3 { font-size: 10.5pt; margin-top: 11pt; margin-bottom: 3pt; color: #1a3d6d; }
  p { margin: 3pt 0; }
  table { border-collapse: collapse; width: 100%; margin: 4pt 0 10pt 0; }
  /* Padding serré + police réduite : un tableau de détail doit tenir un maximum
     de lignes par page, sinon 20 outliers occupent 3 pages (retour testeur). */
  th, td { border: 0.5pt solid #b0b0b0; padding: 2.5pt 4pt; text-align: left;
           font-size: 8.5pt; vertical-align: top; }
  th { background-color: #dce6f1; font-weight: bold; }
  td.num, th.num { text-align: right; }
  tr.alt td { background-color: #f4f6f8; }
  em { color: #444444; }
</style>
"""


def markdown_to_pdf(markdown_text: str, out_path: Path) -> Path:
    """
    Convertit un texte Markdown (titres, gras, italique, tableaux `| a | b |`) en
    PDF écrit sur disque. Lève RuntimeError avec un message explicite si xhtml2pdf
    rapporte une erreur de rendu (out_path n'est alors pas un PDF exploitable).
    """
    html_body = _markdown.markdown(markdown_text, extensions=["tables"])
    html = f"<html><head>{_CSS}</head><body>{html_body}</body></html>"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        result = pisa.CreatePDF(html, dest=f)
    if result.err:
        raise RuntimeError(
            f"Échec de génération du PDF '{out_path}' ({result.err} erreur(s) xhtml2pdf lors du rendu HTML->PDF)."
        )
    return out_path
