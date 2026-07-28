@echo off
REM Wrapper appelable directement par Task Scheduler si un .bat est prefere a PowerShell.
REM La logique complete (choix de l'interpreteur venv, mode incremental) vit dans
REM scripts\run_weekly.ps1 — ce fichier ne fait que la deleguer.
setlocal
set REPO_ROOT=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%scripts\run_weekly.ps1"
exit /b %ERRORLEVEL%
