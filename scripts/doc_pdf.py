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
plus les blocs de code délimités par des triples accents graves.

La typographie reprend celle d'un document LaTeX : la police Latin Modern — celle
des documents composés avec LaTeX, héritière de Computer Modern — un corps de
11 points et l'interligne standard de 13,6 points. Les fontes sont préparées par
`scripts/polices_latex.py` ; si elles sont indisponibles (poste hors réseau), la
conversion se poursuit avec les polices de base du PDF.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

import markdown as _markdown
from xhtml2pdf import pisa

# Corps du texte et interligne d'un document LaTeX en 11 points : \baselineskip
# vaut alors 13,6 points, soit un rapport de 1,24. Les tableaux et le code sont
# composés en 9 points, l'équivalent du \footnotesize de LaTeX.
TAILLE_CORPS = 11.0
TAILLE_TABLEAU = 9.0
INTERLIGNE = 1.24

# Largeur utile d'une page A4 avec les marges définies plus bas (595,3 pt moins
# deux fois 1,7 cm), et place perdue par cellule : marges internes et filets.
UTILE_PT = 499.0
MARGE_PT = 14.0

# Balise de tableau : porte la repetition de la ligne d'en-tete et sert de
# repere pour poser les sauts de page.
_BALISE_TABLEAU = '<table repeat="1">'
_PASSES_MAX = 4

_POLICE_TEXTE = "LatinModern"
_POLICE_FIXE = "LatinModernMono"
_REPLI_TEXTE = "Times-Roman"
_REPLI_FIXE = "Courier"

# Rempli au premier appel : {nom de police -> fichier TrueType}, ou {} si les
# fontes ne sont pas disponibles.
_polices_cache: dict | None = None


def _polices() -> dict:
    """Prépare et enregistre les fontes LaTeX ; dictionnaire vide si indisponibles."""
    global _polices_cache
    if _polices_cache is not None:
        return _polices_cache

    try:
        from reportlab.lib.fonts import addMapping
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        from scripts.polices_latex import preparer

        fichiers = preparer()
        for nom, chemin in fichiers.items():
            pdfmetrics.registerFont(TTFont(nom, str(chemin)))
        # Correspondances gras / italique, nécessaires pour que les titres et les
        # en-têtes de tableau soient composés dans la bonne fonte.
        addMapping(_POLICE_TEXTE, 0, 0, _POLICE_TEXTE)
        addMapping(_POLICE_TEXTE, 1, 0, f"{_POLICE_TEXTE}-Bold")
        addMapping(_POLICE_TEXTE, 0, 1, f"{_POLICE_TEXTE}-Italic")
        addMapping(_POLICE_TEXTE, 1, 1, f"{_POLICE_TEXTE}-Bold")
        addMapping(_POLICE_FIXE, 0, 0, _POLICE_FIXE)
        addMapping(_POLICE_FIXE, 1, 0, _POLICE_FIXE)
        # Le moteur de rendu ne decouvre une fonte que par sa table des familles :
        # une regle `@font-face` l'amenerait a recopier le fichier dans un
        # repertoire temporaire, copie qu'il ne parvient pas a relire.
        from xhtml2pdf import default as moteur_defauts

        for famille in (_POLICE_TEXTE, f"{_POLICE_TEXTE}-Bold", f"{_POLICE_TEXTE}-Italic",
                        _POLICE_FIXE):
            if famille in fichiers or famille == _POLICE_TEXTE:
                moteur_defauts.DEFAULT_FONT[famille.lower()] = famille
        _polices_cache = fichiers
    except Exception as exc:                                   # poste hors réseau, cache absent
        print(f"Police LaTeX indisponible ({exc}) : composition avec les polices de base.",
              file=sys.stderr)
        _polices_cache = {}
    return _polices_cache


def _nom_police(fixe: bool = False) -> str:
    """Police à employer pour la mesure des textes, selon ce qui est disponible."""
    if not _polices():
        return _REPLI_FIXE if fixe else _REPLI_TEXTE
    return _POLICE_FIXE if fixe else _POLICE_TEXTE


def _css() -> str:
    """Feuille de style du document.

    Mise en forme sobre, en noir et niveaux de gris : document destiné à être
    imprimé ou lu en PDF, sans aplats de couleur. Les en-têtes de tableau sont
    répétés en haut de chaque page et une ligne de tableau n'est pas coupée entre
    deux pages. Les corps des titres suivent la progression de LaTeX : 18 pt pour
    le titre, 14 pt pour une section, 12 pt pour une sous-section.
    """
    texte = f'"{_nom_police()}", Times, serif'
    fixe = f'"{_nom_police(fixe=True)}", Courier, monospace'
    return f"""
  @page {{
    size: A4 portrait;
    margin: 2cm 1.7cm 2.2cm 1.7cm;
    @frame pied {{ -pdf-frame-content: pied_de_page; left: 1.7cm; bottom: 1.3cm;
                  width: 17.6cm; height: 0.8cm; }}
  }}
  body {{ font-family: {texte}; font-size: {TAILLE_CORPS}pt; color: #000000;
         line-height: {INTERLIGNE}; }}
  h1 {{ font-size: 18pt; font-weight: bold; margin: 0 0 1.5mm 0; padding-bottom: 2mm;
       border-bottom: 1pt solid #000000; }}
  h2 {{ font-size: 14pt; font-weight: bold; margin: 7mm 0 2.5mm 0; padding-bottom: 1mm;
       border-bottom: 0.5pt solid #888888; }}
  h3 {{ font-size: 12pt; font-weight: bold; margin: 5mm 0 1.5mm 0; }}
  p {{ margin: 0 0 2.5mm 0; text-align: justify; }}
  ul, ol {{ margin: 1mm 0 3mm 5mm; }}
  li {{ margin-bottom: 1mm; }}
  hr {{ border: 0; border-top: 0.5pt solid #cccccc; margin: 5mm 0; }}
  table {{ width: 100%; border-collapse: collapse; margin: 3mm 0 4.5mm 0; }}
  thead {{ -pdf-keep-with-next: true; }}
  tr {{ page-break-inside: avoid; }}
  th {{ background-color: #e6e6e6; color: #000000; font-size: {TAILLE_TABLEAU}pt;
       font-weight: bold; text-align: left; padding: 1.5mm 1.8mm;
       border: 0.4pt solid #999999; line-height: 1.2; }}
  td {{ font-size: {TAILLE_TABLEAU}pt; padding: 1.4mm 1.8mm; border: 0.4pt solid #cccccc;
       vertical-align: top; line-height: 1.2; }}
  code {{ font-family: {fixe}; font-size: {TAILLE_CORPS - 1}pt; }}
  /* Un identifiant long (nom de table, chemin) ne doit pas déborder de sa
     colonne : coupure autorisée à l'intérieur du mot, dans les cellules. */
  td code {{ -pdf-word-wrap: CJK; font-size: {TAILLE_TABLEAU}pt; }}
  th code {{ font-size: {TAILLE_TABLEAU}pt; }}
  pre {{ font-family: {fixe}; font-size: {TAILLE_TABLEAU}pt; background-color: #f5f5f5;
        border: 0.4pt solid #cccccc; padding: 2mm; margin: 2.5mm 0 3.5mm 0;
        line-height: 1.2; page-break-inside: avoid; }}
  pre code {{ font-size: {TAILLE_TABLEAU}pt; }}
  .saut {{ page-break-before: always; }}
  #pied_de_page {{ font-family: {texte}; font-size: 8.5pt; color: #666666;
                  text-align: center; }}
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
    """Largeur d'un texte, en points, dans la police et le corps des tableaux."""
    from reportlab.pdfbase import pdfmetrics

    police = _nom_police(fixe=fixe)
    if gras and not fixe and _polices():
        police = f"{_POLICE_TEXTE}-Bold"
    elif gras and not fixe:
        police = "Times-Bold"
    try:
        return pdfmetrics.stringWidth(texte, police, TAILLE_TABLEAU)
    except Exception:                                      # police non enregistrée
        return len(texte) * TAILLE_TABLEAU * 0.5


def _largeurs(table: list) -> list:
    """Largeurs de colonnes, en pourcentages ENTIERS dont la somme fait 100.

    Deux grandeurs par colonne :

    - un **minimum**, intangible : le mot le plus large qu'elle contient. Le moteur
      coupe aux espaces, pas à l'intérieur d'un mot, et ne rogne pas ce qui dépasse
      — un mot trop large se dessinerait par-dessus la colonne voisine ;
    - un **souhait** : la largeur qu'il faudrait pour que ses cellules courtes
      tiennent chacune sur une seule ligne. Une colonne de valeurs brèves ne doit
      pas être repliée alors que la place existe.

    La répartition sert d'abord les minimums, puis les souhaits si l'espace le
    permet, puis distribue le reste au prorata du volume de texte, aucune colonne
    ne dépassant 2,5 fois son minimum — pour qu'une colonne de libellés longs
    n'écrase pas les autres.

    Les pourcentages sont entiers : les valeurs décimales sont ignorées par le
    moteur, qui retombe alors sur une répartition par défaut.
    """
    lignes = [l.strip().strip("|").split("|") for l in table if "---" not in l]
    if not lignes:
        return []
    nb = max(len(l) for l in lignes)
    # Au-dela de cette largeur, une cellule est trop longue pour pretendre a une
    # seule ligne : elle sera repliee, et seuls ses mots comptent.
    plafond_souhait = 0.26 * UTILE_PT

    minimums, souhaits, poids = [], [], []
    for i in range(nb):
        cellules = [l[i].strip() for l in lignes if len(l) > i]
        besoin = souhait = 0.0
        for rang, cellule in enumerate(cellules):
            fixe = cellule.count("`") >= 2
            propre = cellule.replace("`", "").replace("*", "")
            entiere = _largeur_texte_pt(propre, gras=(rang == 0), fixe=fixe)
            if entiere <= plafond_souhait:
                souhait = max(souhait, entiere)
            for mot in propre.split():
                besoin = max(besoin, _largeur_texte_pt(mot, gras=(rang == 0), fixe=fixe))
        longueurs = [len(c) for c in cellules] or [1]
        maxi, moyenne = max(longueurs), sum(longueurs) / len(longueurs)
        minimums.append(100 * (besoin + MARGE_PT) / UTILE_PT)
        souhaits.append(100 * (max(besoin, souhait) + MARGE_PT) / UTILE_PT)
        poids.append(max(1.0, (maxi + 2 * moyenne) / 3))

    total_min = sum(minimums)
    if total_min >= 100:
        largeurs = [100 * m / total_min for m in minimums]
    else:
        largeurs = list(minimums)
        reste = 100 - total_min
        manques = [s - m for s, m in zip(souhaits, minimums)]
        total_manque = sum(manques)
        if total_manque > reste:                        # souhaits servis au prorata
            largeurs = [m + reste * q / total_manque for m, q in zip(largeurs, manques)]
            reste = 0.0
        else:
            largeurs = [m + q for m, q in zip(largeurs, manques)]
            reste -= total_manque
        if reste > 0:
            total_poids = sum(poids)
            largeurs = [l + reste * p / total_poids for l, p in zip(largeurs, poids)]
            plafonds = [max(2.5 * m, s) for m, s in zip(minimums, souhaits)]
            rendu = sum(max(0.0, l - pl) for l, pl in zip(largeurs, plafonds))
            if rendu > 0:                               # redistribue ce qui depasse le plafond
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


def _rendre(corps: str) -> bytes:
    """Compose le document et renvoie le PDF."""
    pied = '<div id="pied_de_page">page <pdf:pagenumber> / <pdf:pagecount></div>'
    html = (f"<html><head><meta charset='utf-8'><style>{_css()}</style></head>"
            f"<body>{pied}{corps}</body></html>")
    tampon = io.BytesIO()
    resultat = pisa.CreatePDF(html, dest=tampon, encoding="utf-8")
    if resultat.err:
        raise RuntimeError(f"Composition impossible ({resultat.err} erreur(s))")
    return tampon.getvalue()


def _poser_ruptures(corps: str, indices: set) -> str:
    """Insère un saut de page avant les tableaux désignés par leur rang."""
    morceaux = corps.split(_BALISE_TABLEAU)
    rendu = morceaux[0]
    for i, morceau in enumerate(morceaux[1:]):
        if i in indices:
            rendu += '<div class="saut"></div>'
        rendu += _BALISE_TABLEAU + morceau
    return rendu


def _tableaux_coupes(pdf: bytes, tableaux: list) -> list:
    """Rang des tableaux dont les lignes ne tiennent pas sur une même page.

    Le critère est l'intersection : toutes les lignes d'un tableau doivent se
    retrouver sur une page commune. Une cellule qui apparaît aussi dans le texte
    courant ne fausse donc pas le constat.
    """
    from pypdf import PdfReader

    pages = [re.sub(r"\s+", " ", page.extract_text() or "")
             for page in PdfReader(io.BytesIO(pdf)).pages]
    coupes = []
    for rang, table in enumerate(tableaux):
        communes = None
        for ligne in table[2:]:                       # en-tête et séparateur exclus
            cellules = ligne.strip().strip("|").split("|")
            repere = re.sub(r"\s+", " ", re.sub(r"[`*]", "", cellules[0])).strip()
            if len(repere) < 3:
                continue
            presentes = {n for n, page in enumerate(pages) if repere in page}
            if not presentes:                         # cellule non retrouvée : sans portée
                continue
            communes = presentes if communes is None else communes & presentes
        if communes is not None and not communes:
            coupes.append(rang)
    return coupes


def markdown_vers_pdf(source: Path, sortie: Path | None = None) -> Path:
    """Rend `source` en PDF ; par défaut à côté du markdown, même nom."""
    if not source.exists():
        raise FileNotFoundError(f"Document introuvable : {source}")

    sortie = sortie or source.with_suffix(".pdf")
    texte = source.read_text(encoding="utf-8")
    # Marque de saut de page : invisible dans le rendu markdown, honorée dans le PDF.
    # Sert à commencer une section importante en haut d'une page.
    texte = texte.replace("<!-- saut-de-page -->", '<div class="saut"></div>')
    corps = _markdown.markdown(texte, extensions=["tables", "fenced_code", "sane_lists"])
    # `repeat="1"` : un tableau qui continue sur la page suivante y réaffiche sa ligne
    # d'en-tête. Sans cet attribut, la suite du tableau apparaît sans ses intitulés de
    # colonnes, et devient illisible.
    corps = corps.replace("<table>", _BALISE_TABLEAU)
    corps = _largeurs_de_colonnes(corps, texte)

    # Un tableau scindé entre deux pages est illisible, et la hauteur occupée n'est
    # connue qu'après composition : le document est donc composé, les tableaux
    # scindés relevés, un saut de page posé devant chacun, et le tout recomposé.
    # Chaque passe peut déplacer un autre tableau : on conserve la meilleure.
    tableaux = _tableaux_markdown(texte)
    reference, ruptures, meilleur = corps, set(), None
    for _ in range(_PASSES_MAX):
        pdf = _rendre(corps)
        coupes = _tableaux_coupes(pdf, tableaux)
        if meilleur is None or len(coupes) < meilleur[0]:
            meilleur = (len(coupes), pdf)
        elargi = ruptures | set(coupes)
        if not coupes or elargi == ruptures:          # plus rien a gagner
            break
        ruptures = elargi
        corps = _poser_ruptures(reference, ruptures)
    if meilleur[0]:
        print(f"Avertissement : {meilleur[0]} tableau(x) encore scindé(s) dans {sortie.name}.",
              file=sys.stderr)

    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_bytes(meilleur[1])
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
