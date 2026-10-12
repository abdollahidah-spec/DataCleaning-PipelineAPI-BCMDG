# Architecture technique

Repère de code, pour qui doit modifier la chaîne par API : où se trouve quoi, quel contrat
respecter, où brancher un ajout. Le principe, le fonctionnement et le périmètre sont décrits dans
[ARCHITECTURE_PROJET.md](ARCHITECTURE_PROJET.md) ; les règles et les livrables de chaque endpoint,
dans le README de son package.

## Organisation

```
shared/        socle générique
e0X_xxx/       un package par endpoint
```

Un package d'endpoint ne réutilise que `shared/`. Un champ porté par plusieurs endpoints est donc
une copie dans chaque package, et non un module commun : la contrepartie est la dérive possible
entre copies, que `tests/test_coherence_packages.py` surveille — présence de l'étape `MAP_CIBLE`,
envoi des lots Claude en parallèle, identité des copies (arbre syntaxique comparé, commentaires
exclus), et absence de redéfinition locale de la règle `NA`.

## Orchestrateur

`shared/base_api_pipeline.py::BaseApiPipeline.run()` enchaîne :

```
load_data()  ->  preprocess()  ->  process_fields()  ->  compute_quality()
    -> build_instructions_df()  ->  assemble_output_workbook()  ->  write_output()
    -> _generate_pdf_reports()  ->  upload_output()  ->  persist_state()  ->  notify()
```

`build_extraction_frame()` n'est plus appelée par `run()` — plus d'extraction ligne par ligne dans
l'exécution automatisée. Elle reste utilisée telle quelle par `{api}/ad_hoc_extraction.py`,
disponible pour E07, E10 et E11.

## Points d'extension

Un pipeline d'endpoint hérite de `BaseApiPipeline` et ne surcharge que ceci :

| Méthode | Obligatoire | Rôle |
|---|---|---|
| `_build_field_processors(cfg)` | oui | instancie les processeurs de champs du YAML |
| `build_reports_markdown(results, quality)` | en pratique oui | rapports de qualité et d'outliers de l'endpoint |
| `preprocess(df_raw)` | non | colonne calculée avant le traitement des champs — E11 y précalcule son témoin de non-activité globale (`e11_rdcc/global_na.py`) |
| `preprocess_exclude_columns()` | non | colonnes ajoutées par `preprocess()` à exclure des livrables |

Tout le reste — chargement, parallélisme, assemblage du classeur, rapports, dépôt, état,
notification — est générique et n'a pas à être redéfini.

## Contrat d'un champ

Un champ est un `FieldProcessor` (`shared/field_processor.py`) dont la méthode de traitement
retourne un `FieldResult` :

```python
@dataclass
class FieldResult:
    df: pd.DataFrame                     # colonnes ajoutées par ce champ
    classification_df: Optional[pd.DataFrame]   # mapping table brut -> normalisé ; None si le champ n'en produit pas
    outliers_df: pd.DataFrame            # stats outliers (catégoriel) ou anomalies (contrôle)
    exclude_from_export: list            # colonnes intermédiaires exclues des livrables
    stats: dict                          # alimente le rapport de qualité et l'email
    sheet_names: dict                    # noms des feuilles Excel associées
```

Le `type` déclaré dans le YAML sélectionne la famille, le `name` sélectionne le module du
package. Un type ou un nom inconnu échoue au démarrage (`shared/config_validate.py`).

| `type` | Implémentation | Endpoints |
|---|---|---|
| `categorical` | `shared/field_processor.py::CategoricalFieldProcessor`, spécialisé par `{api}/fields/{champ}.py` | les 5, soit 23 couples champ/API |
| `numeric_coherence` | `e11_rdcc/fields/numeric_coherence.py` | E11 |
| `numeric_validation` | `e09_pe/fields/echeances.py` | E09 |
| `transaction_validation` | `e07_fs/fields/transactions.py`, `e10_fe/fields/transactions.py` | E07, E10 |

Les trois familles de contrôle implémentent directement `FieldProcessor` : pas de mapping table,
seulement un rapport d'anomalies.

## Ajouter un endpoint

1. Créer `e0X_xxx/` : `config/E0X_XXX.yaml`, `fields/`, `pipeline.py`, `run_pipeline.py`,
   `reports.py`, `apply_corrections.py`, `referentiel/`.
2. Partir d'une copie des modules de champs d'un endpoint proche — E08 pour un flux à
   rapprochement fiscal, E10 pour un flux à émetteur étranger — puis adapter.
3. Déclarer les champs dans le YAML ; la validation au démarrage signale ce qui manque.
4. Ajouter le package aux tests de cohérence inter-packages.

Aucun endpoint existant n'est modifié par cette opération.

## Dépendances

Voir `requirements.txt`. `rapidfuzz` sert au rapprochement fiscal
(`{api}/fields/_entity_matching.py`, E07, E08, E10) ; `pycountry`, `babel` et `geonamescache`
construisent le référentiel Pays en code. Aucune infrastructure à héberger : pas de modèle exécuté
sur site, pas de service tiers à déployer.
