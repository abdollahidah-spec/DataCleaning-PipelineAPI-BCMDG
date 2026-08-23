# Data-Cleaning-PipelineAPI-BCMDG

## Introduction

Ce projet est le successeur, organisé **par API/endpoint**, de
[`DataCleaning-PipelineField-BCMDG`](../DataCleaning-PipelineField-BCMDG) (organisé par champ).
Chaque pipeline traite **tous les champs d'une API en un seul run** et produit un seul jeu de
livrables, au lieu de faire tourner un champ (Devise, NomCorrespondant, ...) indépendamment
sur toutes les APIs qui le contiennent.

> **Portée actuelle : E11 — RDCC** (ticket BCMDG-172), **E09 — PE** (ticket BCMDG-223) et
> **E08 — OCD**, 6 champs (pas de ticket Jira dédié, porté directement depuis l'ancien repo
> par-champ). Ce README documente principalement E11 (le plus complet) ; E09 et E08 suivent
> exactement la même approche (voir *Champs traités* ci-dessous et [e09_pe/](e09_pe/) /
> [e08_ocd/](e08_ocd/)). E08 combine des champs déterministe+Claude (Devise, NomCorrespondant,
> Produits), du matching contre la base fiscale DGI (NomDonneurOrdre), du Claude+recherche web
> (Bénéficiaire) et un référentiel pycountry/babel/geonamescache (Pays) — voir *Champs traités —
> E08_OCD* pour le détail de chacun. D'autres endpoints (E07_FS, E10_FE...) pourront être ajoutés
> de la même façon — chacun son propre package `e0X_xxx/`, sans modifier ce qui existe déjà (voir
> *Architecture* ci-dessous).

---

## Architecture

```
DataCleaning-PipelineAPI-BCMDG/
├── shared/            Infrastructure générique, réutilisable par toute future API (connexion DB
│                       lecture seule, état incrémental, email, SharePoint, écriture Excel/CSV,
│                       rapports PDF, validation de config, orchestrateur BaseApiPipeline)
├── e11_rdcc/           Package auto-contenu de l'API E11 — champs, référentiels, config, CLI
│   ├── fields/         Devise, NomCorrespondant (catégoriels) + cohérence numérique (4 règles)
│   ├── referentiel/    Référentiels + caches warm-start, propres à E11
│   ├── config/E11_RDCC.yaml
│   ├── pipeline.py, run_pipeline.py, apply_corrections.py, ad_hoc_extraction.py, reports.py
│   └── outputs/        Sorties locales (gitignored)
├── e09_pe/             Package auto-contenu de l'API E09 — même structure qu'e11_rdcc/, en
│   │                    plus simple (1 champ catégoriel + 2 règles de validation)
│   ├── fields/         Devise (catégoriel) + Echeances (MontantEcheance/DateEcheance, 2 règles)
│   ├── referentiel/    Référentiels + caches warm-start, propres à E09
│   ├── config/E09_PE.yaml
│   ├── pipeline.py, run_pipeline.py, apply_corrections.py, reports.py
│   └── outputs/        Sorties locales (gitignored)
├── e08_ocd/            Package auto-contenu de l'API E08 — 6 champs catégoriels
│   ├── fields/         Devise, NomCorrespondant (portés) + Produits (nouveau) — déterministe/
│   │                    référentiel + fallback Claude ; NomDonneurOrdre (matching base fiscale
│   │                    DGI + rapidfuzz + arbitrage Claude) + Beneficiaire (mêmes helpers,
│   │                    fallback Claude + recherche web) partagent _entity_matching.py ; Pays
│   │                    (pycountry/babel/geonamescache + fallback Claude, ex-Ollama)
│   ├── referentiel/    Référentiels + caches warm-start, propres à E08 (dont des caches
│   │                    NomCorrespondant/NomDonneurOrdre/Pays repris tel quels de l'ancien repo)
│   ├── config/E08_OCD.yaml
│   ├── pipeline.py, run_pipeline.py, apply_corrections.py, reports.py
│   └── outputs/        Sorties locales (gitignored)
├── req/                Fichiers de référence externes (base fiscale DGI, entreprises publiques)
│   │                    — gitignored, chemins pilotés par .env (DGI_BASE_PATH/PUBLIC_ENT_PATH)
├── state/              État incrémental local, un fichier JSON par API (gitignored)
├── scripts/            run_weekly.ps1 (cible Task Scheduler), seed_state.py
├── docs/               ARCHITECTURE.md, FUNCTIONAL.md, CONFIGURATION.md
└── tests/
```

**Principe central** : une future API ne réutilise **que** `shared/` — elle a son propre
`fields/`, ses propres référentiels, sa propre config, indépendants des autres packages d'API.
Aucun couplage entre packages d'API, pour qu'une API ne casse jamais en modifiant une autre. Voir
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (section "Décision H") pour le raisonnement complet
— `e09_pe/` est la première application concrète de ce principe : construit en copiant la forme
d'`e11_rdcc/` puis en divergeant librement (pas de règle de cohérence J/J+1, pas de gabarit "sans
activité", témoin NA simple au lieu de la colonne globale — rien de tout ça n'était demandé pour
E09).

---

## Champs traités — E11_RDCC

| Champ | Type | Traitement |
|---|---|---|
| `NomCorrespondant` | catégoriel | Référentiel BCM validé → fallback API Claude |
| `Devise` | catégoriel | Référentiel ISO 4217 (règles pures, pas de LLM) |
| Soldes/dates (`SoldeDebutJournee`, `TotalMvtsDebiteursJournee`, `TotalMvtsCrediteurs`, `SoldeFinJournee`, `DateFinJournee`) | cohérence numérique | 4 règles de cohérence (voir [docs/FUNCTIONAL.md](docs/FUNCTIONAL.md)) — pas de "valeur normalisée", un rapport d'anomalies |

La règle NA commune aux champs catégoriels utilise `NumCompte` comme colonne témoin (voir
[docs/FUNCTIONAL.md](docs/FUNCTIONAL.md)).

## Champs traités — E09_PE

| Champ | Type | Traitement |
|---|---|---|
| `Devise` | catégoriel | Référentiel ISO 4217 (identique à E11 — mêmes règles pures, pas de LLM) |
| `MontantEcheance`, `DateEcheance` | validation numérique/date | 2 règles indépendantes, ligne à ligne (pas de cohérence inter-lignes) : `AMOUNT_POSITIVE` (montant > 0) et `DATE_VALIDITY` (date d'échéance strictement postérieure à `dtCr`) — voir [e09_pe/fields/echeances.py](e09_pe/fields/echeances.py) |

`NumCredoc` (référence du Crédit Documentaire) n'est pas normalisé — colonne témoin de la règle
NA de Devise et identifiant conservé dans `Anomalies_Echeances`, comme `NumCompte` pour E11.

```bash
python -m e09_pe.run_pipeline --config e09_pe/config/E09_PE.yaml --input tests/fixtures/e09_pe_sample.csv
```

Toutes les commandes de la section *Utilisation* ci-dessous existent aussi pour E09
(`python -m e09_pe.run_pipeline`, `python -m e09_pe.apply_corrections`, mêmes options).

## Champs traités — E08_OCD

| Champ | Type | Traitement |
|---|---|---|
| `Devise` | catégoriel | Référentiel ISO 4217 (identique à E11/E09) |
| `NomCorrespondant` | catégoriel | Référentiel BCM validé → fallback API Claude (identique à E11 ; cache warm-start repris de l'ancien repo, déjà "chaud") |
| `Produits` | catégoriel | Référentiel construit depuis `req/DataCleaning_E08 OCD_Produits.xlsx` (catégories + alias déjà connus + bruit connu) → fallback API Claude sur liste FERMÉE de labels valides (`shared/claude_client.py::call_claude_match_batch` — Claude choisit dans la liste ou répond OUTLIER, jamais un libellé inventé) — voir [e08_ocd/fields/produits.py](e08_ocd/fields/produits.py) |
| `NomDonneurOrdre` | catégoriel | NIF exact (base fiscale DGI, ~52k raisons sociales) → entreprise publique connue → classification locale (particulier/ETS) → matching flou DGI (rapidfuzz, exact/fort/arbitrage Claude/aucun match) — voir [e08_ocd/fields/nomdonneurordre.py](e08_ocd/fields/nomdonneurordre.py) |
| `Beneficiaire` | catégoriel | Bénéficiaire ÉTRANGER (import) : mêmes helpers de nettoyage/classification que NomDonneurOrdre (`e08_ocd/fields/_entity_matching.py`) mais pas de base DGI pertinente → fallback Claude + recherche web réelle — voir [e08_ocd/fields/beneficiaire.py](e08_ocd/fields/beneficiaire.py) |
| `Pays` | catégoriel | Référentiel pycountry/babel/geonamescache + alias manuels + mots-clés d'adresse → fallback Claude sur liste fermée des codes ISO-2 (remplace Ollama/qwen2.5 de l'ancien repo — décision confirmée, pas d'infra locale à héberger) — voir [e08_ocd/fields/pays.py](e08_ocd/fields/pays.py) |

`NumCredoc` (référence du Crédit Documentaire) n'est pas normalisé — colonne témoin de la règle
NA de chaque champ catégoriel (chacun indépendamment, comme dans l'ancien repo — pas de correctif
"NA globale" comme pour E11, aucun gabarit "sans activité" multi-champs documenté pour E08).

`NomDonneurOrdre` et `Beneficiaire` nécessitent `DGI_BASE_PATH`/`PUBLIC_ENT_PATH` dans `.env`
(fichiers externes non versionnés, voir `.env.example`) et la dépendance `rapidfuzz`. `Pays`
nécessite `pycountry`/`babel`/`geonamescache`. `Beneficiaire` utilise l'outil serveur Claude
`web_search` (facturé à l'usage, `llm.web_search_max_uses` dans le YAML, défaut 3/valeur).

```bash
python -m e08_ocd.run_pipeline --config e08_ocd/config/E08_OCD.yaml --input tests/fixtures/e08_ocd_sample.csv
```

Toutes les commandes de la section *Utilisation* ci-dessous existent aussi pour E08
(`python -m e08_ocd.run_pipeline`, `python -m e08_ocd.apply_corrections`, mêmes options).

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

Chaque run automatisé produit **un classeur Excel** `E11_RDCC_classification.xlsx`
(voir [docs/FUNCTIONAL.md](docs/FUNCTIONAL.md) pour le détail) :

| Feuille | Contenu |
|---|---|
| `NomCorrespondant`, `Devise` | Classification : valeur brute → valeur normalisée (OUTLIER inclus). **Cumulative** — construite depuis le référentiel + le cache warm-start, pas depuis les lignes du run, donc jamais amputée des labels déjà connus en mode incrémental |
| `Anomalies_Numeriques` | Uniquement les lignes en anomalie (les 4 règles de cohérence) — NumCompte, RefBanque, colonnes numériques/date concernées, `dtCr`, règle violée, détail |
| `Instructions` | Pré-remplie avec les outliers détectés (`Champ`, `Input`, `Label_Attendu`) pour la boucle de correction |

Pas d'extraction ligne par ligne dans ce classeur (sur demande métier — voir *Extraction ad hoc*
pour un export complet à la demande). Stocké en local (`e11_rdcc/outputs/`, ou `{OUTPUT_BASE}/...`
si renseigné) + SharePoint si configuré (chemin stable, écrasé à chaque run) — source Power BI.

En plus du classeur, chaque run génère **un rapport PDF unique** (Markdown → PDF, pure Python,
`shared/pdf_report.py` + `shared/report_templates.py` + le module `reports.py` de l'API) :
`Rapport_Qualite_Outliers_{api_id}_{yyyymmdd}.pdf`, deux sections dans le même fichier (saut de
page entre les deux) — statistiques générales + indicateurs de performance de ce run, puis
répartition des outliers par champ traité et par RefBanque. Les deux fichiers (classeur + PDF)
sont joints à l'email de notification OK.

Le corps de l'email OK suit un gabarit texte (pas HTML) validé par le Business Analyst : sujet
`Pipeline {libellé endpoint} : Rapport de qualité et rapport des outliers – {date}` (ex: "E11 –
RDCC", "E09 – PE" — voir `email.display_name` dans chaque YAML), puis les indicateurs delta de CE
run (lignes traitées, nouvelles valeurs distinctes/normalisées/outliers, taux de conformité, temps
d'exécution), puis un rappel du PDF joint pour le détail complet. Le corps de l'email KO ne
contient **jamais** le texte brut de l'exception ni de traceback — uniquement une catégorie courte
et non technique (`shared/errors.py::PipelineError.category`), le détail technique restant dans
les logs. Le rapport de qualité est aussi disponible dans les logs structurés (`logs/{api_id}_{ts}.log`).

---

## État incrémental (fichier JSON local, pas une table SQL)

**Les bases source restent strictement en lecture seule** (aucune écriture/DDL autorisée côté
métier). L'état incrémental (dernier `dtCr` traité, compteurs cumulatifs d'historique) est donc
simulé localement, dans `state/{api_id}_run_state.json` (un fichier par API — ex:
`state/E11_RDCC_run_state.json`, `state/E09_PE_run_state.json` — ou sous `{STATE_DIR}/...` si
renseigné dans `.env`, même logique qu'`OUTPUT_BASE`). Écriture atomique (fichier temporaire +
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
