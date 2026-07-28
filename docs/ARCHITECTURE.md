# Architecture technique

## Principe : un run, une API, tous ses champs

`shared/base_api_pipeline.py::BaseApiPipeline` orchestre un run complet pour une API :

```
load_data()  ->  process_fields()  ->  compute_quality()
    -> assemble_output_workbook()  ->  write_output()  ->  upload_output()
    -> persist_state()  ->  notify()
```

`build_extraction_frame()` existe toujours mais n'est plus appelée par `run()` (plus
d'extraction ligne par ligne dans le run automatisé, sur demande métier) — elle est réutilisée
telle quelle par l'outil ad hoc `e11_rdcc/ad_hoc_extraction.py` pour les extractions sur mesure.

Chaque champ de l'API est un `FieldProcessor` (`shared/field_processor.py`), configuré depuis
la section `fields:` du YAML de l'API. Deux implémentations génériques existent dans `shared/` :

- **`CategoricalFieldProcessor`** — champ à référentiel fini (Devise, NomCorrespondant, ...).
  Produit une `classification_df` (table brut → normalisé, OUTLIER inclus — exposée à Power BI)
  et des stats d'outliers par RefBanque.
- Le moteur de **cohérence numérique** (E11 uniquement pour l'instant, `e11_rdcc/fields/numeric_coherence.py::NumericCoherenceProcessor`)
  implémente directement `FieldProcessor` — pas de classification, un rapport d'anomalies.

`process_fields()` exécute tous les processeurs sur le **même snapshot brut** (aucun ne dépend
de la colonne de sortie d'un autre) — les champs catégoriels (appels Claude, I/O-bound)
s'exécutent donc en concurrence via `ThreadPoolExecutor`.

## Décision H — chaque API est un package auto-contenu

**`Devise` et `NomCorrespondant` vivent dans `e11_rdcc/fields/`, pas dans un module top-level
partagé entre APIs.** Seule l'infrastructure générique et field-agnostic (I/O, état incrémental,
email, SharePoint, l'ABC `FieldProcessor`, l'orchestrateur, `apply_na_rule`) vit dans `shared/`.

Raisons :
1. Le cache warm-start est naturellement scopé par API (déjà le cas pour `NomCorrespondant`
   dans l'ancien repo — `validated_classif_nomcorrespondant_e08_ocd.json` vs `..._e11_rdcc.json`).
   Le cache `Devise` de l'ancien repo était global à toutes les APIs — un bug de conception
   corrigé ici (`validated_classif_devise_e11_rdcc.json`).
2. "Le même champ" diverge vite en pratique dès qu'une API a des besoins plus complexes —
   dans l'ancien repo, `NomDonneurOrdre` avait déjà deux cascades complètement différentes
   selon l'API (Claude+web-search pour E10, matching fiscal DGI pour E07/E08). Un module
   partagé optimisé pour le cas simple de E11 deviendrait vite un frein ou nécessiterait une
   abstraction prématurée dès qu'une autre API a besoin de plus.

Une future API (ex: E07_FS) crée son propre `e07_fs/fields/devise.py` — en partant d'une copie
de celui d'E11 si pertinent, libre de diverger ensuite. Aucun couplage entre packages d'API.

## FieldResult — le contrat entre un champ et l'orchestrateur

```python
@dataclass
class FieldResult:
    df: pd.DataFrame                     # colonnes ajoutées par ce champ
    classification_df: Optional[pd.DataFrame]   # brut -> normalisé, OUTLIER inclus ; None si pas de concept de classification
    outliers_df: pd.DataFrame            # stats outliers (catégoriel) ou anomalies (numérique)
    exclude_from_export: list            # colonnes intermédiaires exclues (extraction ad hoc)
    stats: dict                          # alimente le rapport de qualité (logs + email)
    sheet_names: dict                    # noms des feuilles Excel associées
```

## Dépendances

Voir `requirements.txt` — volontairement minimal pour E11 (pas de `sentence-transformers`/`torch`/
`ollama`/`rapidfuzz`, uniquement nécessaires pour des champs non utilisés par E11 — une future
API embarquant un champ à base d'embeddings les ajoutera à ce moment-là).

## Diagramme de la pipeline

```
                 ┌─────────────────────┐
                 │  load_data()         │  (fichier local | SQL Initial | SQL Incremental)
                 └──────────┬───────────┘
                             │ df_raw
                 ┌──────────▼───────────┐
                 │  process_fields()     │──► ThreadPoolExecutor (champs catégoriels, I/O Claude)
                 │                       │──► séquentiel (cohérence numérique, CPU local)
                 └──────────┬───────────┘
                             │ df_final, [(processor, FieldResult)]
                             ▼
                    compute_quality()  (logs + alimente l'email)
                             │
                             ▼
                 assemble_output_workbook()
        {Champ}: classification | Anomalies_Numeriques | Instructions
                             │
                             ▼
                       write_output()  (local, OUTPUT_BASE)
                             │
                             ▼
                      upload_output()  (SharePoint si configuré)
                             │
                             ▼
     persist_state() (fichier JSON local e11_rdcc/state/ — watermark + cumulatifs
                       — PAS une table SQL, la base source reste lecture seule)
                             │
                             ▼
                    notify()  (email OK/KO : stats du run + stats globales)
```
