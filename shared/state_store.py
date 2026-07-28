"""
shared/state_store.py
=======================
État du traitement incrémental (Incremental Load) — stocké dans un FICHIER JSON
LOCAL, PAS dans une table SQL Server. Décision explicite : toute modification du
schéma de la base de production (CREATE TABLE) est interdite côté métier — la
base reste en lecture seule (SELECT sur la table source E11 uniquement). Ce
module simule donc localement ce qu'une table de contrôle aurait fait.

Mémorise, par API, le dernier `dtCr` traité avec succès (pour que le run suivant
ne récupère que le delta), ainsi que des compteurs CUMULATIFS depuis le premier
run (lignes traitées, outliers détectés, nombre de runs) — utilisés pour la
section "stats globales" de l'email de notification.

Le "watermark" (`last_dtcr_processed`) n'avance JAMAIS sur un run KO, ni sur un
run OK sans nouvelle ligne — dans les deux cas le prochain run retente exactement
la même fenêtre (idempotent en cas d'échec). Les compteurs cumulatifs suivent la
même règle : ils n'augmentent que sur un run OK (un run KO n'a rien traité).

Fichier : {STATE_DIR}/{api_id}_run_state.json (STATE_DIR configurable via .env,
défaut "e11_rdcc/state/" — gitignored, propre à chaque machine/environnement).
Écriture atomique (fichier temporaire + remplacement) pour ne jamais corrompre
l'état si le processus est interrompu en cours d'écriture.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class RunState:
    api_id: str
    last_dtcr_processed: Optional[datetime]
    last_run_status: Optional[str]
    last_run_mode: Optional[str]
    cumulative_rows: int = 0
    cumulative_outliers: int = 0
    cumulative_runs: int = 0
    first_run_datetime: Optional[datetime] = None


def _state_dir() -> Path:
    # os.getenv(..., default) ne retombe sur le défaut QUE si la variable est absente —
    # si STATE_DIR="" est présente mais vide dans .env, il faut explicitement l'ignorer
    # (même garde que OUTPUT_BASE dans shared/base_api_pipeline.py::write_output).
    value = os.getenv("STATE_DIR", "").strip()
    return Path(value) if value else Path("e11_rdcc/state")


def _state_path(api_id: str) -> Path:
    return _state_dir() / f"{api_id}_run_state.json"


def _to_json(state: RunState) -> dict:
    data = asdict(state)
    for key in ("last_dtcr_processed", "first_run_datetime"):
        if data[key] is not None:
            data[key] = data[key].isoformat()
    return data


def _from_json(data: dict) -> RunState:
    for key in ("last_dtcr_processed", "first_run_datetime"):
        if data.get(key):
            data[key] = datetime.fromisoformat(data[key])
        else:
            data[key] = None
    return RunState(**data)


def _write_state(state: RunState) -> None:
    directory = _state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = _state_path(state.api_id)

    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=f".{state.api_id}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_to_json(state), f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)  # atomique sur Windows/POSIX
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def ensure_state_table(engine=None) -> None:
    """
    Conservée pour compatibilité d'appel avec l'ancien code basé sur SQL Server —
    ne fait plus rien (aucune table à créer, stockage local). `engine` ignoré.
    """
    return None


def get_run_state(api_id: str) -> Optional[RunState]:
    path = _state_path(api_id)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return _from_json(data)


def seed_initial_state(api_id: str, last_dtcr_processed: Optional[datetime] = None) -> None:
    """
    Création manuelle d'un état initial (scripts/seed_state.py) — n'est JAMAIS
    appelée automatiquement par le pipeline, pour ne pas marquer silencieusement
    un run partiel/échoué comme "seedé". N'écrase pas un état existant.
    """
    if get_run_state(api_id) is not None:
        return
    _write_state(RunState(
        api_id=api_id,
        last_dtcr_processed=last_dtcr_processed,
        last_run_status="OK",
        last_run_mode="INITIAL",
        first_run_datetime=datetime.now(),
    ))


def _should_update_watermark(status: str, max_dtcr: Optional[datetime]) -> bool:
    """
    Pure function (indépendamment testable) : le watermark n'avance QUE si le
    run est OK et qu'au moins une ligne a été traitée.
    """
    return status == "OK" and max_dtcr is not None


def record_run_result(
    api_id: str,
    mode: str,
    status: str,
    max_dtcr_processed: Optional[datetime],
    rows_processed: int,
    outliers_this_run: int = 0,
) -> RunState:
    """
    Enregistre le résultat du run ET met à jour les compteurs cumulatifs (seulement
    si status == 'OK' — un run KO n'a rien traité). `max_dtcr_processed` doit être
    calculé par l'appelant à partir des données réellement traitées
    (db_connector.get_max_dtcr), jamais datetime.now() — pour rester exact/idempotent.

    Retourne l'état à jour (utilisé pour la section "stats globales" de l'email).
    """
    existing = get_run_state(api_id)
    advance = _should_update_watermark(status, max_dtcr_processed)
    add_rows = rows_processed if status == "OK" else 0
    add_outliers = outliers_this_run if status == "OK" else 0

    if existing is None:
        new_state = RunState(
            api_id=api_id,
            last_dtcr_processed=max_dtcr_processed if advance else None,
            last_run_status=status,
            last_run_mode=mode,
            cumulative_rows=add_rows,
            cumulative_outliers=add_outliers,
            cumulative_runs=1,
            first_run_datetime=datetime.now(),
        )
    else:
        new_state = RunState(
            api_id=api_id,
            last_dtcr_processed=max_dtcr_processed if advance else existing.last_dtcr_processed,
            last_run_status=status,
            last_run_mode=mode,
            cumulative_rows=existing.cumulative_rows + add_rows,
            cumulative_outliers=existing.cumulative_outliers + add_outliers,
            cumulative_runs=existing.cumulative_runs + 1,
            first_run_datetime=existing.first_run_datetime,
        )

    _write_state(new_state)
    return new_state
