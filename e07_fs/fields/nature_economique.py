"""
e07_fs/fields/nature_economique.py
====================================
Normalisation du champ NatureEconomique pour E07_FS (Flux Sortants) — portée
depuis l'ancien repo field-based (nature_economique/normalize_nature_economique.py,
flux FS) : mêmes données métier (référentiel catégorie -> labels, mapping direct,
modalités vagues, pré-filtre des outliers évidents, cache warm-start déjà « chaud »).

Seul changement de moteur (décision confirmée) : l'embedding local mpnet + Ollama
de l'ancien repo est remplacé par l'API Claude sur la liste FERMÉE des labels du
référentiel (`shared/claude_client.py::call_claude_match_batch`), comme Produits
d'E08 — Claude choisit un label de la liste ou répond OUTLIER, jamais un libellé
inventé. Pas d'infra locale à héberger.

COLONNES AJOUTÉES ({col} = colonne source, ex. NatureEconomique) :
  {col}_clean                 — valeur nettoyée (`nettoyer`, logique de l'ancien repo)
  NatureEconomique_Normalisé  — label du référentiel / 'NA' / 'OUTLIER'
  {col}_Categorie             — catégorie du label (lookup, vide si NA/OUTLIER)
  {col}_method                — 'WARM' / 'MAP' / 'NOISE' / 'CLAUDE' / 'NA' / 'OUTLIER'
  {col}_check                 — True si OUTLIER

Cascade (sur valeurs uniques) :
  A. Vide / NaN                                    -> non résolu (OUTLIER via la règle NA)
  B. 'NA'                                          -> OUTLIER, corrigé par la règle NA
  C. Cache warm-start (brut, strip, rstrip, MAJ)   -> WARM
  D. En-tête / outlier évident (adresse, date, nom
     propre, nombre) / modalité vague              -> OUTLIER (NOISE)
  E. Label exact du référentiel, mapping direct    -> MAP (ou NOISE si mapping -> outlier)
  F. Claude sur la liste fermée des labels         -> CLAUDE (mis en cache), sinon OUTLIER
     Un échec TECHNIQUE Claude n'est jamais mis en cache (réessayé au run suivant).

RÈGLE NA — témoin ReferenceTransaction (shared/na_rule.py), inchangée :
  NatureEconomique == 'NA' ET ReferenceTransaction == 'NA'  -> 'NA'
  NatureEconomique == 'NA' ET ReferenceTransaction != 'NA'  -> OUTLIER
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from shared.claude_client import call_claude_match_batch
from shared.field_processor import CategoricalFieldProcessor
from shared.na_rule import apply_na_rule_frame

_REFERENTIEL_DIR = Path(__file__).parent.parent / "referentiel"


# ══════════════════════════════════════════════════════════════════════════════
# DONNÉES MÉTIER — reprises telles quelles de l'ancien repo (flux FS)
# ══════════════════════════════════════════════════════════════════════════════

_MAPPING_DIRECT_FS: dict[str, Optional[str]] = {
    "RIZ": "PRODUITS ALIMENTAIRES", "LAIT": "PRODUITS ALIMENTAIRES",
    "PRODUIT LITERIAIRE": "PRODUITS ALIMENTAIRES", "MILK": "PRODUITS ALIMENTAIRES",
    "VIANDE": "PRODUITS ALIMENTAIRES", "HUILE": "PRODUITS ALIMENTAIRES",
    "MARGARINE": "PRODUITS ALIMENTAIRES", "FUEL": "PRODUITS PETROLIERS GAZ",
    "TISSU": "PRODUITS TEXTILES", "TISSUS": "PRODUITS TEXTILES",
    "TUSSUS": "PRODUITS TEXTILES", "VETEMENT": "PRODUITS TEXTILES",
    "VETEMNTS": "PRODUITS TEXTILES", "COTTON": "PRODUITS TEXTILES",
    "VETEMENTS": "PRODUITS TEXTILES",
    "ENGINS": "EQUIPEMENTS MACHINES", "VEHICLE": "AUTOMOBILES VEHICULES",
    "TRUCK": "AUTOMOBILES VEHICULES", "MOTO": "AUTOMOBILES VEHICULES",
    "EQUIPENTS": "EQUIPEMENTS MACHINES", "HONORAIRES": "SERVICE CONSEIL GESTION",
    "PRODUIT CERAMIQUE": "MATERIAUX CONSTRUCTION", "CERAMIC": "MATERIAUX CONSTRUCTION",
    "TRANSPORT": "FRAIS ANNEXES TRANSPORT",
    "TRANSPORTS AERIENS PASSAGERS": "BILLETS AVION",
    "IMPORTATIONS DES BIENS": "AUTRES BIENS", "IMPORTATION DE BIENS": "AUTRES BIENS",
    "FRAIS DE SCOLARITE": "EDUCATION FORMATION",
    "PRET AUTRES SECTEURS": "PRET AUTRES SECTEURS LMT",
    "COTISATIONS": "PENSIONS RETRAITES COTISATIONS",
    "EXPORTATION ET IMPORTATION DE BIENS": "AUTRES BIENS",
    "IMPPORTATION EXPORTATION DE BIEN": "AUTRES BIENS",
    "IMP DES BIENS": "AUTRES BIENS", "IMPORTATION DESZ BIENS": "AUTRES BIENS",
    "EXPORTATION ET IMPORTAION DE BIENS": "AUTRES BIENS",
    "FRET": "FRAIS ET TAXES", "FRAIS DE VOYAGE ET DE SEJOUR": "TOURISME SEJOUR",
    "SERVICE INFORMATION": "SERVICE TELECOM INFORMATIQUE",
    "AUTRES INTERMEDIAIRES COMCE DE": "AUTRES SERVICES",
    "AUTRES INTERMEDIAIRES COMCE PR": "AUTRES SERVICES",
    "SERVICES DHL ET CENTRE APPEL ET AUTRES MESSAGERIES": "SERVICES MESSAGERIES EXPRESS",
    "ACTIVITES ORGANIS POLITIQUES":  "ADMINISTRATIONS PUBLIQUES",
    "ACTIVS ORDRE PUBLIC SECURITE":  "SERVICES SECURITE",
    "ACTIVS ORGANIS PATRONALES CONS": "SERVICE CONSEIL GESTION",
    "ACTIVS ORGANIS PROFESSIONNELLE": "SERVICE CONSEIL GESTION",
    "ADMINIST MARCHES FINANCIERS":   "SERVICES BANCAIRES FINANCIERS",
    "SERVICES DHL ET POSTE ET AUTRES MESSAGERIES":        "SERVICES MESSAGERIES EXPRESS",
    "PECHE": "PECHES PRODUITS MARITIMES", "POISSONE": "PECHES PRODUITS MARITIMES",
    "EXPORTATION POISSON": "PECHES PRODUITS MARITIMES",
    "ACTIVITES JURIDIQUES": "ACTES JUDICIAIRES", "JUSTICE": "ACTES JUDICIAIRES",
    "ACTIVITES COMPTABLES": "SERVICES COMPTABLES",
    "VOYAGE TOURISTIQUE": "TOURISME SEJOUR",
    "HEBERGEMENT PELERINAGE OU OMRA": "TOURISME SEJOUR",
    "AFFRETEMENT PELERINAGE": "TOURISME SEJOUR",
    "FRAIS SCOLARITE": "EDUCATION FORMATION", "FRAIS SCOLAIRE": "EDUCATION FORMATION",
    "REGL LOYER": "LOYER", "REGLEMENT LOYERS": "LOYER", "LOYERS": "LOYER",
    "SALAIRE": "SALAIRES", "SALAIRES EMPLOYES": "SALAIRES",
    "SALAIRES ET APPOINTEMENTS": "SALAIRES", "ECONOMIE SUR SALAIRE": "SALAIRES",
    "ECONOMIE SUR REVENUS DES MAURITANIENS": "ECONOMIE REVENUS MAURITANIENS",
    "ECONOMIE SUR LES REVENUS DES ETRANGERS": "ECONOMIE REVENUS ETRANGERS",
    "SALAIRES EMPLOYES LIBYENNES": "ECONOMIE REVENUS ETRANGERS",
    "SALAIRES DES LIBYENNES": "ECONOMIE REVENUS ETRANGERS",
    "AIDE FAMILIALE": "AIDES FAMILIALES", "AIDE FAMILIALS": "AIDES FAMILIALES",
    "ALLOCATION": "AIDES FAMILIALES",
    "PENSION": "PENSIONS RETRAITES COTISATIONS",
    "PENSIONS ET RENTES": "PENSIONS RETRAITES COTISATIONS",
    "NIVELLEMENT DE FONDS": "AVANCE RETOUR DE FOND",
    "APPROVISIONNEMENT COMPTE": "AVANCE RETOUR DE FOND",
    "AMBASSADE": "AMBASSADES CONSULATS", "AMBASSADES": "AMBASSADES CONSULATS",
    "DONS POUR LES INVESTISSEMENTS": "DONS POUR INVESTISSEMENTS",
    "COSULTING": "SERVICE CONSEIL GESTION",
    "SERVICE INFO": "SERVICE TELECOM INFORMATIQUE",
    "TELECOMMUNICATION": "SERVICE TELECOM INFORMATIQUE",
    "SERVICES INFORMATIQUES": "SERVICE TELECOM INFORMATIQUE",
    "FEES": "FRAIS ET TAXES", "FRAIS AVOCAT": "FRAIS AVOCATS",
    "FRAIS AVOCATS": "FRAIS AVOCATS",
    "AUTRE": "AUTRES", "AUTES": "AUTRES", "AUTREQS": "AUTRES",
    "AUTREES": "AUTRES", "AITRES": "AUTRES", "AUTERS": "AUTRES",
    "REF AUTERS": "AUTRES", "AUTRS": "AUTRES",
    # Outliers directs
    "GB": None, "ESPAGNE": None, "USA": None, "MALI": None,
    "ANGOLA": None, "FRANCE": None, "CANADA": None, "INFORMELS": None,
    "B": None, "N": None, "FAUX PARTICULIERS": None,
    "PASSPORT ID": None, "STUDENT ID": None,
}

# Modalités vagues → OUTLIER directement (pas de catégorie NON CLASSE)
_NON_CLASSE_DIRECT_FS: set[str] = {
    "TRANSFERTS", "IMMOBILIER", "INVESTISSEMENTS",
    "AFFAIRES ETRANGERES", "AFFAIRES NON CLASSEES",
    "VOYAGE D?AFFAIRES", "COMMISSIONS COURTAGES",
    "PARTICULIERS ET PROFETIONNEL", "VOYAGE D AFFAIRES",
}

_MOTS_ADRESSE = {
    "ROAD","STREET","AVENUE","BOULEVARD","RUE","FLOOR","BUILDING",
    "BRANCH","DISTRICT","PROVINCE","CITY","CEDEX","BP","ROUTE",
    "NORTH","SOUTH","EAST","WEST","BOX","PLOT","UNIT","SUITE",
    "LEVEL","TOWER","INDUSTRIAL","COMPLEX","PLAZA","CENTER","CENTRE",
    "MUMBAI","DUBAI","LONDON","BEIJING","SHANGHAI","CAIRO","JEDDAH",
    "ISTANBUL","GUANGZHOU","HANGZHOU","YIWU","ZHEJIANG","DAKAR",
    "CASABLANCA","AGADIR","ALGER",
}

_PRENOMS_MAURITANIENS = {
    "AHMAD","AHMED","MOHAMMAD","MUHAMMAD","MOHAMED","MOUHAMED",
    "ABDALLAH","ABDALLAHI","ABDELLAHI","ABD","ABDERRAHMANE","ABDERAHMANE",
    "SIDI","BRAHIM","IBRAHIM","CHEIKH","SHEIKH",
    "OMAR","OUMAR","ALI","HASSAN","HUSSEIN","YAHYA","YAHIA",
    "MAMADOU","MOUSSA","ISSA","YOUSSEF","OUSMANE","YOUSSOUF","IDRISS",
    "SIDINA","TALEB","LELLAH","MALAININE","SALIHY","LEHBIB","HORMA",
    "MAHAND","IDOUMOU","BEYADE","KHALIFA","ELEMINE","YESAA","KHATRY",
    "HACHEM","MOCTAR","GHASSEM","YEHDHIH","EBDEMEL","BENNANE",
    "BOUCHRAYA","SALECK","THIAM","MOULAY","MAOULOUM","MAKE","MODY",
    "BAKARY","VELAH","DADDAH","EWFA","ASKER","GHOUEIZI","MELAININE",
    "HAMOUD","ZEINE","LEMINE","TOLBA","RYAN","BOUH","SOUMARE","CAMARA",
    "ZEINI","IMAM","MED","WELY","AMAL","EBNOU",
    "MARIEM","FATIMA","FATMA","LALLA","AMINATA","AISHA","KERTOUMA",
    "BABIYE","VATMA","LEJHOURY","MAADH","CHACH","SAVIYE","KHADIJETOU",
    "OUKADDOUR","NOUHAYLA",
    "EL","AL","OULD","MINT","BINT","BEN","BOU","SID","MME","MR",
}

_HEADERS_NATECO = {
    "NATUREECONOMIQUE","NATURE_ECONOMIQUE","NATURE ECONOMIQUE",
    "NON SPECIFIE","NON PRECISE","INCONNU","NS","ND","SNN",
    "STRING","NEANT","SANS OBJET","NULL","NONE","N/A","NAN","N A",
    "NON RENSEIGNE",
}

_SYSTEM_PROMPT_TEMPLATE = (
    "Tu es un expert BCM (Banque Centrale de Mauritanie) spécialisé dans la classification "
    "des flux de transactions bancaires internationales selon la balance des paiements "
    "(transferts émis — flux sortants).\n"
    "Voici la liste FERMÉE des labels de nature économique valides (référentiel officiel) :\n"
    "{liste}\n"
    "RÈGLES STRICTES :\n"
    "- Tu choisis TOUJOURS un label PARMI CETTE LISTE EXACTE — jamais un label inventé, "
    "reformulé ou hors liste.\n"
    "- Même si le libellé est abrégé, tronqué, en anglais ou mal orthographié, "
    "tu identifies le label sémantiquement le plus proche.\n"
    "- Si le libellé est ambigu ou trop vague pour appartenir clairement à un label "
    "économique précis (ex: 'PROJETS', 'VOYAGE D?AFFAIRES', 'ACTIVITES BANQUE CENTRALE', "
    "'PARTICULIERS ET PROFETIONNEL'), tu réponds OUTLIER.\n"
    "- Tu réponds UNIQUEMENT avec des lignes au format exact : N. LABEL\n"
    "- Une ligne par item, dans le même ordre. Zéro explication, zéro ligne vide."
)


@dataclass
class NatureEconomiqueReferentiel:
    version: str
    referentiel: dict = field(default_factory=dict)          # {Categorie: [label, ...]}
    label_vers_categorie: dict = field(default_factory=dict)
    all_labels: list = field(default_factory=list)          # liste fermée proposée à Claude
    labels_par_cle: dict = field(default_factory=dict)       # {nettoyer(label): label}
    mapping_direct: dict = field(default_factory=dict)       # {clean: label | None}
    non_classe: set = field(default_factory=set)


def load_nature_economique_referentiel(path) -> NatureEconomiqueReferentiel:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    referentiel = {cat: [lbl.strip() for lbl in labels]
                   for cat, labels in data.get("referentiel", {}).items()}
    label_vers_categorie = {lbl: cat for cat, labels in referentiel.items() for lbl in labels}
    # « OUTLIER » figure dans le référentiel comme catégorie technique : ce n'est
    # pas un label proposable (Claude répond OUTLIER de lui-même si rien ne convient).
    all_labels = [lbl for lbl in label_vers_categorie if lbl != "OUTLIER"]
    return NatureEconomiqueReferentiel(
        version=data.get("version", "unknown"),
        referentiel=referentiel,
        label_vers_categorie=label_vers_categorie,
        all_labels=all_labels,
        labels_par_cle={nettoyer(lbl): lbl for lbl in all_labels},
        mapping_direct=dict(_MAPPING_DIRECT_FS),
        non_classe=set(_NON_CLASSE_DIRECT_FS),
    )


# ══════════════════════════════════════════════════════════════════════════════
# NETTOYAGE ET PRÉ-FILTRE — logique identique à l'ancien repo
# ══════════════════════════════════════════════════════════════════════════════

def _normaliser(texte: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(texte))
    sans_acc = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9\s]", " ", sans_acc.upper()).strip()


def nettoyer(texte) -> str:
    """Normalise + supprime les préfixes numériques BCM + les tokens techniques."""
    s = str(texte).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    t = _normaliser(s)
    t = re.sub(r"^\d{1,5}[-\s]+", "", t.strip())
    mots = [m for m in t.split() if len(m) >= 2
            and not re.fullmatch(r"[\dA-Z]{1,3}\d+[\dA-Z]*", m)]
    return " ".join(mots)


def _est_nom_propre(clean: str) -> bool:
    mots = clean.split()
    if len(mots) < 2:
        return False
    connus = sum(1 for m in mots if m in _PRENOMS_MAURITANIENS)
    return connus >= max(1, len(mots) // 2)


def _est_date(clean: str) -> bool:
    return bool(re.search(
        r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
        r"|\d{4}[-/]\d{1,2}[-/]\d{1,2}"
        r"|[a-z]{3,4}[-/]\d{2,4}"
        r"|\d{1,2}[-/][a-z]{3,4})",
        clean, re.IGNORECASE,
    ))


def est_outlier(clean: str) -> bool:
    """Outlier évident, inutile de solliciter Claude : vide, nombre, adresse,
    date, nom de personne."""
    if not clean:
        return True
    if re.fullmatch(r"[\d\s]+", clean):
        return True
    if re.search(r"\d{4,}", clean):
        return True
    if set(clean.split()) & _MOTS_ADRESSE:
        return True
    if _est_date(clean):
        return True
    if _est_nom_propre(clean):
        return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# WARM-START
# ══════════════════════════════════════════════════════════════════════════════

def _warm_start_path(api_id: str) -> Path:
    return _REFERENTIEL_DIR / f"validated_classif_nature_economique_{api_id.lower()}.json"


def load_warm_start_nature_economique(api_id: str = "E07_FS") -> dict:
    path = _warm_start_path(api_id)
    if not path.exists():
        return {}
    classif = json.load(open(path, encoding="utf-8")).get("classif", {})
    return {k: str(v).strip() for k, v in classif.items()}


def save_warm_start_nature_economique(api_id: str, new_entries: dict, verbose: bool = False) -> None:
    """Fusionne de nouvelles modalités (Claude ou corrections manuelles) dans le cache."""
    if not new_entries:
        return
    path = _warm_start_path(api_id)
    data = {"version": "1.0.0", "classif": {}}
    if path.exists():
        data = json.load(open(path, encoding="utf-8"))
        data.setdefault("classif", {})
    data["classif"].update(new_entries)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    if verbose:
        print(f"  Warm-start NatureEconomique {api_id} : +{len(new_entries)} modalité(s) mise(s) en cache")


def _resolution_deterministe(clean: str, ref: NatureEconomiqueReferentiel) -> Optional[tuple]:
    """Étapes D et E de la cascade ; None si la valeur doit partir chez Claude."""
    if not clean or clean in _HEADERS_NATECO or est_outlier(clean) or clean in ref.non_classe:
        return "OUTLIER", "NOISE"
    if clean in ref.labels_par_cle:
        return ref.labels_par_cle[clean], "MAP"
    if clean in ref.mapping_direct:
        label = ref.mapping_direct[clean]
        return (label, "MAP") if label else ("OUTLIER", "NOISE")
    return None


def treating_nature_economique(
    df:         pd.DataFrame,
    nat_col:    str                          = "NatureEconomique",
    out_col:    str                          = "NatureEconomique_Normalisé",
    ref_col:    str                          = "ReferenceTransaction",
    ref:        NatureEconomiqueReferentiel  = None,
    api_id:     str                          = "E07_FS",
    cfg:        dict                         = None,
    warm_start: bool                         = True,
    verbose:    bool                         = False,
) -> pd.DataFrame:
    """Normalise la colonne NatureEconomique. Traitement sur valeurs uniques."""
    df = df.copy()
    if ref is None:
        ref = load_nature_economique_referentiel(_REFERENTIEL_DIR / "nature_economique_referentiel_E07.json")
    cfg = cfg or {}

    method_col = f"{nat_col}_method"
    ws_cache = load_warm_start_nature_economique(api_id) if warm_start else {}
    ws_upper = {k.strip().upper(): v for k, v in ws_cache.items()}

    uniques = df[nat_col].dropna().unique()
    clean_map = {v: nettoyer(v) for v in uniques}
    df[f"{nat_col}_clean"] = df[nat_col].map(clean_map).fillna("")

    resolu: dict = {}          # valeur brute -> (label, méthode)
    a_claude: dict = {}        # clean -> [valeurs brutes] en attente de Claude
    for v in uniques:
        s = str(v)
        cle_up = s.strip().upper()
        if not cle_up or cle_up in ("NAN", "NONE", "NULL"):
            resolu[v] = (None, None)
            continue
        if cle_up == "NA":
            resolu[v] = ("OUTLIER", "OUTLIER")   # corrigé par la règle NA si Ref aussi NA
            continue
        if warm_start:
            trouve = next((ws_cache[k] for k in (s, s.strip(), s.rstrip()) if k in ws_cache), None)
            if trouve is None:
                trouve = ws_upper.get(cle_up)
            if trouve is not None:
                resolu[v] = (trouve, "WARM")
                continue
        det = _resolution_deterministe(clean_map[v], ref)
        if det is not None:
            resolu[v] = det
            continue
        a_claude.setdefault(clean_map[v], []).append(v)

    cles = list(a_claude)
    batch_size = cfg.get("llm", {}).get("batch_size", 20)
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(liste="\n".join(f"- {l}" for l in ref.all_labels))
    reponses_claude: dict = {}
    echecs: set = set()
    for debut in range(0, len(cles), batch_size):
        lot = cles[debut:debut + batch_size]
        reponses = call_claude_match_batch(lot, ref.all_labels, system_prompt, cfg)
        if reponses is None:
            print(f"  [CLAUDE] échec technique sur le lot {debut}-{debut + len(lot)} "
                  f"-> OUTLIER temporaire (non mis en cache, réessayé au prochain run)")
            echecs.update(lot)
            continue
        reponses_claude.update(zip(lot, reponses))
        if verbose:
            print(f"  [CLAUDE] {min(debut + batch_size, len(cles))}/{len(cles)} modalités", end="\r")

    # Cache indexé sur la valeur BRUTE (même convention que le cache hérité de
    # l'ancien repo) : chaque variante brute d'une même valeur nettoyée est retenue.
    cacheable = {
        str(v).strip(): (reponses_claude.get(clean) or "OUTLIER")
        for clean, bruts in a_claude.items() if clean not in echecs
        for v in bruts
    }
    if cacheable:
        save_warm_start_nature_economique(api_id, cacheable, verbose=verbose)

    for clean, bruts in a_claude.items():
        label = reponses_claude.get(clean)
        for v in bruts:
            resolu[v] = (label, "CLAUDE") if label else ("OUTLIER", "OUTLIER")

    # Series.map(dict) : recherche au niveau C de pandas, sans appel Python par ligne.
    df[out_col] = df[nat_col].map({k: r[0] for k, r in resolu.items()})
    df[method_col] = df[nat_col].map({k: r[1] for k, r in resolu.items()})
    df["_ws_hit"] = df[method_col] == "WARM"

    if ref_col in df.columns:
        iso, mth = apply_na_rule_frame(df, nat_col, ref_col, out_col, method_col)
        df[out_col] = iso
        df[method_col] = mth

    categorie = df[out_col].map(ref.label_vers_categorie).fillna("")
    df[f"{nat_col}_Categorie"] = categorie.where(~df[out_col].isin(["OUTLIER", "NA"]), "")
    df[f"{nat_col}_check"] = df[out_col] == "OUTLIER"
    return df


def build_full_classification_nature_economique(ref: NatureEconomiqueReferentiel, api_id: str,
                                                col_in: str, col_out: str) -> pd.DataFrame:
    """Table de classification CUMULATIVE (référentiel + mapping + cache), indépendante
    du delta traité — même raisonnement que les autres champs catégoriels."""
    combined: dict = {lbl: lbl for lbl in ref.all_labels}
    combined.update({k: (v or "OUTLIER") for k, v in ref.mapping_direct.items()})
    combined.update({k: "OUTLIER" for k in ref.non_classe})
    combined.update(load_warm_start_nature_economique(api_id))
    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_nature_economique_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor NatureEconomique depuis le bloc YAML `fields[]`."""
    cols = field_cfg["columns"]
    ref = load_nature_economique_referentiel(Path(field_cfg["referentiel_path"]))
    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_nature_economique,
        treating_kwargs={
            "nat_col": cols["field"],
            "out_col": cols["field_out"],
            "ref_col": cols["ref_transaction"],
            "ref": ref,
            "api_id": None,   # rempli dynamiquement par CategoricalFieldProcessor.process()
            "cfg": field_cfg,
            "warm_start": True,
            "verbose": False,
        },
        col_in=cols["field"],
        col_out=cols["field_out"],
        ref_banque_col=cols.get("ref_banque", "RefBanque"),
        outlier_tag=field_cfg.get("outlier_tag", "OUTLIER"),
        exclude_suffixes=("_clean", "_method", "_check", "_Categorie"),
        clean_fn=nettoyer,
        save_warm_start_fn=save_warm_start_nature_economique,
        classification_fn=lambda api_id: build_full_classification_nature_economique(
            ref, api_id, cols["field"], cols["field_out"]
        ),
    )
