# scripts/run_weekly.ps1
# Cible du job Windows Task Scheduler (chaque lundi 10h) — lance la pipeline
# E11_RDCC en mode incremental (delta uniquement, cf. shared/state_store.py).
#
# Configuration Task Scheduler recommandee :
#   Programme      : powershell.exe
#   Arguments      : -NoProfile -ExecutionPolicy Bypass -File "<repo>\scripts\run_weekly.ps1"
#   Demarrer dans  : <repo>  (repertoire racine du repo)
#   Declencheur    : hebdomadaire, lundi, 10:00
#
# Prerequis avant la toute premiere activation du job : lancer une fois
#   python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode initial
# pour que ce run incremental ait un etat de depart (voir README).

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

& $python -m e11_rdcc.run_pipeline --config "e11_rdcc/config/E11_RDCC.yaml" --mode incremental
exit $LASTEXITCODE
