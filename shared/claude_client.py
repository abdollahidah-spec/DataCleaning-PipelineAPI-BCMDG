"""
shared/claude_client.py
=========================
Client Anthropic Claude — utilisé par le champ NomCorrespondant pour résoudre
les libellés bruts de banques correspondantes absents du référentiel.
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
