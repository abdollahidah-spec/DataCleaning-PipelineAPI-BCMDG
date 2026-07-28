# Data-Cleaning-PipelineAPI-BCMDG

## Introduction

Ce projet est le successeur, organisé **par API/endpoint**, de
[`DataCleaning-PipelineField-BCMDG`](../DataCleaning-PipelineField-BCMDG) (organisé par champ).
Chaque pipeline traite **tous les champs d'une API en un seul run** et produit un seul jeu de
livrables, au lieu de faire tourner un champ (Devise, NomCorrespondant, ...) indépendamment
sur toutes les APIs qui le contiennent.

> **Portée actuelle : uniquement l'API E11 — RDCC** (ticket BCMDG-172). Ce README ne documente
> que ce qui existe aujourd'hui. Il sera mis à jour au fur et à mesure que d'autres endpoints
> (E07_FS, E08_OCD, ...) seront implémentés — chacun ajoutera son propre package `e0X_xxx/`
> à côté de `e11_rdcc/`, sans modifier ce qui existe déjà (voir *Architecture* ci-dessous).

---

## Architecture

```
DataCleaning-PipelineAPI-BCMDG/
├── shared/            Infrastructure générique, réutilisable par toute future API (connexion DB
│                       lecture seule, état incrémental, email, SharePoint, écriture Excel/CSV,
│                       orchestrateur BaseApiPipeline)
├── e11_rdcc/           Package auto-contenu de l'API E11 — champs, référentiels, config, CLI
│   ├── fields/         Devise, NomCorrespondant (catégoriels) + cohérence numérique (4 règles)
│   ├── referentiel/    Référentiels + caches warm-start, propres à E11
│   ├── config/E11_RDCC.yaml
│   ├── pipeline.py, run_pipeline.py, apply_corrections.py, ad_hoc_extraction.py
│   ├── outputs/        Sorties locales (gitignored)
│   └── state/          État incrémental local (gitignored)
├── scripts/            run_weekly.ps1 (cible Task Scheduler), seed_state.py
├── docs/               ARCHITECTURE.md, FUNCTIONAL.md, CONFIGURATION.md
└── tests/
```

**Principe central** : une future API ne réutilise **que** `shared/` — elle a son propre
`fields/`, ses propres référentiels, sa propre config, indépendants de `e11_rdcc/`. Aucun
couplage entre packages d'API, pour qu'une API ne casse jamais en modifiant une autre. Voir
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (section "Décision H") pour le raisonnement complet.

---

## Champs traités — E11_RDCC

| Champ | Type | Traitement |
|---|---|---|
| `NomCorrespondant` | catégoriel | Référentiel BCM validé → fallback API Claude |
| `Devise` | catégoriel | Référentiel ISO 4217 (règles pures, pas de LLM) |
| Soldes/dates (`SoldeDebutJournee`, `TotalMvtsDebiteursJournee`, `TotalMvtsCrediteurs`, `SoldeFinJournee`, `DateFinJournee`) | cohérence numérique | 4 règles de cohérence (voir [docs/FUNCTIONAL.md](docs/FUNCTIONAL.md)) — pas de "valeur normalisée", un rapport d'anomalies |

La règle NA commune aux champs catégoriels utilise `NumCompte` comme colonne témoin (voir
[docs/FUNCTIONAL.md](docs/FUNCTIONAL.md)).

---

## Installation

**Prérequis :**
- Python 3.10+
- Accès réseau à la base SQL Server BCM (lecture seule uniquement)
- Une clé API Anthropic (`ANTHROPIC_API_KEY`) pour le fallback Claude de `NomCorrespondant`
- (Optionnel) Credentials SharePoint (Microsoft Graph) et SMTP Gmail pour les notifications

```bash
git clone <url> DataCleaning-PipelineAPI-BCMDG
cd DataCleaning-PipelineAPI-BCMDG
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt   # ou requirements.txt seul, sans les tests
copy .env.example .env
# Renseigner .env
```

---

## Utilisation

> Toutes les commandes sont à lancer depuis la racine du repo.

### Run local (fichier CSV/Excel — 100% offline, aucun SharePoint/email/état)

```bash
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --input tests/fixtures/e11_rdcc_sample.csv
```

### Initial Load (historique complet, à lancer une fois avant d'activer le job hebdomadaire)

```bash
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode initial
```

### Incremental Load (delta uniquement — mode utilisé par le job hebdomadaire)

```bash
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode incremental
```

### Dry-run (charge/traite normalement mais n'envoie ni SharePoint ni email)

```bash
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode initial --dry-run
```

### Corrections manuelles (apply_corrections.py)

Le classeur `E11_RDCC_classification.xlsx` contient un onglet **Instructions**, pré-rempli
automatiquement avec les outliers détectés (`Champ`, `Input`, `Label_Attendu` vide). Une équipe
métier renseigne `Label_Attendu` (nom légal correct, ou `OUTLIER`), puis :

```bash
python -m e11_rdcc.apply_corrections --file "e11_rdcc/outputs/E11_RDCC_classification.xlsx"
```

met à jour le cache warm-start du champ concerné (déduit de la colonne `Champ`) — la correction
prime dès le run suivant, sans jamais retoucher le fichier Excel source.

### Extraction ad hoc (requête SQL personnalisée)

Outil indépendant du run automatisé — un analyste fournit sa propre requête, table, ou fichier
local ; le nettoyage normal s'applique à tout champ configuré dont la colonne est présente dans
le résultat (les autres sont ignorés sans erreur). Produit une extraction locale (colonnes de la
requête + leurs versions nettoyées insérées juste après), sans toucher SharePoint/email/état :

```bash
python -m e11_rdcc.ad_hoc_extraction --config e11_rdcc/config/E11_RDCC.yaml \
    --query "SELECT * FROM [DATAWAREHOUSE_SA_PROD].[dbo].[E11EtatBcmReleveDesComptesCorrespondants] WHERE RefBanque = 'BANK01'"

# ou depuis un fichier .sql, ou --table, ou --input (fichier local)
python -m e11_rdcc.ad_hoc_extraction --config e11_rdcc/config/E11_RDCC.yaml --sql-file mon_extrait.sql --output mon_export.csv
```

Un nouveau fichier horodaté est créé à chaque exécution (sauf si `--output` fixe un nom précis,
auquel cas ce fichier est écrasé à chaque relance). Seules les requêtes de lecture (`SELECT` /
`WITH ... SELECT`) sont acceptées — garde-fou contre une requête destructrice collée par erreur.

### Notifications email

Deux adresses suffisent — une qui envoie (`SMTP_USER`), une (ou plusieurs) qui reçoivent
(`EMAIL_TO`). La seule contrainte technique vient de Gmail : il faut un **mot de passe
d'application** dédié (pas le mot de passe du compte) :

1. Sur le compte Gmail qui enverra les emails, avec la validation en 2 étapes déjà active :
   va directement sur `myaccount.google.com/apppasswords`.
2. Si le lien direct ne fonctionne pas : `myaccount.google.com` → **Security** → sous
   "Signing in to Google" → **2-Step Verification** → tout en bas de cette page →
   **App passwords**.
3. Crée-en un (nom libre), copie le code de 16 caractères → `SMTP_APP_PASSWORD` dans `.env`.
4. Renseigne aussi `SMTP_USER` et `EMAIL_TO`.

Si l'option est introuvable même avec le lien direct : c'est probablement un compte Google
Workspace pro (pas un `@gmail.com` perso) où l'administrateur a désactivé la fonctionnalité —
utiliser un compte Gmail personnel/dédié à la place. Tant que ces variables sont vides, les
notifications sont simplement ignorées (aucune erreur, aucun blocage du run).

### Ordonnancement en production (Windows Task Scheduler)

Voir [scripts/run_weekly.ps1](scripts/run_weekly.ps1) — job hebdomadaire, lundi 10h, mode
incrémental. **Avant la toute première activation du job**, lancer manuellement un
`--mode initial` pour amorcer l'état incrémental (voir *État incrémental* ci-dessous).

---

## Outputs

Chaque run automatisé produit **un seul classeur** `E11_RDCC_classification.xlsx`
(voir [docs/FUNCTIONAL.md](docs/FUNCTIONAL.md) pour le détail) :

| Feuille | Contenu |
|---|---|
| `NomCorrespondant`, `Devise` | Classification : valeur brute → valeur normalisée (OUTLIER inclus). **Cumulative** — construite depuis le référentiel + le cache warm-start, pas depuis les lignes du run, donc jamais amputée des labels déjà connus en mode incrémental |
| `Anomalies_Numeriques` | Uniquement les lignes en anomalie (les 4 règles de cohérence) — NumCompte, RefBanque, colonnes numériques/date concernées, règle violée, détail |
| `Instructions` | Pré-remplie avec les outliers détectés (`Champ`, `Input`, `Label_Attendu`) pour la boucle de correction |

Pas d'extraction ligne par ligne dans ce classeur (sur demande métier — voir *Extraction ad hoc*
pour un export complet à la demande). Stocké en local (`e11_rdcc/outputs/`, ou `{OUTPUT_BASE}/...`
si renseigné) + SharePoint si configuré (chemin stable, écrasé à chaque run) — source Power BI.
Le même fichier est joint à l'email de notification.

Le corps de l'email contient : les stats de CE run (lignes traitées, taux conformité/
normalisation/outliers — globaux tous champs confondus, avec détail par champ/règle), puis un
saut de deux lignes, puis les **stats globales** cumulées depuis le tout premier run (Initial
Load) : nombre de runs, lignes traitées au total, outliers/anomalies détectés au total. Le
rapport de qualité est aussi disponible dans les logs structurés (`logs/{api_id}_{ts}.log`).

---

## État incrémental (fichier JSON local, pas une table SQL)

**La base source E11 reste strictement en lecture seule** (aucune écriture/DDL autorisée côté
métier). L'état incrémental (dernier `dtCr` traité, compteurs cumulatifs pour l'email) est donc
simulé localement, dans `e11_rdcc/state/{api_id}_run_state.json` (ou `{STATE_DIR}/...` si
renseigné dans `.env` — même logique qu'`OUTPUT_BASE`). Écriture atomique (fichier temporaire +
remplacement) pour ne jamais corrompre l'état si le process est interrompu en cours d'écriture.

Ce fichier est propre à la machine qui exécute le pipeline — s'il change de serveur, réamorcer
l'état avec `scripts/seed_state.py` (voir *Ordonnancement* ci-dessus) plutôt que de le copier.

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Tous les tests sont 100% locaux (fichiers temporaires, référentiels/caches isolés) — aucun
n'accède à la vraie base de données ni à un vrai service externe.
