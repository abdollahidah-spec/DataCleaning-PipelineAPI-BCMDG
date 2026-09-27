"""
Exécution des appels Claude par lots (shared/claude_batches.py) : découpage,
parallélisme, plafond par run, et échecs techniques jamais confondus avec un
verdict OUTLIER. Aucun appel réseau — la fonction d'appel est simulée.
"""
import threading
import time

import pytest

from shared.claude_batches import DEFAULT_CONCURRENCY, llm_options, resolve_in_batches


def test_decoupage_et_ordre_des_reponses():
    lots = []

    def call(lot):
        lots.append(list(lot))
        return [v.lower() for v in lot]

    resultats, echecs, reportees = resolve_in_batches(["A", "B", "C"], call, batch_size=2, verbose=False)
    assert lots == [["A", "B"], ["C"]]
    assert resultats == {"A": "a", "B": "b", "C": "c"} and not echecs and not reportees


def test_lots_executes_en_parallele():
    """Les 4 lots doivent être en vol EN MÊME TEMPS (c'est là qu'est le gain : le
    temps d'un appel Claude est de l'attente réseau). Critère structurel — le
    nombre d'appels simultanés — et non une durée, qui dépendrait de la charge
    de la machine et rendrait le test instable."""
    barriere = threading.Barrier(4, timeout=10)
    simultanes, maximum, verrou = 0, [0], threading.Lock()

    def call(lot):
        nonlocal simultanes
        with verrou:
            simultanes += 1
            maximum[0] = max(maximum[0], simultanes)
        # Ne rend la main que quand les 4 lots sont arrivés : impossible à franchir
        # si les lots sont enchaînés un par un (l'attente expirerait).
        barriere.wait()
        with verrou:
            simultanes -= 1
        return list(lot)

    resultats, _, _ = resolve_in_batches(list("abcd"), call, batch_size=1, concurrency=4, verbose=False)
    assert maximum[0] == 4
    assert set(resultats) == set("abcd")


def test_concurrence_1_enchaine_les_lots():
    ordre = []

    def call(lot):
        ordre.append(("debut", lot[0]))
        time.sleep(0.01)
        ordre.append(("fin", lot[0]))
        return list(lot)

    resolve_in_batches(list("ab"), call, batch_size=1, concurrency=1, verbose=False)
    assert ordre == [("debut", "a"), ("fin", "a"), ("debut", "b"), ("fin", "b")]


def test_echec_technique_signale_sans_verdict():
    def call(lot):
        return None if "B" in lot else list(lot)

    resultats, echecs, _ = resolve_in_batches(["A", "B"], call, batch_size=1, verbose=False)
    assert echecs == {"B"}
    assert resultats == {"A": "A", "B": None}


def test_plafond_par_run_reporte_le_reste():
    envoyees = []

    def call(lot):
        envoyees.extend(lot)
        return list(lot)

    resultats, _, reportees = resolve_in_batches(list("abcde"), call, batch_size=2,
                                                 max_values=3, verbose=False)
    assert envoyees == ["a", "b", "c"] and reportees == ["d", "e"]
    assert set(resultats) == {"a", "b", "c"}


def test_reponse_plus_courte_que_le_lot():
    """Une réponse tronquée ne doit pas décaler les valeurs suivantes."""
    resultats, _, _ = resolve_in_batches(["A", "B"], lambda lot: ["a"], batch_size=2, verbose=False)
    assert resultats == {"A": "a", "B": None}


def test_aucune_valeur_aucun_appel():
    resultats, echecs, reportees = resolve_in_batches([], lambda lot: pytest.fail("appel inattendu"),
                                                      batch_size=5, verbose=False)
    assert resultats == {} and not echecs and not reportees


@pytest.mark.parametrize("bloc,attendu", [
    ({}, (20, DEFAULT_CONCURRENCY, None)),
    ({"llm": {"batch_size": 5, "concurrency": 8, "max_values_per_run": 1000}}, (5, 8, 1000)),
    ({"llm": {"concurrency": 0, "max_values_per_run": 0}}, (20, 1, None)),
])
def test_options_llm(bloc, attendu):
    assert llm_options(bloc) == attendu
