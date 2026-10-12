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
  /* Un identifiant long (nom de table, chemin) ne doit pas déborder de sa
     colonne : coupure autorisée à l'intérieur du mot, dans les cellules. */
  td code { -pdf-word-wrap: CJK; font-size: 7.5pt; }
  pre { font-family: Courier, monospace; font-size: 7.5pt; background-color: #f5f5f5;
        border: 0.4pt solid #cccccc; padding: 2mm; margin: 2.5mm 0 3.5mm 0;
        page-break-inside: avoid; }
  pre code { font-size: 7.5pt; }
  .saut { page-break-before: always; }
  #pied_de_page { font-size: 7.5pt; color: #666666; text-align: center; }
"""


def _tableaux_markdown(texte: str) -> list:
    """Lignes de chaque tableau markdown du document, dans l'ordre."""
    tableaux, courant = [], []
    for ligne in texte.splitlines():
        if ligne.lstrip().startswith("|"):
            courant.append(ligne.strip())
        elif courant:
            tableaux.append(courant)
            courant = []
    if courant:
        tableaux.append(courant)
    return tableaux


def _largeur_texte_pt(texte: str, gras: bool = False, fixe: bool = False) -> float:
    """Largeur approchée d'un texte, en points, pour la police du document."""
    par_caractere = 4.7 if fixe else (5.1 if gras else 4.3)
    large = sum(1 for c in texte if ord(c) > 0x2000)      # tirets longs, guillemets typographiques
    return (len(texte) - large) * par_caractere + large * 8.0


def _largeurs(table: list) -> list:
    """Largeurs de colonnes, en pourcentages ENTIERS dont la somme fait 100.

    Trois contraintes, dans cet ordre :

    1. l'intitulé d'une colonne courte doit tenir sur une ligne, et aucune cellule
       ne doit contenir un mot plus large que sa colonne — le moteur coupe aux
       espaces, pas à l'intérieur d'un mot, et ne rogne pas ce qui dépasse : le
       texte se dessinerait par-dessus la colonne voisine ;
    2. l'espace restant est réparti au prorata du volume de texte ;
    3. aucune colonne ne dépasse 2,5 fois son minimum, pour qu'une colonne de
       libellés longs n'écrase pas les autres.

    Les pourcentages sont entiers : les valeurs décimales sont ignorées par le
    moteur, qui retombe alors sur une répartition par défaut.
    """
    lignes = [l.strip().strip("|").split("|") for l in table if "---" not in l]
    if not lignes:
        return []
    nb = max(len(l) for l in lignes)
    UTILE_PT, MARGE_PT = 499.0, 16.0

    minimums, poids = [], []
    for i in range(nb):
        cellules = [l[i].strip() for l in lignes if len(l) > i]
        entete = cellules[0] if cellules else ""
        besoin = _largeur_texte_pt(entete, gras=True) if len(entete) <= 16 else 0.0
        for rang, cellule in enumerate(cellules):
            fixe = cellule.count("`") >= 2
            propre = cellule.replace("`", "").replace("*", "")
            for mot in propre.split():
                besoin = max(besoin, _largeur_texte_pt(mot, gras=(rang == 0), fixe=fixe))
        longueurs = [len(c) for c in cellules] or [1]
        maxi, moyenne = max(longueurs), sum(longueurs) / len(longueurs)
        minimums.append(100 * (besoin + MARGE_PT) / UTILE_PT)
        poids.append(max(1.0, (maxi + 2 * moyenne) / 3))

    total_min = sum(minimums)
    if total_min >= 100:
        largeurs = [100 * m / total_min for m in minimums]
    else:
        reste, total_poids = 100 - total_min, sum(poids)
        largeurs = [m + reste * p / total_poids for m, p in zip(minimums, poids)]
        plafonds = [2.5 * m for m in minimums]
        rendu = sum(max(0.0, l - pl) for l, pl in zip(largeurs, plafonds))
        if rendu > 0:                                   # redistribue ce qui dépasse le plafond
            largeurs = [min(l, pl) for l, pl in zip(largeurs, plafonds)]
            marge = [pl - l for l, pl in zip(largeurs, plafonds)]
            total_marge = sum(marge) or 1.0
            largeurs = [l + rendu * m / total_marge for l, m in zip(largeurs, marge)]

    entiers = [max(6, int(round(l))) for l in largeurs]
    entiers[-1] += 100 - sum(entiers)
    return entiers


def _largeurs_de_colonnes(html: str, texte_markdown: str) -> str:
    """Applique les largeurs calculées aux cellules d'en-tête de chaque tableau.

    Les largeurs doivent être portées par les cellules d'en-tête : un `<colgroup>`
    est ignoré par le moteur de rendu, qui retombe alors sur une répartition par
    défaut — la première colonne est étranglée et son texte se dessine par-dessus
    la colonne voisine.
    """
    import re

    specs = [_largeurs(t) for t in _tableaux_markdown(texte_markdown)]
    morceaux = html.split('<table repeat="1">')
    rendu = morceaux[0]
    for i, morceau in enumerate(morceaux[1:]):
        largeurs = list(specs[i]) if i < len(specs) else []

        def poser_largeur(correspondance, largeurs=largeurs):
            return f'<th width="{largeurs.pop(0)}%">' if largeurs else correspondance.group(0)

        # Seules les cellules de la ligne d'en-tête portent la largeur.
        entete_fin = morceau.find("</thead>")
        if entete_fin != -1 and largeurs:
            entete = re.sub(r"<th>", poser_largeur, morceau[:entete_fin])
            morceau = entete + morceau[entete_fin:]
        rendu += '<table repeat="1">' + morceau
    return rendu


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
    corps = _largeurs_de_colonnes(corps, texte)
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
