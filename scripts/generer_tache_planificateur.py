"""
scripts/generer_tache_planificateur.py
========================================
Génère un fichier XML de tâche importable directement dans le Planificateur de
tâches Windows (« Importer une tâche… »).

Pourquoi : « Importer une tâche… » attend une DÉFINITION DE TÂCHE au format XML.
Y déposer `run_pipeline.bat` ne crée rien — c'est une confusion fréquente, et
elle est silencieuse (aucune tâche n'apparaît dans la bibliothèque). Ce script
produit le XML correspondant à ce dépôt, avec les bons chemins absolus.

Usage (depuis la racine du repo) :
    python -m scripts.generer_tache_planificateur                      # quotidien 13:00
    python -m scripts.generer_tache_planificateur --heure 10:00
    python -m scripts.generer_tache_planificateur --frequence hebdomadaire --jour lundi
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

_JOURS = {
    "lundi": "Monday", "mardi": "Tuesday", "mercredi": "Wednesday",
    "jeudi": "Thursday", "vendredi": "Friday", "samedi": "Saturday", "dimanche": "Sunday",
}

_MODELE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{description}</Description>
    <URI>\\{nom}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{heure}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleBy{planification}>
{detail_planification}
      </ScheduleBy{planification}>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{utilisateur}</UserId>
      <LogonType>{type_connexion}</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{commande}</Command>
      <WorkingDirectory>{repertoire}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def generer(heure: str, frequence: str, jour: str, sortie: Path,
             hors_session: bool = False) -> Path:
    repo = Path(__file__).resolve().parent.parent
    bat = repo / "run_pipeline.bat"
    if not bat.exists():
        raise FileNotFoundError(f"{bat} introuvable — lancer ce script depuis le dépôt.")

    if frequence == "quotidien":
        planification, detail = "Day", "        <DaysInterval>1</DaysInterval>"
    else:
        jour_en = _JOURS.get(jour.lower())
        if not jour_en:
            raise ValueError(f"Jour inconnu : {jour!r} — attendu l'un de {sorted(_JOURS)}.")
        planification = "Week"
        detail = ("        <DaysOfWeek>\n"
                  f"          <{jour_en} />\n"
                  "        </DaysOfWeek>\n"
                  "        <WeeksInterval>1</WeeksInterval>")

    utilisateur = f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}".lstrip("\\")

    # InteractiveToken : la tâche s'exécute quand la session Windows est ouverte,
    # SANS mot de passe. InteractiveTokenOrPassword autorise l'exécution session
    # fermée, mais exige un compte AVEC mot de passe — sur un compte sans mot de
    # passe, Windows refuse l'enregistrement ("des restrictions de compte
    # d'utilisateur empêchent cet utilisateur de se connecter"), constaté ici.
    type_connexion = "InteractiveTokenOrPassword" if hors_session else "InteractiveToken"

    xml = _MODELE.format(
        description=escape("Nettoyage/normalisation des endpoints BCM (E11_RDCC, E09_PE, E08_OCD) "
                            "— lance les 3 pipelines en mode incremental."),
        nom="BCM - Data Cleaning APIs",
        heure=heure,
        planification=planification,
        detail_planification=detail,
        utilisateur=escape(utilisateur),
        type_connexion=type_connexion,
        commande=escape(str(bat)),
        repertoire=escape(str(repo)),
    )

    # Le Planificateur attend de l'UTF-16 (comme ses propres exports).
    sortie.write_text(xml, encoding="utf-16")
    return sortie


def main() -> int:
    parser = argparse.ArgumentParser(description="Génère la tâche XML pour le Planificateur Windows")
    parser.add_argument("--heure", default="13:00", help="Heure de déclenchement HH:MM (défaut 13:00)")
    parser.add_argument("--frequence", default="quotidien", choices=["quotidien", "hebdomadaire"])
    parser.add_argument("--jour", default="lundi", help="Jour si --frequence hebdomadaire")
    parser.add_argument("--sortie", default="BCM_DataCleaning_Task.xml")
    parser.add_argument("--hors-session", action="store_true",
                         help="Exécution même session fermée — EXIGE un compte Windows avec mot de passe")
    args = parser.parse_args()

    try:
        heure = args.heure.strip()
        if len(heure.split(":")) != 2:
            raise ValueError(f"Heure invalide : {args.heure!r} — format attendu HH:MM (ex: 13:00).")
        chemin = generer(heure, args.frequence, args.jour, Path(args.sortie),
                          hors_session=args.hors_session)
    except Exception as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1

    print(f"Fichier généré : {chemin.resolve()}\n")
    print("Dans le Planificateur de tâches :")
    print("  1. Bibliothèque du Planificateur de tâches -> clic droit -> « Importer une tâche… »")
    print(f"  2. Choisir {chemin.name}")
    print("  3. Clic droit sur la tâche -> « Exécuter » pour vérifier immédiatement\n")
    if args.hors_session:
        print("Mode hors session : nécessite un compte Windows AVEC mot de passe.")
    else:
        print("La tâche s'exécutera tant que la session Windows est ouverte.")
        print("Pour qu'elle tourne session fermée : définir un mot de passe Windows,")
        print("puis relancer ce script avec --hors-session.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
