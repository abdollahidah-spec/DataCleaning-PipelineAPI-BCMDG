# Data-Cleaning-PipelineAPI-BCMDG

Nettoyage et normalisation des données déclarées par les banques primaires, organisés **par
endpoint**.

## Objet

Les banques primaires déclarent quotidiennement leurs opérations à la BCM à travers un ensemble
d'APIs de déclaration, une par type d'état réglementaire. Les champs de ces déclarations sont
saisis librement : une même devise, un même pays ou une même raison sociale y apparaissent sous
des dizaines d'orthographes.

Ce dépôt traite **tous les champs d'un endpoint en une seule exécution** et produit, pour chaque
champ, une **mapping table** : la liste des valeurs uniques rencontrées, chacune associée à sa
valeur propre. Les valeurs qu'aucune règle ne permet de rattacher sont isolées comme `OUTLIER` et
soumises à validation métier ; une fois validées, elles rejoignent la mémoire du pipeline et ne
sont plus à retraiter.

Cinq endpoints sont traités, chacun dans son propre package :

| Endpoint | Objet | Table source | Contenu |
|---|---|---|---|
| [E07_FS](e07_fs/README.md) | flux sortants | `E7EtatBcmFluxSortants` | 7 champs catégoriels, contrôle des transactions |
| [E08_OCD](e08_ocd/README.md) | ouvertures de crédits documentaires | `E8EtatBcmOuvertureCreditDocumentaires` | 6 champs catégoriels |
| [E09_PE](e09_pe/README.md) | prévisions d'échéances | `E9EtatBcmPrevisionEcheances` | 1 champ catégoriel, contrôle des échéances |
| [E10_FE](e10_fe/README.md) | flux entrants | `E10EtatBcmFluxEntrants` | 7 champs catégoriels, contrôle des transactions |
| [E11_RDCC](e11_rdcc/README.md) | relevés des comptes correspondants | `E11EtatBcmReleveDesComptesCorrespondants` | 2 champs catégoriels, cohérence des soldes |

Le dépôt [`DataCleaning-PipelineField-BCMDG`](../DataCleaning-PipelineField-BCMDG) traite les mêmes
données selon l'autre axe : un champ à la fois, sur toutes les APIs où il existe. Les deux chaînes
appliquent les mêmes règles métier ; leur comparaison figure dans
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Les tables source sont lues en **lecture seule stricte** : aucune écriture, aucun DDL.

---

## Architecture

```
DataCleaning-PipelineAPI-BCMDG/
├── shared/             Socle générique, sans logique métier de champ : connexion en lecture
│                        seule, état incrémental, règles transverses, client de modèle,
│                        écriture Excel/CSV, rapports PDF, notification, dépôt distant,
│                        validation de configuration, orchestrateur BaseApiPipeline
├── e07_fs/             Package autonome de l'endpoint E07 — flux sortants
│   ├── fields/         TypeSwift, ModeReglement, Devise, NomDonneurOrdre, Beneficiaire,
│   │                    NatureEconomique, Pays, et transactions.py (montant, taux, date,
│   │                    conformité des messages sans activité)
│   ├── referentiel/    Référentiels métier et mémoires de valeurs validées, propres à E07
│   ├── config/E07_FS.yaml
│   ├── pipeline.py, run_pipeline.py, apply_corrections.py, ad_hoc_extraction.py, reports.py
│   └── outputs/        Sorties locales (non versionnées)
├── e08_ocd/            Package autonome de l'endpoint E08 — même structure
│   ├── fields/         Devise, NomCorrespondant, Produits, Pays, et NomDonneurOrdre /
│   │                    Beneficiaire qui partagent _entity_matching.py (rapprochement
│   │                    contre la base fiscale)
│   └── ...
├── e09_pe/             Package autonome de l'endpoint E09 — le plus simple
│   ├── fields/         Devise, et echeances.py (montant, date d'échéance)
│   └── ...
├── e10_fe/             Package autonome de l'endpoint E10 — structure d'E07, entités
│   ├── fields/         inversées : donneur d'ordre étranger, bénéficiaire local
│   └── ...
├── e11_rdcc/           Package autonome de l'endpoint E11
│   ├── fields/         Devise, NomCorrespondant, et numeric_coherence.py (4 règles,
│   │                    dont un chaînage J / J+1)
│   ├── global_na.py    Témoin de non-activité global, propre à E11
│   └── ...
├── req/                Fichiers de référence externes : base fiscale, entreprises publiques
│                        — non versionnés, chemins pilotés par .env
├── state/              État incrémental local, un fichier JSON par endpoint (non versionné)
├── scripts/            run_pipeline.ps1 (cible de l'ordonnanceur), seed_state.py
└── docs/               ARCHITECTURE_PROJET.md, ARCHITECTURE.md, FUNCTIONAL.md, CONFIGURATION.md
```

**Principe central** : un package d'endpoint ne réutilise **que** `shared/`. Il a son propre
`fields/`, ses propres référentiels et sa propre configuration, indépendants des autres packages.
Ajouter un endpoint ne modifie donc aucun endpoint existant, et un endpoint peut porter une règle
particulière sans négociation avec les autres. La contrepartie — les champs communs à plusieurs
endpoints sont des copies — et la façon dont elle est surveillée sont décrites dans
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Documentation de référence :**

| Document | Contenu |
|---|---|
| [docs/ARCHITECTURE_PROJET.md](docs/ARCHITECTURE_PROJET.md) | Nature du projet, périmètre, mapping tables, les deux chaînes de traitement |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Structure et organisation du code des deux chaînes, contrats, points d'extension |
| [docs/FUNCTIONAL.md](docs/FUNCTIONAL.md) | Règles métier : les cinq étapes de résolution, la cascade de chaque champ, les contrôles |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Variables d'environnement et clés de configuration |
| `{endpoint}/README.md` | Champs, règles, livrables, réglages et coûts de l'endpoint |

---

## Champs traités

Le détail de chaque cascade figure dans [docs/FUNCTIONAL.md](docs/FUNCTIONAL.md) et dans le README
de chaque package.

### E07_FS — flux sortants

| Champ | Type | Traitement |
|---|---|---|
| `TypeSwfit` | catégoriel | Codes de messages valides du flux sortant ; numéro seul complété en code de message ; bruit et montants écartés |
| `ModeReglement` | catégoriel | Modalités validées et leurs variantes connues |
| `Devise` | catégoriel | Référentiel ISO 4217, cascade entièrement déterministe |
| `NomDonneurOrdre` | catégoriel | Entité locale : identifiant fiscal exact, entreprises publiques, classification locale, rapprochement sur la base fiscale, arbitrage entre candidats proches |
| `Beneficiaire` | catégoriel | Entité étrangère : référentiel, classification locale, puis identification par recherche web |
| `NatureEconomique` | catégoriel | Référentiel du flux sortant (14 catégories, 78 labels), mapping direct, pré-filtre des outliers évidents, puis choix dans la liste fermée des labels |
| `Pays` | catégoriel | Référentiel construit en code, mots-clés d'adresse, rapprochement approché, puis résolution assistée. Règle `NoAs` spécifique |
| `MontantTransaction`, `TauxDeChange`, `DateTransaction` | contrôle | Montant et taux positifs ou nuls, date antérieure à la date de création, conformité du gabarit des messages sans activité |

`ReferenceTransaction` n'est pas normalisée : c'est le témoin de la règle NA de chaque champ
catégoriel, et le discriminant des messages sans activité. `SourceDevise` et `Produit` ne sont pas
normalisés, mais sont contrôlés par le gabarit des messages sans activité. Le champ « banque »
correspond à `RefBanque`.

Historique borné aux lignes créées depuis le 01/01/2024 (`load.initial_since`), comme E09 et E10.

### E08_OCD — ouvertures de crédits documentaires

| Champ | Type | Traitement |
|---|---|---|
| `Devise` | catégoriel | Référentiel ISO 4217, cascade entièrement déterministe |
| `NomCorrespondant` | catégoriel | Référentiel des banques validées, puis identification d'une banque disposant d'un code SWIFT/BIC publié |
| `Produits` | catégoriel | Référentiel construit depuis le fichier métier, puis choix dans la liste fermée des libellés valides |
| `NomDonneurOrdre` | catégoriel | Entité locale : rapprochement sur la base fiscale, environ 52 000 raisons sociales |
| `Beneficiaire` | catégoriel | Entité étrangère : identification par recherche web |
| `Pays` | catégoriel | Référentiel construit en code, mots-clés d'adresse, rapprochement approché, puis résolution assistée |

`NumCredoc` n'est pas normalisé : c'est le témoin de la règle NA de chaque champ catégoriel,
appliqué indépendamment champ par champ.

`NomDonneurOrdre` et `Beneficiaire` exigent `DGI_BASE_PATH` et `PUBLIC_ENT_PATH` dans `.env`
(fichiers externes non versionnés). `Beneficiaire` recourt à la recherche web, facturée à l'usage
et plafonnée par `llm.web_search_max_uses`.

### E09_PE — prévisions d'échéances

| Champ | Type | Traitement |
|---|---|---|
| `Devise` | catégoriel | Référentiel ISO 4217, identique aux autres endpoints |
| `MontantEcheance`, `DateEcheance` | contrôle | Deux règles indépendantes, ligne à ligne : montant strictement positif, et date d'échéance strictement postérieure à la date de création |

`NumCredoc` n'est pas normalisé : témoin de la règle NA, et identifiant conservé dans la feuille
d'anomalies.

### E10_FE — flux entrants

Même approche qu'E07, les rôles des entités s'inversant sur un flux entrant :

| Champ | Type | Traitement |
|---|---|---|
| `TypeSwfit` | catégoriel | Codes de messages valides du flux entrant |
| `ModeReglement`, `Devise`, `Pays` | catégoriel | Identiques à E07 |
| `NomDonneurOrdre` | catégoriel | Émetteur **étranger** : référentiel, classification locale, puis identification par recherche web |
| `Beneficiaire` | catégoriel | Bénéficiaire **local** : identifiant fiscal exact, entreprises publiques, rapprochement sur la base fiscale, arbitrage entre candidats proches |
| `NatureEconomique` | catégoriel | Référentiel du flux entrant (78 labels), mapping direct, puis liste fermée |
| `MontantTransaction`, `TauxDeChange`, `DateTransaction` | contrôle | Mêmes quatre règles qu'E07 |

La table E10 ne porte pas de colonne `SourceDevise` : elle est absente du gabarit des messages sans
activité.

### E11_RDCC — relevés des comptes correspondants

| Champ | Type | Traitement |
|---|---|---|
| `NomCorrespondant` | catégoriel | Référentiel des banques validées, puis identification d'une banque disposant d'un code SWIFT/BIC publié |
| `Devise` | catégoriel | Référentiel ISO 4217, cascade entièrement déterministe |
| Soldes et dates de relevé | cohérence numérique | 4 règles, dont un chaînage J / J+1 entre lignes. Pas de valeur normalisée : un rapport d'anomalies |

La règle NA des champs catégoriels s'appuie ici sur un **témoin global** : une colonne précalculée
qui ne vaut `NA` que si tout le gabarit de non-activité est respecté à la fois — nom du
correspondant, devise et numéro de compte tous à `NA`, et les quatre montants à zéro.

---

## Installation

**Prérequis :**

- Python 3.10 ou plus récent
- Accès réseau à la base de collecte, en lecture seule
- Une clé d'API Anthropic (`ANTHROPIC_API_KEY`) pour la dernière étape de résolution des champs
  qui la prévoient
- Facultatif : accès au dépôt distant (Microsoft Graph) et compte SMTP pour les notifications

```bash
git clone <url> DataCleaning-PipelineAPI-BCMDG
cd DataCleaning-PipelineAPI-BCMDG
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Renseigner ensuite `.env` : voir [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

---

## Utilisation

Toutes les commandes se lancent depuis la racine du dépôt. Les exemples portent sur E11 ;
remplacer `e11_rdcc` et `E11_RDCC` par l'endpoint visé — `e07_fs`, `e08_ocd`, `e09_pe`, `e10_fe`
ou `e11_rdcc`. Les options sont identiques pour les cinq.

### Premier chargement

À lancer une fois par endpoint, avant d'activer l'ordonnancement : il amorce l'état incrémental.

```bash
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode initial
```

### Chargement du delta

Mode utilisé par l'ordonnanceur : seules les lignes créées depuis la dernière exécution réussie
sont traitées.

```bash
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode incremental
```

### Exécution sur un fichier local

Entièrement hors ligne : ni dépôt distant, ni notification, ni écriture d'état.

```bash
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --input data/extrait.csv
```

### Exécution à blanc

Charge et traite normalement, mais ne dépose rien et ne notifie personne.

```bash
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode initial --dry-run
```

### Corrections métier

Les valeurs à valider sont les lignes `OUTLIER` des feuilles de classification du classeur, également
détaillées par champ dans le rapport PDF. L'équipe métier prépare un classeur comportant un onglet
**Instructions** à trois colonnes — `Champ`, `Input` (la valeur brute), `Label_Attendu` (la valeur
correcte, ou `OUTLIER`) — puis :

```bash
python -m e11_rdcc.apply_corrections --file "corrections_a_appliquer.xlsx"
```

La correction rejoint la mémoire des valeurs validées du champ désigné par la colonne `Champ`, et
prime dès l'exécution suivante, sans jamais retoucher le classeur source.

**Traçabilité** : chaque correction appliquée est journalisée dans l'historique propre à
l'endpoint (`{endpoint}/referentiel/corrections_history_{api_id}.json`). Cet historique est
reproduit en **lecture seule** dans l'onglet `Instructions` du classeur produit à l'exécution
suivante : vide à la première exécution, puis enrichi au fil des validations. Rejouer deux fois la
même correction ne crée pas de doublon ; corriger différemment une valeur déjà corrigée conserve
les deux entrées.

### Extraction sur requête libre

Outil indépendant de l'exécution automatisée : un analyste fournit sa propre requête, une table,
ou un fichier local. Le nettoyage normal s'applique à tout champ configuré dont la colonne source
est présente dans le résultat, les autres étant ignorés sans erreur. Produit une extraction locale
— colonnes de la requête, et leurs versions nettoyées insérées juste après — sans toucher au dépôt
distant, à la notification ni à l'état.

```bash
python -m e11_rdcc.ad_hoc_extraction --config e11_rdcc/config/E11_RDCC.yaml \
    --query "SELECT * FROM [DATAWAREHOUSE_SA_PROD].[dbo].[E11EtatBcmReleveDesComptesCorrespondants] WHERE RefBanque = 'BANK01'"

# depuis un fichier .sql, ou --table, ou --input pour un fichier local
python -m e11_rdcc.ad_hoc_extraction --config e11_rdcc/config/E11_RDCC.yaml \
    --sql-file mon_extrait.sql --output mon_export.csv
```

Disponible pour E07, E10 et E11. Un fichier horodaté est créé à chaque exécution, sauf si
`--output` fixe un nom précis, auquel cas ce fichier est écrasé. Seules les requêtes de lecture
sont acceptées — garde-fou contre une requête destructrice collée par erreur.

Les réglages de performance d'un chargement — lecture SQL accélérée, reprise sur coupure réseau,
parallélisme et plafond des appels au modèle — sont documentés dans
[docs/CONFIGURATION.md](docs/CONFIGURATION.md#performance-dun-chargement-réglages-communs).

### Notifications

Deux adresses suffisent : une qui envoie (`SMTP_USER`), une ou plusieurs qui reçoivent
(`EMAIL_TO`). La seule contrainte technique vient de Gmail, qui exige un **mot de passe
d'application** dédié, et non le mot de passe du compte :

1. Sur le compte qui enverra les messages, la validation en deux étapes étant active, ouvrir
   `myaccount.google.com/apppasswords`.
2. Si le lien direct ne fonctionne pas : `myaccount.google.com` → **Security** → **2-Step
   Verification** → en bas de page → **App passwords**.
3. En créer un, puis reporter le code de 16 caractères dans `SMTP_APP_PASSWORD`.
4. Renseigner également `SMTP_USER` et `EMAIL_TO`.

Si l'option reste introuvable, il s'agit probablement d'un compte Google Workspace dont
l'administrateur a désactivé la fonctionnalité : utiliser un compte dédié. Tant que ces variables
sont vides, les notifications sont ignorées, sans erreur ni blocage.

### Ordonnancement

Voir [scripts/run_pipeline.ps1](scripts/run_pipeline.ps1), ou son enveloppe
[run_pipeline.bat](run_pipeline.bat) : il lance les **cinq** pipelines en mode incrémental. La
fréquence est définie par le déclencheur de l'ordonnanceur, pas par le script — quotidienne ou
hebdomadaire, les deux fonctionnent sans modification, chaque exécution traitant le delta accumulé
depuis la dernière exécution réussie. **Avant la première activation**, lancer manuellement un
`--mode initial` par endpoint pour amorcer l'état incrémental (voir *État incrémental*).

---

## Livrables

Chaque exécution automatisée produit **un classeur Excel** `{API_ID}_classification.xlsx` :

| Feuille | Contenu |
|---|---|
| Une par champ catégoriel | La mapping table du champ : valeur brute, valeur normalisée, méthode de résolution, `OUTLIER` inclus. **Cumulative** — construite depuis le référentiel et la mémoire des valeurs validées, et non depuis les seules lignes de l'exécution : elle n'est donc jamais amputée des valeurs déjà connues en mode incrémental |
| `Anomalies_*` | Une feuille par famille de contrôle, limitée aux lignes en anomalie : identifiants, colonnes concernées, date de création, règle violée, détail. Au-delà d'un seuil de lignes, le détail intégral part dans un CSV à côté du classeur |
| `Instructions` | **Historique en lecture seule** des corrections métier déjà appliquées (`Champ`, `Input`, `Label_Attendu`) — vide à la première exécution |

Le classeur ne contient pas d'extraction ligne par ligne : voir *Extraction sur requête libre* pour
un export complet à la demande. Il est écrit en local sous `{endpoint}/outputs/`, ou sous
`{OUTPUT_BASE}/{API_ID}/` si `OUTPUT_BASE` est renseigné, et déposé sur le dépôt distant si
celui-ci est configuré — chemin stable, écrasé à chaque exécution, et source du rapport Power BI.

Chaque exécution produit en outre **un rapport PDF** `Rapport_Qualite_Outliers_{api_id}_{date}.pdf`,
en deux sections : statistiques générales et indicateurs de qualité sur l'ensemble de l'historique
traité, puis synthèse des outliers par champ et détail par champ — valeur source, banque, nombre
d'occurrences. Le nombre de valeurs détaillées par champ est plafonné par
`reports.top_n_outliers_detail`, la liste exhaustive restant dans le classeur. Le PDF est écrit
dans un sous-dossier `Rapport/` dédié.

Les deux fichiers sont joints à la notification de fin d'exécution. Son corps suit un gabarit
texte : objet `Pipeline {libellé endpoint} : Rapport de qualité et rapport des outliers – {date}`,
puis les indicateurs de l'exécution — lignes traitées, nouvelles valeurs distinctes, normalisées et
outliers, taux de conformité, temps d'exécution — puis un renvoi au PDF joint. En cas d'échec, le
corps ne contient **jamais** le texte brut de l'exception : seulement une catégorie courte et non
technique, le détail restant dans les journaux (`logs/{api_id}_{horodatage}.log`).

---

## État incrémental

**Les tables source restent strictement en lecture seule** : aucune écriture, aucun DDL. L'état
incrémental — dernière date de création traitée, compteurs cumulatifs — est donc tenu localement,
dans `state/{api_id}_run_state.json`, un fichier par endpoint, ou sous `{STATE_DIR}` si cette
variable est renseignée. L'écriture est atomique — fichier temporaire puis remplacement — pour ne
jamais corrompre l'état si l'exécution est interrompue.

Ce fichier est propre à la machine qui exécute le pipeline. En cas de changement de serveur,
réamorcer l'état avec `scripts/seed_state.py` plutôt que de le copier.
