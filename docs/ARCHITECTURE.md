# Structure et organisation du code

Document de référence pour qui doit lire, modifier ou reprendre le code. Il décrit les **deux
chaînes** de traitement : leur arborescence, leur orchestrateur, le contrat que respecte un champ,
ce qu'elles partagent et ce sur quoi elles divergent.

Le principe, le périmètre et la nature des livrables sont décrits dans
[ARCHITECTURE_PROJET.md](ARCHITECTURE_PROJET.pdf) ; les règles métier dans
[FUNCTIONAL.md](FUNCTIONAL.md) ; les réglages dans [CONFIGURATION.md](CONFIGURATION.md).

## 1. Les deux dépôts

| | Chaîne par champ | Chaîne par API |
|---|---|---|
| Dépôt | `DataCleaning-PipelineField-BCMDG` | `DataCleaning-PipelineAPI-BCMDG` |
| Unité de lancement | un champ | un endpoint |
| Portée d'une commande | toutes les APIs où le champ existe, ou une seule | tous les champs de l'endpoint |
| Découpage du code | un dossier par champ, 8 champs, 22 couples champ/API | un package par endpoint, 5 endpoints, 23 couples |
| Orchestrateur | `shared/base_pipeline.py::BasePipeline` | `shared/base_api_pipeline.py::BaseApiPipeline` |
| Résolution assistée | modèle local (Ollama, embeddings) pour `Pays` et `NatureEconomique`, Claude pour les noms | Claude pour tous les champs concernés |
| Traitement par delta | non : mémoire des valeurs validées seulement | oui : état incrémental par endpoint |
| Livrables | un classeur et un CSV horodatés par couple | un classeur et deux rapports PDF par endpoint |
| Notification, dépôt distant | dépôt distant seulement | notification et dépôt distant |
| Tests automatisés | aucun | 450, dont des tests de cohérence inter-packages |

Les deux chaînes appliquent les mêmes règles métier et alimentent la même forme de mémoire des
valeurs validées. Le dépôt par champ est l'origine des référentiels et des mémoires reprises par
le dépôt par API.

## 2. Chaîne par champ

### 2.1 Arborescence

```
{champ}/
├── normalize_{champ}.py      cascade de nettoyage — tout le métier du champ
├── pipeline_{champ}.py       orchestrateur du champ, hérite de BasePipeline — point d'entrée CLI
├── config/{API}.yaml         un fichier par couple champ/API (2 à 5 selon le champ)
├── referentiel/              référentiels métier et mémoires de valeurs validées
├── keywords.py               listes de mots-clés             (NomDonneurOrdre)
├── build_referentiel.py      reconstruction depuis le fichier métier   (NomDonneurOrdre, Beneficiaire)
├── apply_corrections.py      injection des corrections métier (NomDonneurOrdre, Beneficiaire, NomCorrespondant)
└── outputs/{API}/            livrables horodatés

shared/
├── base_pipeline.py          orchestrateur commun, règle NA, chargement de configuration, CLI
├── db_connector.py           lecture de la table source et des fichiers locaux
├── build_tables.py           construction de la mapping table et de l'analyse des outliers
├── writer.py                 écriture du classeur à trois onglets
├── claude_client.py          résolution assistée des champs de noms
├── ollama_client.py          résolution assistée locale de NatureEconomique
├── sharepoint_uploader.py    dépôt distant
└── config_base.yaml          socle de configuration

req/                          fichiers de référence métier en entrée
```

### 2.2 Orchestrateur

`shared/base_pipeline.py::BasePipeline` porte tout ce qui ne dépend pas du champ :

| Méthode | Rôle |
|---|---|
| `run(config_path, ...)` | une exécution sur un couple champ/API : chargement, normalisation, tables de sortie, écriture, dépôt distant |
| `run_all(config_dir, max_workers)` | toutes les APIs du champ, **en parallèle**, un fil par couple |
| `_save_outputs(df, cfg, historique)` | écriture du classeur et du CSV horodatés sous `{champ}/outputs/{API}/` |
| `cli(config_dir_default)` | interface en ligne de commande, commune à tous les champs |
| `apply_na_rule(...)` | règle NA, fonction de module, appliquée après la cascade |
| `parse_schedule(valeur)` | relance périodique : nombre suivi de `m`, `h` ou `d`, jours par défaut |
| `load_config(path)` | fusion du YAML du couple avec `shared/config_base.yaml` |

Les sept options de la ligne de commande, identiques pour les huit champs :

| Option | Effet |
|---|---|
| `--config` | un seul couple champ/API |
| `--config-dir` | dossier des YAML du champ, pour `--all` |
| `--all` | toutes les APIs du champ |
| `--workers` | nombre de couples traités en parallèle (défaut 4) |
| `--input` | fichier local CSV ou Excel au lieu de la base |
| `--warm-start` | mémoire des valeurs validées pour les modalités connues, cascade pour les nouvelles |
| `--schedule` | relance automatique à intervalle régulier |

### 2.3 Contrat d'un champ

Un champ fournit un `pipeline_{champ}.py` qui hérite de `BasePipeline` et implémente :

| Méthode | Obligatoire | Rôle |
|---|---|---|
| `normalize(df, cfg)` | oui | appelle la cascade de `normalize_{champ}.py` et retourne le jeu de données annoté |
| `build_output_tables(df, cfg)` | oui | mapping table et analyse des outliers, en général via `shared/build_tables.py` |
| `get_instructions_history(cfg)` | non | historique des corrections à placer dans l'onglet `Instructions` |
| `get_export_cols(df, cfg)` | non | colonnes retenues dans le CSV d'extraction |

Tout le reste — parallélisme, chargement, écriture, horodatage, dépôt distant, relance
périodique — est hérité et n'a pas à être redéfini.

### 2.4 Déroulé d'une exécution

1. **Configuration** : le YAML du couple est fusionné avec le socle.
2. **Chargement** : table source lue intégralement, ou fichier local si `--input`. Pour `Pays` et
   `NatureEconomique`, un filtre sur la date de création borne le volume.
3. **Cascade** : exécutée sur les **valeurs distinctes** du champ, puis rediffusée sur les lignes.
   Chaque valeur repart avec la méthode qui l'a résolue.
4. **Règle NA**, appliquée en dernier.
5. **Tables de sortie** : mapping table des valeurs résolues, analyse des outliers avec
   occurrences et répartition par banque.
6. **Écriture** : classeur à trois onglets — `Mapping_Clean`, `Analyse_Outliers`,
   `Instructions` — et CSV d'extraction, tous deux horodatés.
7. **Dépôt distant**, si configuré.

## 3. Chaîne par API

### 3.1 Arborescence

```
e0X_xxx/
├── config/E0X_XXX.yaml       configuration de l'endpoint : champs, colonnes, seuils
├── fields/
│   ├── {champ}.py            un module par champ de l'endpoint
│   ├── _entity_matching.py   rapprochement fiscal        (E07, E08, E10)
│   ├── _keywords.py          listes de mots-clés          (E07, E10)
│   ├── transactions.py       contrôles de transaction     (E07, E10)
│   ├── numeric_coherence.py  cohérence des soldes         (E11)
│   └── echeances.py          contrôles d'échéance         (E09)
├── pipeline.py               orchestrateur de l'endpoint, hérite de BaseApiPipeline
├── run_pipeline.py           point d'entrée CLI
├── reports.py                rapport d'outliers propre à l'endpoint
├── apply_corrections.py      injection des corrections métier
├── ad_hoc_extraction.py      extraction sur requête libre  (E07, E10, E11)
├── build_referentiel.py      reconstruction depuis le fichier métier (selon l'endpoint)
├── global_na.py              témoin de non-activité global (E11)
├── referentiel/              référentiels métier et mémoires de valeurs validées
└── outputs/                  livrables, si OUTPUT_BASE n'est pas défini

shared/                       socle générique, sans logique métier de champ
tests/                        51 fichiers, 450 tests
state/                        état incrémental, un fichier par endpoint
```

Un package d'endpoint ne réutilise que `shared/`, et rien d'un autre package. Ajouter un endpoint
ne modifie aucun endpoint existant.

### 3.2 Orchestrateur

`shared/base_api_pipeline.py::BaseApiPipeline.run()` enchaîne :

```
load_data()  ->  preprocess()  ->  process_fields()  ->  compute_quality()
    -> build_instructions_df()  ->  assemble_output_workbook()  ->  write_output()
    -> _generate_pdf_reports()  ->  upload_output()  ->  persist_state()  ->  notify()
```

`build_extraction_frame()` n'est plus appelée par `run()` — l'extraction ligne par ligne a été
retirée de l'exécution automatisée. Elle reste utilisée par `{api}/ad_hoc_extraction.py`.

### 3.3 Points d'extension

Un pipeline d'endpoint hérite de `BaseApiPipeline` et ne surcharge que ceci :

| Méthode | Obligatoire | Rôle |
|---|---|---|
| `_build_field_processors(cfg)` | oui | instancie les processeurs de champs déclarés dans le YAML |
| `build_reports_markdown(results, quality)` | en pratique oui | rapports de qualité et d'outliers de l'endpoint |
| `preprocess(df_raw)` | non | colonne calculée avant le traitement des champs — E11 y précalcule son témoin de non-activité global |
| `preprocess_exclude_columns()` | non | colonnes ajoutées par `preprocess()` à exclure des livrables |

### 3.4 Contrat d'un champ

Un champ est un `FieldProcessor` (`shared/field_processor.py`) dont la méthode de traitement
retourne un `FieldResult` :

```python
@dataclass
class FieldResult:
    df: pd.DataFrame                     # colonnes ajoutées par ce champ
    classification_df: Optional[pd.DataFrame]   # mapping table brut -> normalisé ; None si le champ n'en produit pas
    outliers_df: pd.DataFrame            # stats outliers (catégoriel) ou anomalies (contrôle)
    exclude_from_export: list            # colonnes intermédiaires exclues des livrables
    stats: dict                          # alimente le rapport de qualité et la notification
    sheet_names: dict                    # noms des feuilles Excel associées
```

Le `type` déclaré dans le YAML sélectionne la famille, le `name` sélectionne le module du
package. Un type ou un nom inconnu échoue au démarrage (`shared/config_validate.py`).

| `type` | Implémentation | Sortie | Endpoints |
|---|---|---|---|
| `categorical` | `shared/field_processor.py::CategoricalFieldProcessor`, spécialisé par `{api}/fields/{champ}.py` | mapping table et méthode de résolution par ligne | les 5, soit 23 couples |
| `numeric_coherence` | `e11_rdcc/fields/numeric_coherence.py` | anomalies, avec chaînage temporel entre lignes | E11 |
| `numeric_validation` | `e09_pe/fields/echeances.py` | anomalies, règle par règle | E09 |
| `transaction_validation` | `e07_fs/fields/transactions.py`, `e10_fe/fields/transactions.py` | anomalies, règle par règle | E07, E10 |

### 3.5 Contenu de `shared/`

| Module | Rôle |
|---|---|
| `base_api_pipeline.py` | orchestrateur d'une exécution, de bout en bout |
| `field_processor.py` | contrat `FieldProcessor` / `FieldResult`, et `CategoricalFieldProcessor` |
| `config.py`, `config_validate.py` | fusion du YAML avec le socle, validation stricte au démarrage |
| `db_connector.py` | lecture seule : projection, lecture par paquets, conversion des colonnes texte volumineuses, reprise sur coupure réseau, lecture de fichiers locaux avec replis d'encodage |
| `query_columns.py` | projection des colonnes utiles et confrontation au schéma réel de la table |
| `state_store.py` | état incrémental, un fichier JSON par endpoint |
| `na_rule.py` | règle NA, vectorisée |
| `referentiel_cibles.py` | valeur déjà conforme au référentiel, étape `MAP_CIBLE` |
| `dgi_cache.py` | mémoire des rapprochements fiscaux déterministes, avec empreinte du fichier source |
| `claude_client.py` | liste fermée, identification par code SWIFT/BIC, arbitrage entre candidats, identification documentaire |
| `claude_batches.py` | découpage en lots, envoi en parallèle, plafond de valeurs par exécution |
| `corrections_history.py` | historique des corrections appliquées, alimente l'onglet `Instructions` |
| `writer.py`, `build_tables.py` | assemblage et écriture du classeur |
| `report_templates.py`, `quality_report.py`, `pdf_report.py` | rapports de qualité et d'outliers, rendu PDF |
| `email_notifier.py`, `sharepoint_uploader.py` | notification et dépôt distant, tous deux optionnels |
| `env_loader.py`, `logging_conf.py`, `console.py`, `errors.py`, `frame_utils.py` | socle technique |

### 3.6 Déroulé d'une exécution

1. **Mode** : historique complet, ou delta depuis le dernier horodatage traité.
2. **Confrontation au schéma réel** avant la requête principale : une colonne optionnelle absente
   est retirée, une colonne indispensable absente arrête l'exécution.
3. **Chargement** : projection des seules colonnes utiles, lecture par paquets, conversion des
   colonnes texte volumineuses, reprise sur coupure réseau.
4. **Prétraitement**, si l'endpoint en déclare un.
5. **Champs en parallèle**, un fil par champ, chacun ne recevant que ses colonnes.
6. **Qualité** : indicateurs de l'exécution et cumuls.
7. **Classeur** : une feuille par champ catégoriel, une feuille d'anomalies par famille de
   contrôle, et l'onglet `Instructions`.
8. **Rapports PDF**, dépôt distant, état, notification.

Un échec laisse l'état inchangé : le delta est repris à l'exécution suivante. Une exécution sur
fichier local est entièrement hors ligne — ni notification, ni dépôt distant, ni écriture d'état.

## 4. Où vit chaque étape de résolution

Les deux chaînes appliquent la même escalade en cinq étapes, décrite dans
[FUNCTIONAL.md](FUNCTIONAL.md) §2 : nettoyage basique, mapping direct, rapprochement approché,
similarité sémantique, puis client de modèle en dernier recours. Voici où chacune est implémentée,
dans l'un et l'autre dépôt.

| Étape | Chaîne par champ | Chaîne par API |
|---|---|---|
| 0. Mémoire des valeurs validées | `{champ}/referentiel/validated_classif_*.json`, lue par `normalize_{champ}.py` — **seulement si `--warm-start`** | `{api}/referentiel/validated_classif_{champ}_{api}.json`, lue systématiquement par `{api}/fields/{champ}.py` |
| 1. Nettoyage basique | fonctions `clean_*` et `nettoyer` de `normalize_{champ}.py` ; `clean_label` et `hyper_normaliser` pour les noms | mêmes fonctions dans `{api}/fields/{champ}.py` ; `clean_label` et `hyper_normaliser` dans `{api}/fields/_entity_matching.py` |
| 2. Mapping direct | référentiels de `{champ}/referentiel/`, listes de mots-clés de `{champ}/keywords.py` | référentiels de `{api}/referentiel/`, listes de `{api}/fields/_keywords.py`, et `shared/referentiel_cibles.py` pour l'étape `MAP_CIBLE` |
| 3. Rapprochement approché | `rapidfuzz` dans `nomdonneurordre/`, `beneficiaire/` et `pays/normalize_pays.py` | `rapidfuzz` dans `{api}/fields/_entity_matching.py` et `{api}/fields/pays.py` ; résultats déterministes mémorisés par `shared/dgi_cache.py` |
| 4. Similarité sémantique | `sentence-transformers` dans `nature_economique/normalize_nature_economique.py` | **absente** — le champ passe du mapping direct au client de modèle |
| 5. Client de modèle | `shared/claude_client.py` pour les noms, `shared/ollama_client.py` pour `NatureEconomique`, `ollama` appelé directement pour `Pays` — appels séquentiels | `shared/claude_client.py` pour tous les champs concernés, par lots parallèles via `shared/claude_batches.py` |
| Règle NA | `apply_na_rule`, fonction de `shared/base_pipeline.py` | `shared/na_rule.py`, vectorisée |
| Règle NA, exception `Pays` | règle dédiée dans `pays/normalize_pays.py` | règle dédiée dans `{api}/fields/pays.py` |

`Pays` est la seule exception à la règle NA partagée : `NA` étant le code ISO de la Namibie,
ce champ porte sa propre règle, de sens inverse, décrite dans [FUNCTIONAL.md](FUNCTIONAL.md) §4.

Deux conséquences de structure :

- la chaîne par champ dépend d'un **modèle installé localement**, désigné par un chemin absolu
  dans la configuration (`model_embed: C:\models\mpnet-base-v2`) et d'un service Ollama en
  fonctionnement. Ce sont deux prérequis attachés au poste, non portables en l'état ;
- la chaîne par API n'héberge aucun modèle, mais son étape 5 dépend d'un service externe et
  facturé. Les trois garde-fous — lots parallèles, plafond par exécution, mémorisation — sont
  implémentés dans `shared/claude_batches.py` et dans la mémoire de chaque champ.

### Points d'entrée

| | Chaîne par champ | Chaîne par API |
|---|---|---|
| Traitement | `python {champ}/pipeline_{champ}.py` | `python -m {api}.run_pipeline` |
| Options | `--config`, `--config-dir`, `--all`, `--workers`, `--input`, `--warm-start`, `--schedule` | `--config` (obligatoire), `--mode auto\|initial\|incremental`, `--input`, `--dry-run` |
| Corrections métier | `python {champ}/apply_corrections.py --file <classeur>` — l'endpoint est déduit du nom de fichier | `python -m {api}.apply_corrections --file <classeur>` |
| Reconstruction d'un référentiel | `python {champ}/build_referentiel.py` | `python -m {api}.build_referentiel` |
| Extraction sur requête libre | — | `python -m {api}.ad_hoc_extraction --config <yaml> --query <sql>` (E07, E10, E11) |

La différence de forme reflète la différence de structure : un champ est un script autonome dans
son dossier, un endpoint est un package Python exécuté comme module.

## 5. Correspondance des modules entre les deux dépôts

| Champ | Colonne source | Chaîne par champ | Chaîne par API |
|---|---|---|---|
| Devise | `Devise` | `devise/normalize_devise.py`, 5 YAML | `{e07,e08,e09,e10,e11}/fields/devise.py` |
| Pays | `Pays` | `pays/normalize_pays.py`, 3 YAML | `{e07,e08,e10}/fields/pays.py` |
| NomDonneurOrdre | `NomDonneurOrdre` | `nomdonneurordre/normalize_nomdonneurordre.py`, 3 YAML | `{e07,e08,e10}/fields/nomdonneurordre.py` |
| Beneficiaire | `Beneficiaire` | `beneficiaire/normalize_beneficiaire.py`, 3 YAML | `{e07,e08,e10}/fields/beneficiaire.py` |
| NomCorrespondant | `NomCorrespondant` | `nomcorrespondant/normalize_nomcorrespondant.py`, 2 YAML | `{e08,e11}/fields/nomcorrespondant.py` |
| NatureEconomique | `NatureEconomique` | `nature_economique/normalize_nature_economique.py`, 2 YAML | `{e07,e10}/fields/nature_economique.py` |
| TypeSwift | `TypeSwfit` | `typeswift/normalize_typeswift.py`, 2 YAML | `{e07,e10}/fields/typeswift.py` |
| ModeReglement | `ModeReglement` | `mode_reglement/normalize_mode_reglement.py`, 2 YAML | `{e07,e10}/fields/mode_reglement.py` |
| Produits | `Produits` | — | `e08_ocd/fields/produits.py` |

`TypeSwfit` est l'orthographe réelle de la colonne dans le schéma source. `Produits` n'existe que
dans la chaîne par API.

## 6. Divergences assumées

| Sujet | Chaîne par champ | Chaîne par API |
|---|---|---|
| Résolution assistée de `NatureEconomique` | embeddings locaux puis modèle Qwen via Ollama — méthodes `EMBED`, `REGLE` | Claude sur la liste fermée des labels — méthode `CLAUDE` |
| Résolution assistée de `Pays` | modèle Qwen via Ollama — méthode `LLM` | Claude, et les résolutions sont mémorisées |
| Infrastructure | un service de modèle local à héberger | aucune, appels facturés à l'usage |
| Nommage des mémoires de valeurs validées | hétérogène : `validated_classif_devise.json` est **commun à toutes les APIs**, `validated_classif_MR.json`, `..._NatEco_FE.json`, `..._pays_fs.json` | uniforme : `validated_classif_{champ}_{api}.json`, une mémoire par couple |
| Borne d'historique | filtre sur la date de création pour `Pays` et `NatureEconomique` seulement | réglage `load.initial_since`, au 01/01/2024 sur E07, E09 et E10 |
| Champs numériques et de date | hors périmètre | inclus, avec rapport d'anomalies |
| Étape `MAP_CIBLE` | présente sur les champs de noms | présente sur tous les champs adossés à un référentiel de libellés |
| Appels au modèle | séquentiels | par lots parallèles, avec plafond par exécution |
| Rapports | classeur et CSV | classeur, rapport de qualité et rapport d'outliers en PDF |
| Tests | aucun | 450 |

La mémoire `Devise` commune à toutes les APIs est un défaut de conception du dépôt par champ :
une valeur tranchée pour un endpoint s'applique à tous. La chaîne par API le corrige en isolant
une mémoire par couple champ/API.

## 7. Tests de la chaîne par API

51 fichiers, 450 tests, hors réseau : la base, le modèle de langage, la messagerie et le dépôt
distant sont tous simulés. Quatre groupes :

- **par champ** — `test_devise.py`, `test_pays.py`, `test_nomdonneurordre.py`,
  `test_beneficiaire.py`, `test_produits.py`, `test_e07_fields.py`, `test_e10_fields.py` ;
- **par famille de contrôle** — `test_numeric_rules.py`, `test_e09_echeances.py`,
  `test_e07_transactions.py`, `test_e10_transactions.py`, `test_global_na.py` ;
- **par mécanisme transverse** — `test_na_rule.py` et `test_na_rule_null.py`,
  `test_map_cible_e08_e11.py`, `test_dgi_cache.py`, `test_claude_batches.py`,
  `test_corrections_cles.py`, `test_db_chargement.py`, `test_state_store.py` ;
- **cohérence inter-packages** — `test_coherence_packages.py` : présence de l'étape `MAP_CIBLE`,
  envoi des lots en parallèle, identité des copies d'un même champ entre packages (arbre
  syntaxique comparé, commentaires exclus), et absence de redéfinition locale de la règle NA.

Ce dernier groupe est la contrepartie du découpage en packages autonomes : il détecte la dérive
entre deux copies d'un même champ.

## 8. Ajouter un champ ou un endpoint

**Un champ dans la chaîne par champ** : créer `{champ}/normalize_{champ}.py` avec la cascade,
`{champ}/pipeline_{champ}.py` qui hérite de `BasePipeline` et implémente les deux méthodes
obligatoires, un `config/{API}.yaml` par couple visé, et le référentiel dans
`{champ}/referentiel/`.

**Un endpoint dans la chaîne par API** :

1. Créer `e0X_xxx/` avec `config/`, `fields/`, `pipeline.py`, `run_pipeline.py`, `reports.py`,
   `apply_corrections.py` et `referentiel/`.
2. Partir d'une copie des modules de champs d'un endpoint proche — E08 pour un flux à
   rapprochement fiscal, E10 pour un flux à émetteur étranger — puis adapter.
3. Déclarer les champs dans le YAML : la validation au démarrage signale ce qui manque.
4. Ajouter le package aux tests de cohérence inter-packages.

Aucun endpoint existant n'est modifié par cette opération.

## 9. Dépendances

| | Chaîne par champ | Chaîne par API |
|---|---|---|
| Socle | `pandas`, `numpy`, `pyyaml`, `python-dotenv`, `sqlalchemy`, `pyodbc`, `xlsxwriter`, `openpyxl` | idem |
| Référentiel Pays | `pycountry`, `babel`, `geonamescache` | idem |
| Rapprochement fiscal | `rapidfuzz` | idem |
| Résolution assistée | `anthropic`, `ollama`, `sentence-transformers`, `torch` | `anthropic` seulement |
| Rapports et dépôt distant | — | `markdown`, `xhtml2pdf`, `msal`, `requests` |

L'écart tient aux deux familles de modèles : la chaîne par champ embarque un modèle local et ses
dépendances d'apprentissage automatique ; la chaîne par API n'appelle qu'un service distant.
