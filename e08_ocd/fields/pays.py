"""
e08_ocd/fields/pays.py
==========================
Normalisation du champ Pays pour E08_OCD — porté depuis
DataCleaning-PipelineField-BCMDG/pays/normalize_pays.py, avec UN changement
délibéré (décision confirmée) : le fallback LLM bascule d'Ollama/qwen2.5:14b
(modèle local, ~9 Go, serveur à héberger) vers l'API Claude — cohérent avec le
reste du repo (Devise/NomCorrespondant/Produits/NomDonneurOrdre/Bénéficiaire
n'utilisent que Claude, aucune infra locale).

Deuxième différence délibérée : les résolutions Claude sont maintenant mises en
cache (validated_classif_pays_e08_ocd.json) — l'ancien repo ne cachait JAMAIS les
résolutions Ollama (compute local "gratuit", recalculées à chaque run). Claude
étant facturé à l'appel, ne pas cacher reviendrait à repayer indéfiniment les
mêmes valeurs "check" chaque semaine — incohérent avec tous les autres champs
de ce repo, qui cachent tous leurs résolutions Claude.

Tout le reste (nettoyage, référentiel pycountry/babel/geonamescache + alias
manuels, matching flou, mots-clés d'adresse, règle NoAs/OUTLIER) est porté à
l'identique.

COLONNES AJOUTÉES :
  Pays_clean      — valeur nettoyée
  Pays_Normalisé  — code ISO-2 / 'NoAs' / 'OUTLIER' / 'NA' (Namibie)
  Pays_method     — 'WARM' / 'MAP' / 'FUZZY' / 'ADDR' / 'CLAUDE' / 'NoAs' / 'OUTLIER'
  Pays_check      — True si OUTLIER

RÈGLE NoAs / OUTLIER (SPÉCIFIQUE à Pays, ne réutilise PAS shared/na_rule.py —
le code ISO-2 de la Namibie est littéralement "NA", ambigu avec le témoin NA
générique des autres champs, d'où une règle dédiée) :
  Pays vide/null  ET  NumCredoc vide/null  →  NoAs   (bruit pur, les deux vides)
  Pays == 'NA'    ET  NumCredoc non vide   →  'NA'   (Namibie, valeur légitime)
  Pays == 'NA'    ET  NumCredoc vide       →  NoAs
  Valeur non-pays détectée par la cascade  →  OUTLIER (toujours, quel que soit NumCredoc)
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd
import pycountry
from babel import Locale
import geonamescache
from rapidfuzz import process as rfuzz

from shared.claude_client import call_claude_match_batch
from shared.field_processor import CategoricalFieldProcessor

_REFERENTIEL_DIR = Path(__file__).parent.parent / "referentiel"

# ══════════════════════════════════════════════════════════════════════════════
# REGEX PRÉ-COMPILÉES
# ══════════════════════════════════════════════════════════════════════════════

_RE_SYMBOLS     = re.compile(
    r"[.,/\\?;:!#@%&*()\[\]+=_~^`|<>'\"°’‘–—«»]+"
)
_RE_SPACES      = re.compile(r"\s{2,}")
_RE_DIGITS_ONLY = re.compile(r"^\d+$")
_RE_NON_PAYS    = re.compile(
    r"^(NONE|NULL|NA(?!MIBI)|N/A|STRING|IMMOBILIER|TOURISME|HOTELLIERE|"
    r"FAUX PARTICULIERS|INDUSTRIES TEXTILES|CONFECTION|AUTRES SERVICES|"
    r"MANUTENTION|COMMERCE DIVERS|COMMERCE ET L.INDUSTRIE|A NE PAS UTILISER|"
    r"INTERMEDIAIRES|COMCE GROS|FAUX|REG POLAIRES|ANTARCTIQUE|"
    r"REPUBLIC OF MORRIS|NOGAS)$",
    re.IGNORECASE,
)
_RE_DATE = re.compile(
    r"""\b(
        \d{4}[-/]\d{1,2}[-/]\d{1,2}
      | \d{1,2}[-/]\d{1,2}[-/]\d{2,4}
      | \d{1,2}[-/](jan|fev|mar|avr|mai|jun|jul|aou|sep|oct|nov|dec|
                    feb|apr|aug)[a-z]*[-/]?\d{0,4}
      | (jan|fev|mar|avr|mai|jun|jul|aou|sep|oct|nov|dec|
         feb|apr|aug)[a-z]*[-/\s]\d{2,4}
      | \d{1,2}-\d{2}
    )\b""",
    re.VERBOSE | re.IGNORECASE,
)

# Valeurs considérées comme "vides" dans la colonne témoin (NumCredoc)
_REF_EMPTY_VALUES = {"na", "nan", "none", "null", "", "string"}

FUZZY_CUTOFF = 93  # bon compromis précision/rappel


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

def _strip_acc(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return _strip_acc(s.strip().lower())


def _ref_is_empty(ref_raw: str) -> bool:
    return ref_raw.strip().lower() in _REF_EMPTY_VALUES


@lru_cache(maxsize=16_384)
def clean_pays(raw: str) -> str:
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = _RE_DATE.sub(" ", s)
    s = _RE_SYMBOLS.sub(" ", s)
    s = _RE_SPACES.sub(" ", s).strip()
    return _strip_acc(s.upper())


# ══════════════════════════════════════════════════════════════════════════════
# RÉFÉRENTIEL — pycountry/babel/geonamescache + alias manuels (identique à
# l'ancien repo, aucun changement métier)
# ══════════════════════════════════════════════════════════════════════════════

def _build_lookup() -> tuple[dict[str, str], set[str]]:
    raw: dict[str, str] = {}

    for c in pycountry.countries:
        a2 = c.alpha_2
        for attr in ("name", "alpha_2", "alpha_3", "official_name", "common_name"):
            v = getattr(c, attr, None)
            if v:
                raw[v] = a2

    fr_loc = Locale("fr")
    for c in pycountry.countries:
        fr_name = fr_loc.territories.get(c.alpha_2.upper())
        if fr_name:
            raw[fr_name] = c.alpha_2

    gc = geonamescache.GeonamesCache()
    for info in gc.get_countries().values():
        a2 = info.get("iso")
        for field in ("capital", "capital_fr"):
            cap = info.get(field)
            if cap and a2:
                raw[cap] = a2

    custom: dict[str, str] = {
        "etats-unis": "US", "etats-unis d'amerique": "US", "etats unis": "US",
        "etats unis d'amerique": "US", "usa": "US", "united states": "US",
        "ny": "US", "tx": "US", "fl": "US", "n y": "US",
        "new york": "US", "chicago": "US", "etats-unis amerique": "US",
        "420 montgomery street": "US",
        "royaume-uni": "GB", "grande bretagne": "GB", "england": "GB", "uk": "GB",
        "united kingdom": "GB", "leicester le87 2bb united": "GB",
        "farance": "FR", "ffr": "FR", "bretagne": "FR",
        "5 rue scribe": "FR", "cedex france": "FR",
        "allemangne": "DE", "german": "DE", "deutschland": "DE",
        "espana": "ES", "espange": "ES", "sp": "ES",
        "avenida diagonal 621 629": "ES", "las palmas": "ES",
        "las palmas espagne": "ES", "santa cruz de tenerife": "ES",
        "italia": "IT", "italya": "IT", "italla": "IT",
        "s.maria amonte (pi) branch": "IT", "via leonardo da vinci n8 5602": "IT",
        "pays-bas": "NL", "pays bas": "NL", "hollande": "NL", "holande": "NL",
        "holanda": "NL", "holland": "NL", "nederland": "NL",
        "the netherdands": "NL", "the netheriands": "NL", "the netherlnads": "NL",
        "netherland": "NL", "netherlande": "NL", "etherlands": "NL",
        "swiss": "CH", "switezerland": "CH", "switzerlands": "CH",
        "pully": "CH", "chene bourg": "CH",
        "belguim": "BE", "belgian": "BE", "andenne-belgique": "BE",
        "norway": "NO",
        "sweden": "SE", "sw": "SE",
        "denmark": "DK", "lyngby hovedgade 85 dk-2800": "DK",
        "finland": "FI",
        "ireland": "IE", "irland": "IE",
        "portugual": "PT", "portugare": "PT",
        "austria": "AT",
        "luxerbourg": "LU",
        "greece": "GR",
        "poland": "PL", "poulanda": "PL", "polande": "PL",
        "hungary": "HU", "hangary": "HU",
        "romania": "RO",
        "bulgaria": "BG",
        "slovenia": "SI", "slovenija": "SI",
        "slovakia": "SK",
        "republique tcheque": "CZ", "tchequie": "CZ", "tcheque republique": "CZ",
        "lithuania": "LT",
        "estonia": "EE",
        "latvia": "LV",
        "cyprus": "CY",
        "malta": "MT",
        "iceland": "IS",
        "ukraine": "UA",
        "russie": "RU", "su": "RU",
        "serbia": "RS", "serbie-et-montenegro": "RS",
        "bosnie herzegovine": "BA", "bosnie-herzegovine": "BA",
        "bosnia": "BA", "herzegovni": "BA",
        "macedoine": "MK", "macedoine ex-republique yougoslave": "MK",
        "albania": "AL",
        "moldova": "MD",
        "kosovo": "XK",
        "georgia": "GE",
        "armenia": "AM",
        "azerbaijan": "AZ",
        "turkey": "TR", "turkiye": "TR", "turkya": "TR",
        "turkeye": "TR", "turkye": "TR",
        "maroc": "MA", "marocco": "MA", "marroc": "MA",
        "casablanca": "MA", "mohammedia": "MA", "agadir": "MA",
        "laayoune": "MA", "maarif": "MA", "meknes": "MA", "bouskoura": "MA",
        "agence meknes ibn khaldoun": "MA",
        "aceur casablanca maroc": "MA", "nouaceur-casablanca": "MA",
        "20250 casablanca": "MA", "angle bdzerktouni rue franche": "MA",
        "algerie": "DZ", "argerie": "DZ", "algeria": "DZ",
        "alger": "DZ", "algier": "DZ",
        "11 bd colonel amirouche alger": "DZ",
        "tunisia": "TN", "tunis": "TN", "sousse": "TN",
        "avenue habib bourguiba": "TN", "25 avenue habib bourguiba": "TN",
        "rue hedi nouira": "TN",
        "egypt": "EG", "egybt": "EG", "eqypt": "EG", "caire": "EG",
        "86 cairo egypt": "EG", "86 cairo -alexandria egypt": "EG",
        "24 fawzy moaaz st semouha": "EG", "alexandria old port": "EG",
        "libya": "LY", "libyenne": "LY", "libyenne jamahiriya arabe": "LY",
        "libyan": "LY",
        "mauritania": "MR", "mauritania,nouakchott": "MR",
        "nouakchott mauritania": "MR",
        "nktt": "MR", "teyarett amouratt lot 359": "MR",
        "sahara occidental": "EH",
        "senegql": "SN", "senegale": "SN", "senegal residents": "SN",
        "dakar": "SN", "parcells assainies senegal": "SN",
        "burkina faso": "BF", "burkina": "BF",
        "cote d'ivoire": "CI", "cote divoire": "CI", "cote d ivoire": "CI",
        "abidjan": "CI",
        "cameroun": "CM", "cameron": "CM",
        "rd congo": "CD", "republique democratique du congo": "CD",
        "rep democratique du congo": "CD", "congo rep democrat": "CD",
        "mauritius": "MU", "iles maurices": "MU", "ile maurice": "MU",
        "republic of morris": "MU",
        "south africa": "ZA", "rep of south africa": "ZA",
        "100 grayston drive sandton jhb": "ZA",
        "kenya": "KE", "nairobi": "KE", "po box 30711 00100 nairobi": "KE",
        "namibia": "NA", "namibie": "NA",
        "benin": "BJ", "bénin": "BJ",
        "bostwana": "BW",
        "swaziland": "SZ",
        "arabie saoudite": "SA", "arabie saudi": "SA", "ksa": "SA",
        "kingdom of saudi arabia": "SA", "arabe saoudi": "SA",
        "32040 jeddah 21428 saudi": "SA",
        "emirats arabes unis": "AE", "uae": "AE", "uea": "AE",
        "dubai": "AE", "dubaie": "AE", "head office baniyas road": "AE",
        "jebel ali zone dubai": "AE", "v1a jumeriah lakes towers dubai": "AE",
        "jordan": "JO", "southern abdoun branch 17 maze": "JO",
        "lebanon": "LB", "lebenon": "LB", "leban": "LB",
        "oman": "OM", "sultanate of om": "OM",
        "palestine": "PS",
        "chine populaire": "CN", "chinoi": "CN", "suzhou branch": "CN",
        "china heilongjiang branch": "CN",
        "43 renming road gongyi zhenhzh": "CN",
        "21 shishan road suzhou": "CN", "321 fengqi road hangzhou": "CN",
        "unit 5 duilding 9 jincun f": "CN", "n9 jinrong 2nd street wuxi": "CN",
        "line 307 mengcheng bozhou anhu": "CN",
        "indi": "IN", "ind": "IN",
        "wakadewai mumbai pune road": "IN",
        "taluk theni distirict india": "IN",
        "fort market branch mumbai indi": "IN",
        "complex morbi-36642 gujarat india": "IN",
        "pakistane": "PK",
        "bangladech": "BD",
        "thailand": "TH", "125 ekkachai rd bang bon": "TH",
        "vietnam": "VN",
        "cambodia": "KH",
        "malaysia": "MY", "malisya": "MY", "malasya": "MY",
        "jalan yap kwan 50450 kuala lumpur": "MY",
        "55 jalan raja chulan 50200": "MY",
        "50300 kuala lumpur malaysia": "MY",
        "singapore": "SG", "singhaphore": "SG",
        "12 marina boulvard dbs asia": "SG",
        "exchange singapora 608526": "SG",
        "65chilia street ocbc centre": "SG",
        "1 wallich street 29 01 guoco": "SG",
        "indonesia": "ID", "indonossia": "ID",
        "philipines": "PH",
        "hong-kong": "HK", "hong kong": "HK", "hongkong": "HK", "honkong": "HK",
        "11th floor the center 99 queen": "HK",
        "3 garden road central hong kon": "HK",
        "tawer no 135hoi run rood hong kong": "HK",
        "hennessy road hong kong": "HK",
        "charter house 8 connaught road": "HK",
        "taiwan province de chine": "TW",
        "coree du sud": "KR", "coree republique de": "KR", "korea": "KR",
        "south korea": "KR", "coree": "KR",
        "coree du nord": "KP",
        "coree, rep. populaire democratique": "KP",
        "japon": "JP",
        "uzbekistan": "UZ",
        "toronto": "CA",
        "mexico": "MX",
        "bresil": "BR", "brazil": "BR",
        "argentina": "AR",
        "chile": "CL", "tchili": "CL",
        "colombia": "CO",
        "jamaica": "JM",
        "australia": "AU",
        "nouvelle zelande": "NZ", "new zealand": "NZ",
        "isle of man": "IM", "jersey": "JE", "gibraltar": "GI",
        "macao": "MO", "macau": "MO",
        "caïmans, ile": "KY",
        "an": "AN",
        "pb": "PG",
        "reg polaires, antarctique": "AQ", "antarctique": "AQ",
    }
    raw.update(custom)

    lookup: dict[str, str] = {}
    for k, v in raw.items():
        nk = _norm(str(k))
        if nk:
            lookup[nk] = v

    valid_iso2: set[str] = {c.alpha_2 for c in pycountry.countries} | {"XK", "AN"}
    return lookup, valid_iso2


_LOOKUP, _VALID_ISO2 = _build_lookup()
_REF_NAMES: list[str] = list(_LOOKUP.keys())
_ISO2_LIST: list[str] = sorted(_VALID_ISO2)

_ADDR_KEYWORDS: list[tuple[str, str]] = sorted(
    list(json.load(open(_REFERENTIEL_DIR / "pays_addr_keywords.json", encoding="utf-8"))["addr_keywords"].items()),
    key=lambda t: -len(t[0]),
)


def _extract_from_address(val_lower: str) -> Optional[str]:
    for kw, iso in _ADDR_KEYWORDS:
        if kw in val_lower:
            return iso
    return None


# ══════════════════════════════════════════════════════════════════════════════
# RÉSOLUTION DÉTERMINISTE — cascade non-pays → MAP exact → ISO-2 direct → FUZZY → ADDR → check
# ══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=16_384)
def get_iso2_with_method(raw_value: str) -> tuple[Optional[str], Optional[str]]:
    if pd.isna(raw_value) or str(raw_value).strip() == "":
        return None, None

    cleaned = clean_pays(str(raw_value))
    if not cleaned:
        return None, None

    c_lower = _norm(cleaned)

    if _RE_NON_PAYS.match(cleaned) or _RE_DIGITS_ONLY.match(cleaned):
        return "OUTLIER", "OUTLIER"

    if c_lower in _LOOKUP:
        return _LOOKUP[c_lower], "MAP"

    if len(cleaned) == 2 and cleaned in _VALID_ISO2:
        return cleaned, "MAP"

    best = rfuzz.extractOne(c_lower, _REF_NAMES, score_cutoff=FUZZY_CUTOFF)
    if best:
        match_key, _, _ = best
        return _LOOKUP[match_key], "FUZZY"

    iso = _extract_from_address(c_lower)
    if iso:
        return iso, "ADDR"

    return "check", "check"


# ══════════════════════════════════════════════════════════════════════════════
# RÈGLE NoAs / OUTLIER (spécifique Pays, voir docstring module)
# ══════════════════════════════════════════════════════════════════════════════

def _apply_na_rule_direct(
    pays_raw: str,
    ref_raw:  str,
    current_iso: Optional[str],
    current_mth: Optional[str],
) -> tuple:
    pays_lower = pays_raw.strip().lower()
    ref_empty  = _ref_is_empty(ref_raw)

    if pays_lower in ("", "nan", "none", "null"):
        return ("NoAs", "NoAs") if ref_empty else ("OUTLIER", "OUTLIER")

    if pays_lower == "na":
        if ref_empty:
            return "NoAs", "NoAs"
        return "NA", "MAP"

    if pays_lower in ("noas", "naos"):
        return ("NoAs", "NoAs") if ref_empty else ("OUTLIER", "OUTLIER")

    if current_iso == "OUTLIER":
        return "OUTLIER", "OUTLIER"

    return current_iso, current_mth


# ══════════════════════════════════════════════════════════════════════════════
# CLAUDE (remplace Ollama/qwen2.5 — voir docstring module) sur les valeurs "check"
# ══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT_PAYS_TEMPLATE = (
    "Tu es un expert en identification de pays a partir de valeurs saisies dans des "
    "transactions bancaires internationales (noms de pays en toute langue, villes, "
    "adresses postales, codes, abreviations).\n"
    "Voici la liste FERMEE des codes ISO-3166-1 alpha-2 valides :\n"
    "{liste}\n"
    "REGLES STRICTES :\n"
    "- Si la valeur designe un pays, ou une ville/adresse rattachable SANS AMBIGUITE "
    "a un pays, reponds avec son code ISO-2 EXACT parmi la liste ci-dessus, en MAJUSCULES.\n"
    "- Si aucun indice geographique n'est identifiable, reponds exactement OUTLIER.\n"
    "- Ne devine JAMAIS un pays au hasard : en cas de doute, OUTLIER.\n"
    "- Tu reponds UNIQUEMENT avec des lignes au format exact : N. CODE\n"
    "- Une ligne par item, dans le meme ordre. Zero explication, zero ligne vide."
)


def _warm_start_path(api_id: str) -> Path:
    return _REFERENTIEL_DIR / f"validated_classif_pays_{api_id.lower()}.json"


def load_warm_start_pays(api_id: str) -> dict:
    path = _warm_start_path(api_id)
    if not path.exists():
        return {}
    return json.load(open(path, encoding="utf-8")).get("classif", {})


def save_warm_start_pays(api_id: str, new_entries: dict, verbose: bool = False) -> None:
    if not new_entries:
        return
    path = _warm_start_path(api_id)
    data = {"_comment": "Cache warm-start NA exclu", "version": "1.0.0", "classif": {}}
    if path.exists():
        data = json.load(open(path, encoding="utf-8"))
        data.setdefault("classif", {})
    data["classif"].update(new_entries)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    if verbose:
        print(f"  Warm-start Pays {api_id} : +{len(new_entries)} modalité(s) mise(s) en cache")


def enrich_with_claude(
    df:         pd.DataFrame,
    api_id:     str,
    cfg:        dict,
    iso_col:    str = "Pays_Normalisé",
    method_col: str = "Pays_method",
    pays_col:   str = "Pays",
    verbose:    bool = False,
) -> pd.DataFrame:
    """Résout les valeurs encore 'check' (non résolues par la cascade déterministe)
    via Claude sur la liste fermée des codes ISO-2 valides. Contrairement à
    l'ancien repo (Ollama, non caché), les résolutions sont mises en cache — voir
    docstring module."""
    check_mask   = (df[iso_col] == "check") & df[pays_col].notna() & (df[pays_col].astype(str).str.strip() != "")
    check_values = [str(v) for v in df.loc[check_mask, pays_col].unique().tolist()]

    df["Pays_check"] = df[iso_col] == "check"
    if not check_values:
        return df

    system_prompt = _SYSTEM_PROMPT_PAYS_TEMPLATE.format(liste="\n".join(f"- {c}" for c in _ISO2_LIST))
    batch_size = cfg.get("llm", {}).get("batch_size", 25)
    resultats: dict[str, Optional[str]] = {}
    failed_techniquement: set = set()
    total = len(check_values)
    for debut in range(0, total, batch_size):
        batch_val = check_values[debut:debut + batch_size]
        reponses  = call_claude_match_batch(batch_val, _ISO2_LIST, system_prompt, cfg)
        if reponses is None:
            print(f"  [CLAUDE] échec technique sur le batch {debut}-{debut+len(batch_val)} "
                  f"-> OUTLIER temporaire (non mis en cache, réessayé au prochain run)")
            for v in batch_val:
                resultats[v] = None
                failed_techniquement.add(v)
            continue
        for k, v in enumerate(batch_val):
            resultats[v] = reponses[k]
        if verbose:
            print(f"  [CLAUDE Pays] {min(debut + batch_size, total)}/{total} valeurs", end="\r")
    if total and verbose:
        print()

    cacheable = {
        v: (iso or "OUTLIER")
        for v, iso in resultats.items()
        if v not in failed_techniquement
    }
    if cacheable:
        save_warm_start_pays(api_id, cacheable, verbose=verbose)

    llm_df = pd.DataFrame([
        {pays_col: v, "_llm_iso": resultats.get(v), "_llm_mth": "CLAUDE" if resultats.get(v) else "OUTLIER"}
        for v in check_values
    ])
    df = df.merge(llm_df, on=pays_col, how="left")

    mask_check = df[iso_col] == "check"
    df.loc[mask_check, iso_col]    = df.loc[mask_check, "_llm_iso"].fillna("OUTLIER")
    df.loc[mask_check, method_col] = df.loc[mask_check, "_llm_mth"].fillna("OUTLIER")

    df.drop(columns=["_llm_iso", "_llm_mth"], inplace=True)
    df["Pays_check"] = df[iso_col] == "OUTLIER"
    return df


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def treating_pays(
    df:         pd.DataFrame,
    pays_col:   str  = "Pays",
    ref_col:    str  = "NumCredoc",
    api_id:     str  = "E08_OCD",
    cfg:        dict = None,
    warm_start: bool = True,
    verbose:    bool = False,
) -> pd.DataFrame:
    """Normalise la colonne Pays. Traitement sur valeurs uniques puis merge."""
    df = df.copy()
    cfg = cfg or {}

    ws_cache: dict = {}
    if warm_start:
        ws_cache = load_warm_start_pays(api_id)
        if verbose:
            print(f"  Warm-start Pays {api_id} : {len(ws_cache)} modalités connues")
    ws_cache_upper: dict = {k.strip().upper(): v for k, v in ws_cache.items()} if warm_start else {}

    unique_pays = df[pays_col].dropna().unique()
    clean_map   = {v: clean_pays(str(v)) for v in unique_pays}
    clean_map[None] = ""
    df["Pays_clean"] = df[pays_col].map(clean_map).fillna("")

    iso_map: dict = {}
    for v in unique_pays:
        if warm_start:
            s = str(v)
            if s in ws_cache:
                iso_map[v] = (ws_cache[s], "WARM"); continue
            if s.strip() in ws_cache:
                iso_map[v] = (ws_cache[s.strip()], "WARM"); continue
            if s.rstrip() in ws_cache:
                iso_map[v] = (ws_cache[s.rstrip()], "WARM"); continue
            key_up = s.strip().upper()
            if key_up in ws_cache_upper:
                iso_map[v] = (ws_cache_upper[key_up], "WARM"); continue

        iso_map[v] = get_iso2_with_method(str(v))

    df["Pays_Normalisé"] = df[pays_col].map(lambda v: iso_map.get(v, (None, None))[0])
    df["Pays_method"]    = df[pays_col].map(lambda v: iso_map.get(v, (None, None))[1])
    df["_ws_hit"] = df[pays_col].map(lambda v: iso_map.get(v, (None, None))[1] == "WARM")

    if ref_col in df.columns:
        pairs     = df[[pays_col, ref_col]].drop_duplicates()
        pays_vals = pairs[pays_col].tolist()
        ref_vals  = pairs[ref_col].tolist()
        pair_iso, pair_mth = [], []
        for pv, rv in zip(pays_vals, ref_vals):
            c_iso, c_mth = iso_map.get(pv, (None, None))
            res_iso, res_mth = _apply_na_rule_direct(str(pv), str(rv), c_iso, c_mth)
            pair_iso.append(res_iso)
            pair_mth.append(res_mth)

        pair_df = pd.DataFrame({pays_col: pays_vals, ref_col: ref_vals, "_pair_iso": pair_iso, "_pair_mth": pair_mth})
        df = df.merge(pair_df, on=[pays_col, ref_col], how="left")
        df["Pays_Normalisé"] = df["_pair_iso"].where(df["_pair_iso"].notna(), df["Pays_Normalisé"])
        df["Pays_method"]    = df["_pair_mth"].where(df["_pair_mth"].notna(), df["Pays_method"])
        df.drop(columns=["_pair_iso", "_pair_mth"], inplace=True)

    df = enrich_with_claude(df, api_id, cfg, pays_col=pays_col, verbose=verbose)
    return df


def build_full_classification_pays(api_id: str, col_in: str, col_out: str) -> pd.DataFrame:
    """
    Table de classification pour la BI — Pays n'a pas de "référentiel Excel BCM"
    comme les autres champs (son référentiel EST le code, voir _build_lookup()) :
    la table exposée ici est donc uniquement le cache warm-start (valeurs déjà
    résolues, manuellement ou via Claude), pas le référentiel pycountry/babel au
    complet (des dizaines de milliers d'alias/villes, sans valeur pour la BI).
    """
    combined = load_warm_start_pays(api_id)
    if not combined:
        return pd.DataFrame(columns=[col_in, col_out])
    df = pd.DataFrame(list(combined.items()), columns=[col_in, col_out])
    return df.drop_duplicates().sort_values([col_out, col_in]).reset_index(drop=True)


def build_pays_processor(field_cfg: dict) -> CategoricalFieldProcessor:
    """Factory : construit le FieldProcessor Pays à partir du bloc YAML `fields[]`."""
    cols = field_cfg["columns"]

    return CategoricalFieldProcessor(
        field_name=field_cfg["name"],
        treating_fn=treating_pays,
        treating_kwargs={
            "pays_col": cols["field"],
            "ref_col": cols["ref_transaction"],
            "api_id": None,          # rempli dynamiquement par CategoricalFieldProcessor.process()
            "cfg": field_cfg,
            "warm_start": True,
            "verbose": False,
        },
        col_in=cols["field"],
        col_out=cols["field_out"],
        ref_banque_col=cols.get("ref_banque", "RefBanque"),
        outlier_tag=field_cfg.get("outlier_tag", "OUTLIER"),
        exclude_suffixes=("_clean", "_method", "_check"),
        clean_fn=clean_pays,
        save_warm_start_fn=save_warm_start_pays,
        classification_fn=lambda api_id: build_full_classification_pays(
            api_id, cols["field"], cols["field_out"]
        ),
    )
