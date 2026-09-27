"""
shared/claude_batches.py
==========================
Exécution des appels Claude par lots — commun à tous les champs qui ont un
fallback LLM (NomDonneurOrdre/Beneficiaire web, arbitrage DGI, NatureEconomique,
Pays, NomCorrespondant).

Pourquoi : chaque appel Claude dure de quelques secondes à plus d'une minute
quand il déclenche une recherche web, et ce temps est presque entièrement de
l'attente réseau. Enchaîner les lots un par un rendait le premier chargement
d'E07/E10 interminable (des milliers de valeurs nouvelles à résoudre). Les lots
sont donc lancés en parallèle (`llm.concurrency`), ce qui divise le temps d'attente
d'autant, sans changer ni les résultats ni le contenu des lots.

Deux garde-fous :
  - `max_values` (`llm.max_values_per_run`) borne le nombre de valeurs NOUVELLES
    résolues dans un run. Les valeurs au-delà ne sont pas envoyées, restent
    OUTLIER pour ce run et ne sont PAS mises en cache : elles seront proposées au
    run suivant. Utile pour un premier chargement (coût et durée bornés).
  - un échec TECHNIQUE (appel qui renvoie None) n'est jamais mis en cache — même
    contrat que les fonctions de shared/claude_client.py.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

DEFAULT_CONCURRENCY = 4


def llm_options(cfg: Optional[dict]) -> tuple[int, int, Optional[int]]:
    """
    (batch_size, concurrency, max_values) lus dans le bloc `llm` du champ.
    `concurrency` absent -> défaut ; 0 ou 1 -> lots enchaînés un par un.
    `max_values_per_run` absent ou 0 -> aucun plafond.
    """
    llm = (cfg or {}).get("llm", {}) or {}
    batch_size = int(llm.get("batch_size") or 20)
    demandee = llm.get("concurrency")
    concurrency = DEFAULT_CONCURRENCY if demandee is None else max(1, int(demandee))
    max_values = llm.get("max_values_per_run") or None
    return batch_size, concurrency, int(max_values) if max_values else None


def resolve_in_batches(
    valeurs:     list,
    call_fn:     Callable[[list], Optional[list]],
    batch_size:  int,
    concurrency: int = 1,
    max_values:  Optional[int] = None,
    libelle:     str = "CLAUDE",
    verbose:     bool = True,
) -> tuple[dict, set, list]:
    """
    Découpe `valeurs` en lots de `batch_size`, appelle `call_fn(lot)` (jusqu'à
    `concurrency` lots en parallèle) et rassemble les réponses.

    Retourne (resultats, echecs_techniques, reportees) :
      - resultats : {valeur -> réponse ou None} pour les valeurs envoyées
      - echecs_techniques : valeurs des lots en échec technique (jamais à cacher)
      - reportees : valeurs non envoyées faute de quota `max_values`
    """
    a_traiter = list(valeurs)
    reportees: list = []
    if max_values is not None and len(a_traiter) > max_values:
        reportees = a_traiter[max_values:]
        a_traiter = a_traiter[:max_values]
        if verbose:
            print(f"  [{libelle}] {len(reportees)} valeur(s) nouvelle(s) au-delà du plafond "
                  f"llm.max_values_per_run={max_values} : laissées en OUTLIER, non mises en cache, "
                  f"reproposées au prochain run.")

    lots = [a_traiter[i:i + batch_size] for i in range(0, len(a_traiter), batch_size)]
    resultats: dict = {}
    echecs: set = set()
    if not lots:
        return resultats, echecs, reportees

    def _appeler(lot):
        return lot, call_fn(lot)

    total = len(lots)
    faits = 0
    with ThreadPoolExecutor(max_workers=min(concurrency, total)) as pool:
        for lot, reponses in pool.map(_appeler, lots):
            faits += 1
            if reponses is None:
                echecs.update(lot)
                for v in lot:
                    resultats[v] = None
            else:
                for i, v in enumerate(lot):
                    resultats[v] = reponses[i] if i < len(reponses) else None
            if verbose and (faits == total or faits % 10 == 0):
                print(f"  [{libelle}] {faits}/{total} lots traités "
                      f"({min(faits * batch_size, len(a_traiter))}/{len(a_traiter)} valeurs)", flush=True)
    return resultats, echecs, reportees
