"""
shared/claude_client.py
=========================
Client Anthropic Claude — utilisé par les champs catégoriels qui ont besoin
d'un fallback LLM pour résoudre une valeur brute absente du référentiel/cache
(NomCorrespondant, Produits...).

`call_claude_nomcorrespondant_batch` est la fonction d'origine (protocole
"liste numérotée", un label libre par ligne). `call_claude_match_batch` est une
primitive plus générique ajoutée ensuite (mêmes retry/parsing, mais avec une
LISTE FERMÉE de labels valides fournie dans le prompt) — pour les champs où la
réponse doit obligatoirement être une valeur du référentiel (ou OUTLIER), sans
laisser Claude inventer un libellé (voir e08_ocd/fields/produits.py).
"""
from __future__ import annotations

import os
import re
import time


_SYSTEM_PROMPT = (
    "Tu es un expert bancaire international specialise dans l'identification "
    "des banques correspondantes et de leurs codes SWIFT/BIC officiels publies.\n"
    "Pour chaque libelle brut fourni (nom de banque saisi par un operateur, "
    "souvent abrege, tronque ou mal orthographie), determine s'il designe "
    "sans ambiguite une VRAIE banque existante possedant un VRAI code SWIFT/BIC.\n"
    "REGLES STRICTES :\n"
    "- Si tu identifies la banque avec certitude, reponds avec son nom legal "
    "officiel complet, en MAJUSCULES, sans code SWIFT ni commentaire.\n"
    "- Si le libelle est ambigu, trop vague, ou ne correspond a aucune banque "
    "reelle connue avec un code SWIFT/BIC verifiable, reponds exactement OUTLIER.\n"
    "- Ne reponds JAMAIS par un nom invente : en cas de doute, OUTLIER.\n"
    "- Tu reponds UNIQUEMENT avec des lignes au format exact : N. REPONSE\n"
    "- Une ligne par item, dans le meme ordre. Zero explication, zero ligne vide."
)


def call_claude_nomcorrespondant_batch(
    batch: list[str],
    cfg:   dict | None = None,
) -> list[str | None] | None:
    """
    Résout un batch de libellés bruts de banques correspondantes via l'API Claude.

    Retourne :
      - une liste de labels (nom légal officiel en MAJUSCULES) ou None par item
        (None = verdict explicite OUTLIER de Claude), dans le même ordre que `batch` ;
      - None (et non une liste) si l'appel a échoué techniquement (clé absente/
        invalide, réseau, rate-limit épuisé...) — à distinguer d'un verdict OUTLIER :
        l'appelant ne doit JAMAIS mettre en cache un échec technique, sous peine de
        blacklister définitivement des banques réelles suite à un simple incident API.
    """
    cfg        = cfg or {}
    llm_cfg    = cfg.get("llm", {})
    model      = llm_cfg.get("model",      "claude-sonnet-5")
    max_retry  = llm_cfg.get("max_retry",  3)
    retry_wait = llm_cfg.get("retry_wait", 2)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[CLAUDE] ANTHROPIC_API_KEY absente — appel ignoré (échec technique, pas de cache)")
        return None

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    items       = "\n".join(f"{i + 1}. {v}" for i, v in enumerate(batch))
    user_prompt = f"Libellés à identifier :\n{items}"

    text = None
    for attempt in range(1, max_retry + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=40 * len(batch) + 50,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", "") == "text"
            )
            break
        except Exception as e:
            print(f"[CLAUDE ERROR] tentative {attempt}/{max_retry} : {e}")
            if attempt < max_retry:
                time.sleep(retry_wait * attempt)

    if text is None:
        return None  # échec technique — ne pas confondre avec un verdict OUTLIER

    resultats = [None] * len(batch)
    pattern   = re.compile(r"^(\d+)[.\)]\s*(.+)$")
    for ligne in text.strip().split("\n"):
        m = pattern.match(ligne.strip())
        if not m:
            continue
        idx = int(m.group(1)) - 1
        val = m.group(2).strip().upper()
        if 0 <= idx < len(batch) and val and val != "OUTLIER":
            resultats[idx] = val
    return resultats


def call_claude_match_batch(
    batch:         list[str],
    valid_labels:  list[str],
    system_prompt: str,
    cfg:           dict | None = None,
) -> list[str | None] | None:
    """
    Résout un batch de valeurs brutes en les faisant correspondre à UNE valeur
    d'une liste FERMÉE (`valid_labels`, ex: labels du référentiel Produits) — Claude
    doit obligatoirement choisir dans cette liste ou répondre OUTLIER, jamais
    inventer une valeur hors liste. `system_prompt` doit déjà contenir la liste des
    labels valides (voir e08_ocd/fields/produits.py pour la construction du prompt).

    Retour : même contrat que call_claude_nomcorrespondant_batch — liste de labels
    (garantis appartenir à `valid_labels`) ou None par item, dans l'ordre de `batch` ;
    None (pas une liste) si l'appel a échoué techniquement (jamais mis en cache).
    """
    cfg        = cfg or {}
    llm_cfg    = cfg.get("llm", {})
    model      = llm_cfg.get("model",      "claude-sonnet-5")
    max_retry  = llm_cfg.get("max_retry",  3)
    retry_wait = llm_cfg.get("retry_wait", 2)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[CLAUDE] ANTHROPIC_API_KEY absente — appel ignoré (échec technique, pas de cache)")
        return None

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    items       = "\n".join(f"{i + 1}. {v}" for i, v in enumerate(batch))
    user_prompt = f"Valeurs à classifier :\n{items}"

    valid_set = set(valid_labels)
    text = None
    for attempt in range(1, max_retry + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=60 * len(batch) + 50,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", "") == "text"
            )
            break
        except Exception as e:
            print(f"[CLAUDE ERROR] tentative {attempt}/{max_retry} : {e}")
            if attempt < max_retry:
                time.sleep(retry_wait * attempt)

    if text is None:
        return None  # échec technique — ne pas confondre avec un verdict OUTLIER

    resultats = [None] * len(batch)
    pattern   = re.compile(r"^(\d+)[.\)]\s*(.+)$")
    for ligne in text.strip().split("\n"):
        m = pattern.match(ligne.strip())
        if not m:
            continue
        idx = int(m.group(1)) - 1
        val = m.group(2).strip()
        if not (0 <= idx < len(batch)) or not val or val.upper() == "OUTLIER":
            continue
        # Garde-fou : Claude doit choisir dans la liste fournie — une réponse hors
        # liste (hallucination/format inattendu) est traitée comme non résolue
        # plutôt que silencieusement acceptée.
        if val in valid_set:
            resultats[idx] = val
    return resultats


# ══════════════════════════════════════════════════════════════════════════════
# NomDonneurOrdre/Bénéficiaire E08 — arbitrage entre candidats DGI proches
# ══════════════════════════════════════════════════════════════════════════════
# Portée telle quelle depuis DataCleaning-PipelineField-BCMDG/shared/claude_client.py
# (call_claude_nomdonneurordre_dgi_arbitrage_batch), réutilisée pour les deux champs
# E08 qui font du matching DGI (e08_ocd/fields/nomdonneurordre.py) — même prompt,
# même contrat, un seul candidat 1/2/3 choisi ou OUTLIER si aucun ne correspond
# avec certitude.

_SYSTEM_PROMPT_DGI_ARBITRAGE = (
    "Tu es un expert du registre fiscal mauritanien (DGI - Direction Generale des Impots).\n"
    "Pour chaque item, on te donne un libelle d'entreprise saisi par un operateur bancaire "
    "et une liste courte (2-3) de raisons sociales candidates issues de la base DGI qui lui "
    "ressemblent textuellement, avec leur NIF et forme juridique.\n"
    "Determine SI ET SEULEMENT SI le libelle designe sans ambiguite l'une de ces raisons "
    "sociales candidates (la MEME entite legale, pas seulement un nom proche ou une entite "
    "homonyme differente).\n"
    "REGLES STRICTES :\n"
    "- Si un candidat correspond avec certitude, reponds avec son numero (1, 2 ou 3).\n"
    "- Si aucun candidat ne correspond avec certitude, ou si plusieurs candidats sont "
    "plausibles sans element distinctif suffisant pour trancher (homonymie, cas ambigu), "
    "reponds exactement OUTLIER. Ne devine JAMAIS.\n"
    "- Tu reponds UNIQUEMENT avec des lignes au format exact : N. REPONSE (ou REPONSE est "
    "un numero de candidat ou OUTLIER).\n"
    "- Une ligne par item, dans le meme ordre. Zero explication, zero ligne vide."
)


def call_claude_dgi_arbitrage_batch(
    items: list[dict],
    cfg:   dict | None = None,
) -> list[int | None] | None:
    """
    Tranche, pour chaque item {"label": str, "candidates": [{"raison_sociale","nif",
    "forme_juridique"}, ...]}, quel candidat (1-based index) correspond au libellé —
    ou None si aucun candidat ne correspond avec certitude (OUTLIER).

    Retourne None (pas une liste) en cas d'échec technique — jamais mis en cache par
    l'appelant, cf. call_claude_nomcorrespondant_batch.
    """
    cfg        = cfg or {}
    llm_cfg    = cfg.get("llm", {})
    model      = llm_cfg.get("model",      "claude-sonnet-5")
    max_retry  = llm_cfg.get("max_retry",  3)
    retry_wait = llm_cfg.get("retry_wait", 2)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[CLAUDE] ANTHROPIC_API_KEY absente — appel ignoré (échec technique, pas de cache)")
        return None

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    lignes = []
    for i, item in enumerate(items):
        lignes.append(f"{i + 1}. Libelle : {item['label']}")
        for c, cand in enumerate(item["candidates"]):
            lignes.append(
                f"   Candidat {c + 1} : {cand.get('raison_sociale', '')} "
                f"(NIF {cand.get('nif', '') or 'inconnu'}, "
                f"{cand.get('forme_juridique', '') or 'forme juridique inconnue'})"
            )
    user_prompt = "Items a arbitrer :\n" + "\n".join(lignes)

    text = None
    for attempt in range(1, max_retry + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=40 * len(items) + 50,
                system=_SYSTEM_PROMPT_DGI_ARBITRAGE,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", "") == "text"
            )
            break
        except Exception as e:
            print(f"[CLAUDE ERROR] tentative {attempt}/{max_retry} : {e}")
            if attempt < max_retry:
                time.sleep(retry_wait * attempt)

    if text is None:
        return None

    resultats: list[int | None] = [None] * len(items)
    pattern = re.compile(r"^(\d+)[.\)]\s*(.+)$")
    for ligne in text.strip().split("\n"):
        m = pattern.match(ligne.strip())
        if not m:
            continue
        idx = int(m.group(1)) - 1
        val = m.group(2).strip().upper()
        if not (0 <= idx < len(items)):
            continue
        if val == "OUTLIER":
            resultats[idx] = None
        elif val.isdigit():
            resultats[idx] = int(val)
    return resultats


# ══════════════════════════════════════════════════════════════════════════════
# Bénéficiaire E08 — Claude + recherche web (bénéficiaire étranger d'un crédit
# documentaire import, pas de base fiscale mauritanienne pertinente)
# ══════════════════════════════════════════════════════════════════════════════
# Portée telle quelle depuis DataCleaning-PipelineField-BCMDG/shared/claude_client.py
# (call_claude_beneficiaire_web_batch). Outil serveur web_search — facturé à
# l'usage (cfg["llm"]["web_search_max_uses"], défaut 3 recherches/item).

_SYSTEM_PROMPT_BENEF_WEB = (
    "Tu es un expert en identification d'entreprises (registres du commerce, bases "
    "d'immatriculation officielles, codes SWIFT/BIC) partout dans le monde.\n"
    "Pour chaque libelle brut fourni (nom du BENEFICIAIRE d'un paiement sortant ou "
    "d'un credit documentaire, saisi par un operateur bancaire mauritanien, souvent "
    "abrege, tronque ou mal orthographie), utilise la recherche web pour determiner "
    "s'il designe sans ambiguite une VRAIE entreprise existante, et retrouver sa "
    "raison sociale legale exacte telle que documentee officiellement.\n"
    "REGLES STRICTES :\n"
    "- Si tu identifies l'entreprise avec certitude (source officielle ou fiable trouvee "
    "via la recherche web), reponds avec sa raison sociale legale complete, en MAJUSCULES, "
    "sans commentaire.\n"
    "- Si le libelle est ambigu, trop vague, ou ne correspond a aucune entreprise reelle "
    "verifiable, reponds exactement OUTLIER.\n"
    "- Mefie-toi particulierement des libelles COURTS ou CRYPTIQUES (acronymes de "
    "3-6 lettres, codes sans mot reconnaissable) : de tels libelles correspondent "
    "souvent PAR HASARD a plusieurs entreprises sans rapport entre elles. Ne resous "
    "un tel libelle QUE si une source fiable le lie explicitement et sans ambiguite "
    "a CE libelle precis (ex : un vrai code SWIFT/BIC officiel qui correspond "
    "exactement). Un acronyme plausible mais non verifie doit rester OUTLIER.\n"
    "- Ne reponds JAMAIS par un nom invente, une simple reformulation du libelle brut, "
    "ou une entreprise 'la plus proche' sans lien documente certain : en cas de doute, "
    "OUTLIER. Un faux positif est pire qu'un OUTLIER manque.\n"
    "- Tu reponds UNIQUEMENT avec des lignes au format exact : N. REPONSE\n"
    "- Une ligne par item, dans le meme ordre. Zero explication, zero ligne vide."
)


def call_claude_beneficiaire_web_batch(
    batch: list[str],
    cfg:   dict | None = None,
) -> list[str | None] | None:
    """
    Résout un batch de libellés Beneficiaire (E08, bénéficiaire étranger) via
    Claude + recherche web réelle (tool serveur web_search). Même contrat que
    call_claude_nomcorrespondant_batch : liste de labels/None dans l'ordre du
    batch, ou None si échec technique (pas de cache).

    Batch volontairement petit par défaut (cfg["llm"]["batch_size"], défaut 5) pour
    garder une recherche pertinente par item.
    """
    cfg        = cfg or {}
    llm_cfg    = cfg.get("llm", {})
    model      = llm_cfg.get("model",      "claude-sonnet-5")
    max_retry  = llm_cfg.get("max_retry",  3)
    retry_wait = llm_cfg.get("retry_wait", 2)
    max_uses   = llm_cfg.get("web_search_max_uses", 3)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[CLAUDE] ANTHROPIC_API_KEY absente — appel ignoré (échec technique, pas de cache)")
        return None

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    items       = "\n".join(f"{i + 1}. {v}" for i, v in enumerate(batch))
    user_prompt = f"Libellés de bénéficiaires à identifier :\n{items}"

    text = None
    for attempt in range(1, max_retry + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1024 + 60 * len(batch),
                system=_SYSTEM_PROMPT_BENEF_WEB,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[{
                    "type":     "web_search_20250305",
                    "name":     "web_search",
                    "max_uses": max_uses * len(batch),
                }],
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", "") == "text"
            )
            break
        except Exception as e:
            print(f"[CLAUDE ERROR] tentative {attempt}/{max_retry} : {e}")
            if attempt < max_retry:
                time.sleep(retry_wait * attempt)

    if text is None:
        return None

    resultats = [None] * len(batch)
    pattern   = re.compile(r"^(\d+)[.\)]\s*(.+)$")
    for ligne in text.strip().split("\n"):
        m = pattern.match(ligne.strip())
        if not m:
            continue
        idx = int(m.group(1)) - 1
        val = m.group(2).strip().upper()
        if 0 <= idx < len(batch) and val and val != "OUTLIER":
            resultats[idx] = val
    return resultats
