"""
e08_ocd/fields/_entity_matching.py
======================================
Socle de nettoyage/classification/matching PARTAGÉ par NomDonneurOrdre et
Bénéficiaire (E08_OCD) — porté depuis DataCleaning-PipelineField-BCMDG/
nomdonneurordre/normalize_nomdonneurordre.py. Dans l'ancien repo, beneficiaire/
normalize_beneficiaire.py IMPORTAIT ces fonctions directement depuis
nomdonneurordre/ pour garantir que les deux champs restent alignés ; ici,
factorisé dans ce module privé au package e08_ocd/fields/ (pas de dépendance
croisée entre PACKAGES d'API — decision H — mais un module partagé DANS le même
package reste cohérent, les deux champs doivent utiliser EXACTEMENT la même
logique de nettoyage/classification).

Couvre :
  - Nettoyage (clean_label, hyper_normaliser, nettoyer_nifnni, nif_valide)
  - Classification locale (particulier / ETS personnel / ETS outlier / bruit évident)
  - Base fiscale DGI (chargement, index, matching flou rapidfuzz)
  - Entreprises publiques (chargement, index, matching)

Chemins des fichiers de données externes (DGI_BASE_PATH, PUBLIC_ENT_PATH) TOUJOURS
lus depuis .env — jamais en dur dans le code (voir .env.example).
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from e08_ocd.fields._keywords import KEYWORDS_ENTREPRISE, OUTLIERS_EVIDENTS, PRENOMS_KEYWORDS

# ══════════════════════════════════════════════════════════════════════════════
# NETTOYAGE
# ══════════════════════════════════════════════════════════════════════════════

_ARTEFACTS        = re.compile(r"_x000d_|\\r|\\n|\r\n|\r|\n", re.IGNORECASE)
# "MED O/ AHMED" -> "MED OULD AHMED" ; "FATIMA Mt/ HMD" -> "FATIMA MINT HMD" ; "Bt/" -> "BINT "
_ABREV_OULD       = re.compile(r"\bO\s*[/.]\s*", re.IGNORECASE)
_ABREV_MINT       = re.compile(r"\bMT\s*[/.]\s*", re.IGNORECASE)
_ABREV_BINT       = re.compile(r"\bBT\s*[/.]\s*", re.IGNORECASE)
# "H.M" -> "HM" (segments à 1 lettre) ; "BEDER.SALAM" -> "BEDER SALAM" (segments multi-lettres)
_DOT_SEQ          = re.compile(r"\b([A-Z]+(?:\.[A-Z]+)+)\b")
_LETTRES_UNIQUES  = re.compile(r"\b([A-Z](?:\s+[A-Z]){1,})\b")
_SPECIAUX         = re.compile(r"[*#@^~`|\\,;:.()\[\]{}\"'/]")
_PONCT_MULTI      = re.compile(r"[-]{2,}")
_MULTI_SPACE      = re.compile(r"\s+")
_DEVISES          = re.compile(r"\b(USD|EUR|MRU|MRO|DOLLARS?|EUROS?)\b", re.IGNORECASE)
_NON_ALNUM        = re.compile(r"[^A-Z0-9]")
_NON_CHIFFRES     = re.compile(r"\D")


def _traiter_points(match: re.Match) -> str:
    segments = match.group(0).split(".")
    if all(len(s) == 1 for s in segments):
        return "".join(segments)
    return " ".join(segments)


def _joindre_lettres_uniques(match: re.Match) -> str:
    return match.group(0).replace(" ", "")


def clean_label(raw) -> str:
    """
    Nettoyage basique commun à NomDonneurOrdre/Bénéficiaire :
      • Majuscules, sans accents, sans artefacts d'export (_x000D_...)
      • Expansion 'O/', 'Mt/', 'Bt/' -> OULD / MINT / BINT
      • Jointure des acronymes 'H.M' -> 'HM' ; 'Beder.Salam' -> 'BEDER SALAM'
      • Suppression devises, ponctuation/caractères spéciaux, espaces superflus
    """
    if pd.isna(raw):
        return ""
    v = str(raw).strip()
    if not v or v.lower() in ("nan", "none", "null"):
        return ""
    v = _ARTEFACTS.sub(" ", v)
    v = v.upper()
    v = unicodedata.normalize("NFKD", v)
    v = "".join(c for c in v if not unicodedata.combining(c))
    v = _ABREV_OULD.sub("OULD ", v)
    v = _ABREV_MINT.sub("MINT ", v)
    v = _ABREV_BINT.sub("BINT ", v)
    v = _DOT_SEQ.sub(_traiter_points, v)
    v = _LETTRES_UNIQUES.sub(_joindre_lettres_uniques, v)
    v = _DEVISES.sub(" ", v)
    v = _SPECIAUX.sub(" ", v)
    v = _PONCT_MULTI.sub("-", v)
    v = _MULTI_SPACE.sub(" ", v)
    return v.strip(" -")


def hyper_normaliser(s) -> str:
    """Normalisation agressive (alphanumérique uniquement) pour un fast-path exact-match."""
    cleaned = clean_label(s)
    return _NON_ALNUM.sub("", cleaned)


def nettoyer_nifnni(valeur) -> str:
    """Garde uniquement les chiffres (espaces, tirets, lettres retirés)."""
    if pd.isna(valeur):
        return ""
    return _NON_CHIFFRES.sub("", str(valeur))


def nif_valide(code_clean: str) -> bool:
    """8 chiffres exacts, pas tous identiques (111111111, 00000000... = invalide)."""
    return len(code_clean) == 8 and len(set(code_clean)) > 1


# ══════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION LOCALE (particulier / ETS / outlier évident)
# ══════════════════════════════════════════════════════════════════════════════

_KEYWORD_ENTREPRISE_RE = re.compile(
    r"\b(?:" + "|".join(sorted(KEYWORDS_ENTREPRISE, key=len, reverse=True)) + r")\b"
)
_ETS_PREFIX_RE = re.compile(r"^(ETS|ETB|ETABLISSEMENTS?)\b\s*")
# "X ET FRERES" / "X ET FILS" (sans préfixe ETS) : convention de nom commercial
# familial — c'est une raison sociale (souvent une vraie entrée DGI), pas la
# signature d'un particulier isolé.
_FAMILY_FIRM_RE = re.compile(r"\bET\s+(FRERES|FILS)\b")


def est_outlier_evident(label_clean: str) -> bool:
    """Bruit évident : vide, valeur blacklistée, tout-chiffres, tout-ponctuation."""
    if not label_clean or len(label_clean) <= 1:
        return True
    if label_clean in OUTLIERS_EVIDENTS:
        return True
    if label_clean.isdigit():
        return True
    if re.fullmatch(r"[\W_]+", label_clean):
        return True
    return False


def contient_keyword_entreprise(label_clean: str) -> bool:
    """True si un mot-clé entreprise (SARL, SOCIETE, ETS...) est présent (word-boundary)."""
    return bool(_KEYWORD_ENTREPRISE_RE.search(label_clean))


def commence_par_ets(label_clean: str) -> bool:
    return bool(re.match(r"^(ETS|ETB|ETABLISSEMENTS?)\b", label_clean))


def contient_prenom(label_clean: str) -> bool:
    return any(t in PRENOMS_KEYWORDS for t in label_clean.split())


def est_particulier(label_clean: str) -> bool:
    """Particulier = aucun mot-clé entreprise + au moins 2 mots + au moins 1 prénom reconnu."""
    if contient_keyword_entreprise(label_clean):
        return False
    if _FAMILY_FIRM_RE.search(label_clean):
        return False
    tokens = label_clean.split()
    if len(tokens) < 2:
        return False
    return any(t in PRENOMS_KEYWORDS for t in tokens)


def classify_local(label_clean: str) -> tuple[str | None, str | None]:
    """
    Applique les 3 scénarios métier :
      1. Nom propre d'un particulier          -> (label_clean, "PARTICULIER")
      2. 'ETS/Etablissement + nom propre'     -> (label_clean, "ETS_PERSONNEL")
         'ETS + ...' mais reste pas clair     -> ("OUTLIER", "ETS_OUTLIER")
      3. Ni l'un ni l'autre                   -> (None, None)  — à résoudre en cascade
    """
    if est_particulier(label_clean):
        return label_clean, "PARTICULIER"
    if commence_par_ets(label_clean):
        reste = _ETS_PREFIX_RE.sub("", label_clean).strip()
        if reste and contient_prenom(reste) and not contient_keyword_entreprise(reste):
            return label_clean, "ETS_PERSONNEL"
        return "OUTLIER", "ETS_OUTLIER"
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
# BASE FISCALE DGI
# ══════════════════════════════════════════════════════════════════════════════

def load_dgi_base(path: str | Path | None = None) -> pd.DataFrame:
    """Charge la base fiscale DGI. Chemin par défaut : variable d'env DGI_BASE_PATH
    (jamais en dur dans le code — voir .env.example)."""
    path = path or os.getenv("DGI_BASE_PATH")
    if not path:
        raise ValueError(
            "Chemin de la base DGI introuvable : définis DGI_BASE_PATH dans .env "
            "ou passe explicitement `path` à load_dgi_base()."
        )
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Base fiscale DGI introuvable : {p}")
    if p.is_dir():
        raise ValueError(f"DGI_BASE_PATH pointe vers un dossier, pas un fichier : {p}")
    try:
        df = pd.read_excel(p, dtype=str)
    except Exception as exc:
        taille = p.stat().st_size
        raise ValueError(
            f"Impossible de lire {p} comme fichier Excel ({exc}). Taille du fichier : "
            f"{taille:,} octets. Vérifie que c'est un .xlsx valide (ouvrable dans Excel), "
            f"qu'il n'est pas vide/corrompu ni tronqué, et — si le dossier est synchronisé "
            f"OneDrive/SharePoint — qu'il n'est pas en mode 'à la demande' (clic droit sur "
            f"le fichier -> Toujours conserver sur cet appareil)."
        ) from exc
    df["NIF"] = df["NIF"].map(nettoyer_nifnni).str.zfill(8)
    return df


def prepare_dgi_index(
    df_dgi:     pd.DataFrame,
    col_raison: str = "RAISON_SOCIALE",
    col_nif:    str = "NIF",
    col_forme:  str = "FORME_JURIDIQUE",
) -> dict:
    """
    Index précalculé de la base DGI (une seule passe par run) :
      nif_index     : {NIF (8 chiffres) -> RAISON_SOCIALE d'origine}
      dgi_clean     : liste des raisons sociales nettoyées (dédupliquées, ordre stable)
      clean_to_orig : {raison_sociale nettoyée -> raison_sociale d'origine}
      clean_to_meta : {raison_sociale nettoyée -> {"nif", "forme_juridique"}}
      dgi_exact     : {raison_sociale hyper-normalisée -> raison_sociale d'origine}

    Pas de filtre sur SITUATION (ACTIF/EN CESSATION) — comportement confirmé
    identique à l'ancien repo (une entreprise en cessation reste matchable).
    """
    nif_index:     dict[str, str] = {}
    clean_to_orig: dict[str, str] = {}
    clean_to_meta: dict[str, dict] = {}
    dgi_exact:     dict[str, str] = {}

    for row in df_dgi.itertuples(index=False):
        raison = getattr(row, col_raison, None)
        if raison is None or pd.isna(raison) or not str(raison).strip():
            continue
        orig  = str(raison).strip()
        nif   = str(getattr(row, col_nif, "") or "")
        forme = getattr(row, col_forme, "") or ""

        if nif_valide(nif) and nif not in nif_index:
            nif_index[nif] = orig

        cl = clean_label(orig)
        if cl and cl not in clean_to_orig:
            clean_to_orig[cl] = orig
            clean_to_meta[cl] = {"nif": nif, "forme_juridique": str(forme).strip()}

        hn = hyper_normaliser(orig)
        if hn and hn not in dgi_exact:
            dgi_exact[hn] = orig

    return {
        "nif_index":     nif_index,
        "dgi_clean":     list(clean_to_orig.keys()),
        "clean_to_orig": clean_to_orig,
        "clean_to_meta": clean_to_meta,
        "dgi_exact":     dgi_exact,
    }


def _dgi_fuzzy_batch(
    labels:     list[str],
    dgi_index:  dict,
    batch_size: int = 300,
    top_k:      int = 3,
) -> dict[str, list[tuple[str, float]]]:
    """
    Top-k candidats DGI par libellé via rapidfuzz.process.cdist, par lots pour
    borner la mémoire (batch_size labels x len(dgi_clean) à la fois).
    """
    dgi_clean = dgi_index["dgi_clean"]
    resultats: dict[str, list[tuple[str, float]]] = {}
    if not dgi_clean or not labels:
        return resultats

    for i in range(0, len(labels), batch_size):
        batch  = labels[i:i + batch_size]
        scores = process.cdist(batch, dgi_clean, scorer=fuzz.WRatio, workers=-1)
        k = min(top_k, scores.shape[1])
        for j, lab in enumerate(batch):
            row   = scores[j]
            part  = np.argpartition(-row, k - 1)[:k]
            order = part[np.argsort(-row[part])]
            resultats[lab] = [(dgi_clean[idx], float(row[idx])) for idx in order]
    return resultats


# ══════════════════════════════════════════════════════════════════════════════
# ENTREPRISES PUBLIQUES — short name à privilégier comme valeur normalisée quand
# une entreprise publique mauritanienne est reconnue, quelle que soit la
# variation du libellé brut ou de la raison sociale DGI trouvée.
# ══════════════════════════════════════════════════════════════════════════════

def load_public_entities(path: str | Path | None = None) -> pd.DataFrame:
    """Charge la liste des entreprises publiques (Short Name / Raison sociale).
    Chemin par défaut : PUBLIC_ENT_PATH (.env). Vide/absente -> DataFrame vide
    (dégradé propre, pas d'erreur — cette liste est un raffinement, pas un
    prérequis strict, contrairement à la base DGI)."""
    path = path or os.getenv("PUBLIC_ENT_PATH")
    if not path:
        return pd.DataFrame(columns=["Short Name", "Raison social - Public Ent"])
    p = Path(path)
    if not p.exists() or p.is_dir():
        return pd.DataFrame(columns=["Short Name", "Raison social - Public Ent"])
    try:
        return pd.read_excel(p, dtype=str)
    except Exception as exc:
        taille = p.stat().st_size
        raise ValueError(
            f"Impossible de lire {p} comme fichier Excel ({exc}). Taille du fichier : "
            f"{taille:,} octets. Vérifie que c'est un .xlsx valide (ouvrable dans Excel), "
            f"qu'il n'est pas vide/corrompu ni tronqué, et — si le dossier est synchronisé "
            f"OneDrive/SharePoint — qu'il n'est pas en mode 'à la demande' (clic droit sur "
            f"le fichier -> Toujours conserver sur cet appareil)."
        ) from exc


# Short names connus pour entrer en collision avec un usage générique/international
# et donc exclus du matching par token isolé, même si >= 3 caractères :
#   SAM — collision confirmée en test avec le suffixe "S.A.M." (Société Anonyme
#         Monégasque), très courant dans des raisons sociales étrangères sans
#         aucun rapport avec la Société des Aéroports de Mauritanie.
_SHORT_TOKEN_DENYLIST = {"SAM"}

# Longueur minimale avant de tenter le matching flou (tier 3) : en-dessous,
# rapidfuzz.WRatio produit des scores élevés par accident sur du bruit court.
_MIN_LEN_FUZZY_PUBLIC_ENT = 8


def prepare_public_ent_index(
    df_public:  pd.DataFrame,
    col_short:  str = "Short Name",
    col_raison: str = "Raison social - Public Ent",
) -> dict:
    """
    Index précalculé de la liste des entreprises publiques :
      short_tokens  : {short_name nettoyé -> short_name nettoyé}, limité aux
                       short names d'au moins 3 caractères hors denylist.
      full_to_short : {raison_sociale nettoyée -> short_name nettoyé}
      full_clean    : liste des raisons sociales nettoyées (pour rapidfuzz)
    """
    short_tokens:  dict[str, str] = {}
    full_to_short: dict[str, str] = {}

    for short, raison in zip(df_public.get(col_short, []), df_public.get(col_raison, [])):
        if short is None or pd.isna(short) or not str(short).strip():
            continue
        short_clean = clean_label(short)
        if not short_clean:
            continue
        if len(short_clean) >= 3 and short_clean not in _SHORT_TOKEN_DENYLIST:
            short_tokens[short_clean] = short_clean

        if raison is not None and not pd.isna(raison) and str(raison).strip():
            full_clean = clean_label(raison)
            if full_clean and full_clean not in full_to_short:
                full_to_short[full_clean] = short_clean

    return {
        "short_tokens":  short_tokens,
        "full_to_short": full_to_short,
        "full_clean":    list(full_to_short.keys()),
    }


def match_public_entity(label_clean: str, public_index: dict, threshold: float = 93.0) -> str | None:
    """
    Retourne le short name canonique si `label_clean` désigne une entreprise
    publique connue :
      1. libellé (nettoyé) = une raison sociale publique connue
      2. un token du libellé = un short name connu (>= 3 caractères, hors denylist)
      3. match rapidfuzz fort (libellé d'au moins _MIN_LEN_FUZZY_PUBLIC_ENT
         caractères) contre les raisons sociales publiques
    Retourne None si rien de suffisamment sûr.
    """
    if not label_clean or not public_index["full_to_short"]:
        return None

    if label_clean in public_index["full_to_short"]:
        return public_index["full_to_short"][label_clean]

    for tok in label_clean.split():
        if tok in public_index["short_tokens"]:
            return public_index["short_tokens"][tok]

    if len(label_clean) >= _MIN_LEN_FUZZY_PUBLIC_ENT:
        match = process.extractOne(label_clean, public_index["full_clean"], scorer=fuzz.WRatio)
        if match and match[1] >= threshold:
            return public_index["full_to_short"][match[0]]

    return None
