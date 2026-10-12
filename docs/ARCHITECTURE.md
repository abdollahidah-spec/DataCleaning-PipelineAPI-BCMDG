# Architecture technique

Documents liés :
- [ARCHITECTURE_PROJET.md](ARCHITECTURE_PROJET.md) — description de l'existant destinée à une
  équipe technique : les deux volets (par champ / par API), le périmètre, la nature des mapping
  tables, le déroulé d'une exécution, les données et leur persistance, les dépendances et les
  limites connues.
- [FUNCTIONAL.md](FUNCTIONAL.md) — règles métier, champ par champ et API par API.
- [CONFIGURATION.md](CONFIGURATION.md) — variables d'environnement et clés des YAML.
- Le README de chaque package d'endpoint détaille ses champs, ses règles, ses livrables, ses
  réglages et ses coûts : [e07_fs](../e07_fs/README.md), [e08_ocd](../e08_ocd/README.md),
  [e09_pe](../e09_pe/README.md), [e10_fe](../e10_fe/README.md), [e11_rdcc](../e11_rdcc/README.md).

## Principe : une exécution, une API, tous ses champs

`shared/base_api_pipeline.py::BaseApiPipeline` orchestre une exécution complète pour une API :

```
load_data()  ->  preprocess()  ->  process_fields()  ->  compute_quality()
    -> build_instructions_df()  ->  assemble_output_workbook()  ->  write_output()
    -> _generate_pdf_reports()  ->  upload_output()  ->  persist_state()  ->  notify()
```

`preprocess()` est un point d'extension : par défaut il ne fait rien, E11 s'en sert pour
précalculer la colonne de non-activité globale (voir `e11_rdcc/global_na.py`).

`build_extraction_frame()` existe toujours mais n'est plus appelée par `run()` (plus
d'extraction ligne par ligne dans l'exécution automatisée, sur demande métier) — elle est
réutilisée telle quelle par l'outil ad hoc `{api}/ad_hoc_extraction.py` pour les extractions sur
mesure, disponible pour E07, E10 et E11 ; pas encore porté pour E08 et E09.

Chaque champ de l'API est un `FieldProcessor` (`shared/field_processor.py`), configuré depuis la
section `fields:` du YAML de l'API. Le type déclaré sélectionne la famille de traitement, le nom
sélectionne le module du package ; un type ou un nom inconnu échoue explicitement, au démarrage
(`shared/config_validate.py`).

| Type déclaré | Implémentation | Sortie | Endpoints |
|---|---|---|---|
| `categorical` | `shared/field_processor.py::CategoricalFieldProcessor`, spécialisé par `{api}/fields/{champ}.py` | mapping table brut → normalisé, `OUTLIER` inclus, et stats d'outliers par RefBanque | les 5, soit 23 couples champ/API |
| `numeric_coherence` | `e11_rdcc/fields/numeric_coherence.py` | rapport d'anomalies, avec chaînage temporel J / J+1 | E11 |
| `numeric_validation` | `e09_pe/fields/echeances.py` | rapport d'anomalies, règle par règle | E09 |
| `transaction_validation` | `e07_fs/fields/transactions.py`, `e10_fe/fields/transactions.py` | rapport d'anomalies, règle par règle | E07, E10 |

Les trois familles de contrôle implémentent directement `FieldProcessor` : elles ne produisent
pas de mapping table, seulement un rapport d'anomalies.

`process_fields()` exécute tous les processeurs sur le **même instantané brut** (aucun ne dépend
de la colonne de sortie d'un autre) — les champs catégoriels (appels Claude, attente réseau)
s'exécutent donc en concurrence via `ThreadPoolExecutor`.

## Décision H — chaque API est un package auto-contenu

**Un champ partagé par plusieurs APIs est copié dans chaque package, il n'est pas factorisé dans
un module commun.** Seule l'infrastructure générique et indépendante des champs (I/O, état
incrémental, email, SharePoint, l'ABC `FieldProcessor`, l'orchestrateur, les règles transverses)
vit dans `shared/`.

Raisons :
1. Le cache warm-start est naturellement scopé par API (déjà le cas pour `NomCorrespondant` dans
   l'ancien repo — `validated_classif_nomcorrespondant_e08_ocd.json` vs `..._e11_rdcc.json`). Le
   cache `Devise` de l'ancien repo était global à toutes les APIs — un défaut de conception
   corrigé ici (`validated_classif_devise_e11_rdcc.json`).
2. « Le même champ » diverge vite en pratique. `NomDonneurOrdre` en est l'illustration :
   rapprochement fiscal DGI sur E07 et E08, Claude avec recherche web sur E10, l'émetteur y étant
   étranger. Un module partagé optimisé pour le cas simple deviendrait un frein, ou imposerait
   une abstraction prématurée.

E07_FS et E10_FE ont été construits ainsi, à partir d'une copie des modules d'E08 puis adaptés.
Aucun couplage entre packages d'API.

Le prix de cette décision est la dérive possible entre copies :
`tests/test_coherence_packages.py` vérifie que les mécanismes transverses — étape `MAP_CIBLE`,
envoi des lots Claude en parallèle — sont présents dans tous les packages qui doivent les porter.

## FieldResult — le contrat entre un champ et l'orchestrateur

```python
@dataclass
class FieldResult:
    df: pd.DataFrame                     # colonnes ajoutées par ce champ
    classification_df: Optional[pd.DataFrame]   # brut -> normalisé, OUTLIER inclus ; None si pas de concept de classification
    outliers_df: pd.DataFrame            # stats outliers (catégoriel) ou anomalies (contrôle)
    exclude_from_export: list            # colonnes intermédiaires exclues (extraction ad hoc)
    stats: dict                          # alimente le rapport de qualité (logs + email)
    sheet_names: dict                    # noms des feuilles Excel associées
```

L'orchestrateur ne manipule que cette structure : il ignore ce qu'est une devise, un pays ou un
solde.

## Modules partagés

| Module | Rôle |
|---|---|
| `base_api_pipeline.py` | orchestrateur d'une exécution, de bout en bout |
| `field_processor.py` | contrat `FieldProcessor` / `FieldResult`, `CategoricalFieldProcessor` |
| `config.py`, `config_validate.py` | fusion du YAML d'API avec `config_base.yaml`, validation au démarrage |
| `db_connector.py`, `query_columns.py` | chargement SQL : projection des colonnes utiles, `CAST` des colonnes texte, lecture par tranches, reprise sur coupure réseau |
| `state_store.py` | état incrémental, un fichier JSON par API sous `STATE_DIR` |
| `na_rule.py` | règle NA, appliquée en dernier et vectorisée |
| `referentiel_cibles.py` | valeur déjà conforme au référentiel (étape `MAP_CIBLE`), comparaison insensible à la casse |
| `dgi_cache.py` | mémoire des rapprochements fiscaux déterministes, avec empreinte de la base DGI |
| `claude_client.py` | appels au modèle de langage : liste fermée, arbitrage, recherche web |
| `claude_batches.py` | découpage en lots, envoi en parallèle, plafond de valeurs par exécution |
| `corrections_history.py` | historique des corrections métier appliquées, alimente l'onglet `Instructions` |
| `writer.py`, `build_tables.py` | assemblage et écriture du classeur |
| `report_templates.py`, `quality_report.py`, `pdf_report.py` | rapports de qualité et d'outliers, rendu PDF |
| `email_notifier.py`, `sharepoint_uploader.py` | notification et dépôt distant, tous deux optionnels |
| `env_loader.py`, `logging_conf.py`, `console.py`, `errors.py`, `frame_utils.py` | socle technique |

## Dépendances

Voir `requirements.txt`. `rapidfuzz` est requis par le rapprochement fiscal DGI
(`{api}/fields/_entity_matching.py`, E07, E08 et E10) ; `pycountry`, `babel` et `geonamescache`
construisent le référentiel Pays en code. Aucune infrastructure locale à héberger : pas de modèle
exécuté sur site, pas de service tiers à déployer.

## Diagramme de la pipeline

```
                 ┌──────────────────────┐
                 │  load_data()         │  (fichier local | SQL Initial | SQL Incremental)
                 └──────────┬───────────┘
                            │ df_raw
                 ┌──────────▼───────────┐
                 │  preprocess()        │  (point d'extension ; non-activité globale pour E11)
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │  process_fields()    │──► ThreadPoolExecutor (champs catégoriels, attente réseau)
                 │                      │──► séquentiel (familles de contrôle, calcul local)
                 └──────────┬───────────┘
                            │ df_final, [(processor, FieldResult)]
                            ▼
                    compute_quality()  (logs + alimente l'email)
                            │
                            ▼
                 assemble_output_workbook()
        {Champ}: classification | Anomalies_* | Instructions
                            │
                            ▼
                      write_output()  (local, OUTPUT_BASE)
                            │
                            ▼
                 _generate_pdf_reports()  (qualité + outliers)
                            │
                            ▼
                     upload_output()  (SharePoint si configuré)
                            │
                            ▼
     persist_state()  (fichier JSON local sous STATE_DIR — horodatage traité et cumulatifs
                       — PAS une table SQL, la base source reste lecture seule)
                            │
                            ▼
                    notify()  (email OK/KO : stats de l'exécution + stats globales)
```
