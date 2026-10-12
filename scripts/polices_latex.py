"""
scripts/polices_latex.py
==========================
Met à disposition la police de LaTeX — Latin Modern, l'héritière de Computer
Modern — au format exploitable par le générateur de PDF du repo.

Pourquoi ce détour : Latin Modern n'est distribuée qu'en OpenType à contours
PostScript, que le moteur de rendu PDF ne sait pas lire. Les fichiers sont donc
convertis une fois en TrueType (contours quadratiques), puis mis en cache dans
`assets/fonts/` — dossier non versionné, régénérable.

Usage :
    python -m scripts.polices_latex            # télécharge si besoin, convertit, vérifie
    python -m scripts.polices_latex --verifier  # contrôle seulement ce qui est en cache
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent.parent / "assets" / "fonts"
MIROIR = "https://ctan.math.illinois.edu/fonts/lm/fonts/opentype/public/lm"

# Les quatre fontes nécessaires à un document : romain, gras, italique, chasse fixe.
FACES = {
    "LatinModern": "lmroman10-regular.otf",
    "LatinModern-Bold": "lmroman10-bold.otf",
    "LatinModern-Italic": "lmroman10-italic.otf",
    "LatinModernMono": "lmmono10-regular.otf",
}

# Caractères indispensables à un document français et technique.
CARACTERES_TESTES = "éèêàçùôûïœ«»—≈’°"


def _telecharger(nom: str) -> Path:
    cible = DOSSIER / nom
    if cible.exists():
        return cible
    DOSSIER.mkdir(parents=True, exist_ok=True)
    requete = urllib.request.Request(f"{MIROIR}/{nom}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(requete, timeout=60) as reponse:
        cible.write_bytes(reponse.read())
    return cible


def _en_truetype(source: Path, cible: Path) -> None:
    """Convertit les contours PostScript en contours quadratiques TrueType."""
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTFont, newTable

    police = TTFont(str(source))
    if police.sfntVersion != "OTTO":
        cible.write_bytes(source.read_bytes())
        return

    jeu = police.getGlyphSet()
    glyphes = {}
    for nom in police.getGlyphOrder():
        plume = TTGlyphPen(jeu)
        jeu[nom].draw(Cu2QuPen(plume, max_err=1.0, reverse_direction=True))
        glyphes[nom] = plume.glyph()

    police["loca"] = newTable("loca")
    glyf = police["glyf"] = newTable("glyf")
    glyf.glyphOrder = police.getGlyphOrder()
    glyf.glyphs = glyphes
    # Les boites englobantes ne sont pas deduites du trace : sans ce calcul, ni le
    # `maxp` ni l'en-tete de la fonte ne peuvent etre etablis.
    for glyphe in glyphes.values():
        glyphe.recalcBounds(glyf)
    del police["CFF "]
    police["head"].glyphDataFormat = 0
    police["head"].indexToLocFormat = 0
    # La table `maxp` d'une OpenType est en version 0.5 : elle ne decrit pas de
    # contours. Le TrueType attend la version 1.0 et ses statistiques de trace,
    # que fontTools recalcule a partir du `glyf` qui vient d'etre construit.
    police["maxp"].tableVersion = 0x00010000
    # Les limites liees au hinting : la fonte convertie n'embarque aucune
    # instruction, elles valent donc zero.
    for champ, valeur in (('maxZones', 2), ('maxTwilightPoints', 0), ('maxStorage', 0),
                          ('maxFunctionDefs', 0), ('maxInstructionDefs', 0),
                          ('maxStackElements', 0), ('maxSizeOfInstructions', 0)):
        setattr(police["maxp"], champ, valeur)
    police["maxp"].numGlyphs = len(glyphes)
    # La table `post` d'une OpenType est en version 3.0 (pas de noms de glyphes).
    # La version 2.0 exigée par le TrueType attend, elle, les trois champs ci-dessous.
    post = police["post"]
    post.formatType = 2.0
    post.glyphOrder = police.getGlyphOrder()
    post.extraNames = []
    post.mapping = {}
    police.sfntVersion = "\000\001\000\000"
    police["maxp"].recalc(police)
    police.save(str(cible))


def preparer(verifier_seulement: bool = False) -> dict:
    """{nom de police -> fichier TrueType}. Télécharge et convertit si nécessaire."""
    polices = {}
    for nom, fichier_otf in FACES.items():
        ttf = DOSSIER / (Path(fichier_otf).stem + ".ttf")
        if not ttf.exists():
            if verifier_seulement:
                raise FileNotFoundError(f"Police absente du cache : {ttf}")
            _en_truetype(_telecharger(fichier_otf), ttf)
        polices[nom] = ttf
    return polices


def controler(polices: dict) -> None:
    """Vérifie que chaque fonte se charge et couvre les caractères du français."""
    from fontTools.ttLib import TTFont
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont as TTFontRL

    for nom, chemin in polices.items():
        pdfmetrics.registerFont(TTFontRL(nom, str(chemin)))
        table = TTFont(str(chemin)).getBestCmap()
        manquants = [c for c in CARACTERES_TESTES if ord(c) not in table]
        etat = "OK" if not manquants else f"caractères absents : {''.join(manquants)}"
        print(f"  {nom:22s} {chemin.name:26s} {chemin.stat().st_size / 1024:5.0f} Ko  {etat}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prépare la police LaTeX (Latin Modern) en TrueType")
    parser.add_argument("--verifier", action="store_true")
    args = parser.parse_args()
    try:
        polices = preparer(verifier_seulement=args.verifier)
    except Exception as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1
    controler(polices)
    return 0


if __name__ == "__main__":
    sys.exit(main())
