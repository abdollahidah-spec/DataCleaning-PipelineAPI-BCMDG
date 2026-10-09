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

_CSS = """
  @page { size: A4 portrait; margin: 1.5cm 1.3cm 1.6cm 1.3cm; }
  body { font-family: Helvetica, Arial, sans-serif; font-size: 9.5pt; color: #1a1a1a; line-height: 1.45; }
  h1 { font-size: 17pt; color: #0b3d61; margin: 0 0 2mm 0; }
  h2 { font-size: 12.5pt; color: #0b3d61; margin: 7mm 0 2mm 0; border-bottom: 0.6pt solid #0b3d61;
       padding-bottom: 1mm; }
  h3 { font-size: 10.5pt; color: #104e78; margin: 5mm 0 1.5mm 0; }
  p, li { font-size: 9.5pt; }
  ul, ol { margin: 1mm 0 2mm 5mm; }
  li { margin-bottom: 0.8mm; }
  hr { border: 0; border-top: 0.5pt solid #c8d4de; margin: 5mm 0; }
  table { width: 100%; border-collapse: collapse; margin: 2mm 0 4mm 0; }
  th { background-color: #0b3d61; color: #ffffff; font-size: 8.5pt; text-align: left;
       padding: 1.6mm 2mm; }
  td { font-size: 8.5pt; padding: 1.4mm 2mm; border-bottom: 0.4pt solid #dde5ec;
       vertical-align: top; }
  tr.alt td { background-color: #f4f7fa; }
  code { font-family: Courier, monospace; font-size: 8.5pt; background-color: #f1f4f7; }
  pre { font-family: Courier, monospace; font-size: 8pt; background-color: #f6f8fa;
        border: 0.4pt solid #dde5ec; padding: 2mm; margin: 2mm 0 3mm 0; }
  pre code { background-color: transparent; }
"""


def markdown_vers_pdf(source: Path, sortie: Path | None = None) -> Path:
    """Rend `source` en PDF ; par défaut à côté du markdown, même nom."""
    if not source.exists():
        raise FileNotFoundError(f"Document introuvable : {source}")

    sortie = sortie or source.with_suffix(".pdf")
    corps = _markdown.markdown(source.read_text(encoding="utf-8"),
                               extensions=["tables", "fenced_code", "sane_lists"])
    html = f"<html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{corps}</body></html>"

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
