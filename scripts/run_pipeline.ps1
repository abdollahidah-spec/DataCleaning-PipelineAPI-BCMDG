# scripts/run_pipeline.ps1
# Cible unique du job Windows Task Scheduler — lance les 3 pipelines (E11_RDCC,
# E09_PE, E08_OCD) en mode incremental (delta uniquement, cf. shared/state_store.py).
# Chaque pipeline gere son propre succes/echec (email OK/KO, etat) : l'echec de
# l'un n'empeche pas le lancement des suivants.
#
# La FREQUENCE (quotidienne, hebdomadaire, autre) n'est PAS definie ici : elle
# depend uniquement du declencheur configure dans le Task Scheduler. Le script
# traite le delta accumule depuis le dernier run reussi, quel que soit l'ecart
# entre deux executions — les deux rythmes sont donc supportes sans modification.
#
# Configuration Task Scheduler recommandee :
#   Programme      : powershell.exe
#   Arguments      : -NoProfile -ExecutionPolicy Bypass -File "<repo>\scripts\run_pipeline.ps1"
#   Demarrer dans  : <repo>  (repertoire racine du repo)
#   Declencheur    : au choix — quotidien, ou hebdomadaire (ex: lundi 10:00)
#
# Prerequis avant la toute premiere activation du job : lancer une fois chaque
# pipeline en --mode initial pour qu'elle ait un etat de depart (voir README) :
#   python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode initial
#   python -m e09_pe.run_pipeline   --config e09_pe/config/E09_PE.yaml     --mode initial
#   python -m e08_ocd.run_pipeline  --config e08_ocd/config/E08_OCD.yaml   --mode initial

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

$pipelines = @(
    @{ Module = "e11_rdcc.run_pipeline"; Config = "e11_rdcc/config/E11_RDCC.yaml" },
    @{ Module = "e09_pe.run_pipeline";   Config = "e09_pe/config/E09_PE.yaml" },
    @{ Module = "e08_ocd.run_pipeline";  Config = "e08_ocd/config/E08_OCD.yaml" }
)

$exitCode = 0
foreach ($p in $pipelines) {
    Write-Host "=== $($p.Module) ==="
    & $python -m $p.Module --config $p.Config --mode incremental
    if ($LASTEXITCODE -ne 0) {
        Write-Host "=== $($p.Module) : ECHEC (code $LASTEXITCODE) ==="
        $exitCode = $LASTEXITCODE
    }
}

exit $exitCode
