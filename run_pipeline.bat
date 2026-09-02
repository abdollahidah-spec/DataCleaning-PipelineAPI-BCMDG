@echo off
REM Wrapper appelable directement par Task Scheduler si un .bat est prefere a PowerShell.
REM La logique complete (choix de l'interpreteur venv, lancement des 3 pipelines en
REM mode incremental) vit dans scripts\run_pipeline.ps1 — ce fichier ne fait que la deleguer.
REM
REM La frequence (quotidienne, hebdomadaire...) depend uniquement du declencheur
REM configure dans le Task Scheduler, pas de ce script : les deux sont supportes.
setlocal
set REPO_ROOT=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%scripts\run_pipeline.ps1"
exit /b %ERRORLEVEL%
