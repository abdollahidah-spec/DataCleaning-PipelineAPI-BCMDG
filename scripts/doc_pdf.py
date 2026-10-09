"""
scripts/doc_pdf.py
====================
Convertit un document markdown du repo en PDF, à côté du fichier source.

Sert à diffuser un document (architecture, cadrage, README) à des lecteurs qui ne
travaillent pas dans le dépôt. Le markdown reste la source : le PDF est un
artefact régénérable, non versionné (voir .gitignore).

Usage :
    python -m scripts.doc_pdf docs/ARCHITECTURE_PROJET.md
    python -m scripts.doc_pdf docs/ARCHITECTURE_PROJET.md --output /chemin/sortie.pdf
    python -m scripts.doc_pdf e07_fs/README.md e10_fe/README.md

Le moteur de rendu est celui des rapports de run (markdown + xhtml2pdf), avec en
plus les blocs de code délimités par des triples accents graves, et une feuille de
style adaptée à un document long : titres hiérarchisés, tableaux lisibles sur toute
la largeur, code en police à espacement fixe.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import markdown as _markdown
from xhtml2pdf import pisa

# Mise en forme sobre, en noir et niveaux de gris : document destiné à être imprimé
# ou lu en PDF, sans aplats de couleur. Les en-têtes de tableau sont répétés en haut
# de chaque page et une ligne de tableau n'est pas coupée entre deux pages.
_CSS = """
  @page {
    size: A4 portrait;
    margin: 2cm 1.7cm 2.2cm 1.7cm;
    @frame pied { -pdf-frame-content: pied_de_page; left: 1.7cm; bottom: 1.3cm;
                  width: 17.6cm; height: 0.8cm; }
  }
  body { font-family: Helvetica, Arial, sans-serif; font-size: 9.5pt; color: #000000;
         line-height: 1.4; }
  h1 { font-size: 15pt; font-weight: bold; margin: 0 0 1mm 0; padding-bottom: 2mm;
       border-bottom: 1pt solid #000000; }
  h2 { font-size: 11pt; font-weight: bold; margin: 7mm 0 2.5mm 0; padding-bottom: 1mm;
       border-bottom: 0.5pt solid #888888; }
  h3 { font-size: 9.5pt; font-weight: bold; margin: 4.5mm 0 1.5mm 0; }
  p { margin: 0 0 2.5mm 0; text-align: justify; }
  ul, ol { margin: 1mm 0 3mm 5mm; }
  li { margin-bottom: 1mm; }
  hr { border: 0; border-top: 0.5pt solid #cccccc; margin: 5mm 0; }
  table { width: 100%; border-collapse: collapse; margin: 2.5mm 0 4mm 0; }
  thead { -pdf-keep-with-next: true; }
  tr { page-break-inside: avoid; }
  th { background-color: #e6e6e6; color: #000000; font-size: 8pt; font-weight: bold;
       text-align: left; padding: 1.5mm 1.8mm; border: 0.4pt solid #999999; }
  td { font-size: 8pt; padding: 1.4mm 1.8mm; border: 0.4pt solid #cccccc;
       vertical-align: top; }
  code { font-family: Courier, monospace; font-size: 8.5pt; }
  pre { font-family: Courier, monospace; font-size: 7.5pt; background-color: #f5f5f5;
        border: 0.4pt solid #cccccc; padding: 2mm; margin: 2.5mm 0 3.5mm 0;
        page-break-inside: avoid; }
  pre code { font-size: 7.5pt; }
  .saut { page-break-before: always; }
  #pied_de_page { font-size: 7.5pt; color: #666666; text-align: center; }
"""


def markdown_vers_pdf(source: Path, sortie: Path | None = None) -> Path:
    """Rend `source` en PDF ; par défaut à côté du markdown, même nom."""
    if not source.exists():
        raise FileNotFoundError(f"Document introuvable : {source}")

    sortie = sortie or source.with_suffix(".pdf")
    texte = source.read_text(encoding="utf-8")
    # Marque de saut de page : invisible dans le rendu markdown, honorée dans le PDF.
    # Sert à empêcher qu'un tableau commencé en bas de page soit coupé en deux.
    texte = texte.replace("<!-- saut-de-page -->", '<div class="saut"></div>')
    corps = _markdown.markdown(texte, extensions=["tables", "fenced_code", "sane_lists"])
    # `repeat="1"` : un tableau qui continue sur la page suivante y réaffiche sa ligne
    # d'en-tête. Sans cet attribut, la suite du tableau apparaît sans ses intitulés de
    # colonnes, et devient illisible.
    corps = corps.replace("<table>", '<table repeat="1">')
    pied = ('<div id="pied_de_page">page <pdf:pagenumber> / <pdf:pagecount></div>')
    html = (f"<html><head><meta charset='utf-8'><style>{_CSS}</style></head>"
            f"<body>{pied}{corps}</body></html>")

    sortie.parent.mkdir(parents=True, exist_ok=True)
    with open(sortie, "wb") as f:
        resultat = pisa.CreatePDF(html, dest=f, encoding="utf-8")
    if resultat.err:
        raise RuntimeError(f"Échec de la conversion PDF de {source} ({resultat.err} erreur(s))")
    return sortie


def main() -> int:
    parser = argparse.ArgumentParser(description="Convertit un document markdown du repo en PDF")
    parser.add_argument("documents", nargs="+", help="Fichiers markdown à convertir")
    parser.add_argument("--output", default=None,
                        help="Chemin du PDF (uniquement avec un seul document en entrée)")
    args = parser.parse_args()

    if args.output and len(args.documents) > 1:
        print("ERREUR : --output ne s'utilise qu'avec un seul document.", file=sys.stderr)
        return 2

    for chemin in args.documents:
        try:
            produit = markdown_vers_pdf(Path(chemin), Path(args.output) if args.output else None)
        except Exception as exc:
            print(f"ERREUR : {exc}", file=sys.stderr)
            return 1
        print(f"{chemin} -> {produit} ({produit.stat().st_size / 1024:.0f} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
