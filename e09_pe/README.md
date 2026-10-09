# E09_PE — Prévisions d'échéances

Pipeline de nettoyage, normalisation et validation de l'endpoint **E09 — PE** (ticket BCMDG-223).
Endpoint le plus simple du repo : un champ catégoriel et deux règles de validation ligne à ligne,
sans cohérence inter-lignes ni gabarit « sans activité ».

## Source

| | |
|---|---|
| Table | `E9EtatBcmPrevisionEcheances` |
| Colonne d'horodatage | `dtCr` |
| Accès | lecture seule, aucune écriture ni DDL |
| Historique | borné aux lignes créées depuis le **01/01/2024** (`load.initial_since`) |

La table est volumineuse (plus de 5 millions de lignes) : la projection de colonnes, la lecture
par paquets et la borne d'historique sont ici déterminantes pour la durée du premier chargement.

## Champs traités

| Champ | Type | Traitement |
|---|---|---|
| `Devise` | catégoriel | Cascade entièrement déterministe, sans LLM : bruit connu → code ISO 4217 exact → code numérique → alias → nettoyage de repli. Identique à E11 et E08 |
| `MontantEcheance`, `DateEcheance` | validation numérique/date | 2 règles indépendantes, ligne à ligne : le résultat est un rapport d'anomalies, pas une valeur normalisée |

`NumCredoc` (référence du crédit documentaire) n'est pas normalisée : elle sert de témoin à la
règle NA de `Devise` et d'identifiant dans l'onglet d'anomalies.

## Règles de validation

| Règle | Contrôle |
|---|---|
| `AMOUNT_POSITIVE` | `MontantEcheance` numérique et **strictement** supérieur à 0 (une échéance nulle n'est pas une prévision) |
| `DATE_VALIDITY` | `DateEcheance` parsable et **strictement postérieure** à `dtCr`, comparaison au jour : une échéance est par nature future au moment de l'enregistrement |

La virgule décimale est acceptée. Une valeur non numérique ou manquante est elle-même une
anomalie. Une ligne en échec sur les deux règles produit deux lignes d'anomalies.

## Règle NA

```
Devise == 'NA' ET NumCredoc == 'NA'   -> 'NA'        (ligne sans activité)
Devise == 'NA' ET NumCredoc != 'NA'   -> OUTLIER     (NA suspecte)
Devise vide / NULL                    -> OUTLIER
```

Témoin simple par champ, contrairement à E11 et sa colonne globale : aucun gabarit « sans
activité » multi-champs n'est défini pour E09.

## Livrables

| Livrable | Contenu |
|---|---|
| `E09_PE_classification.xlsx` | Onglet `Devise` (table de classification cumulative : référentiel + cache + corrections), `Anomalies_Echeances` (lignes en anomalie, avec `NumCredoc` et `dtCr`), `Instructions` (historique des corrections) |
| `Rapport/Rapport_Qualite_Outliers_E09_PE_{AAAAMMJJ}.pdf` | Rapport de qualité du run + rapport des outliers par champ et par `RefBanque` |
| Email | Indicateurs du run en liste à puces, les deux livrables en pièce jointe |

Destination : `{OUTPUT_BASE}/E09_PE/`, ou `e09_pe/outputs/` si `OUTPUT_BASE` est vide. Chemin
stable, écrasé à chaque run. Au-delà de `reports.max_anomaly_rows_excel` lignes, le détail
intégral des anomalies part dans un CSV à côté du classeur.

## Commandes

```bash
# Premier chargement (depuis le 01/01/2024), à lancer une fois avant d'activer la planification
python -m e09_pe.run_pipeline --config e09_pe/config/E09_PE.yaml --mode initial

# Delta depuis le dernier run réussi — ce que lance la tâche planifiée
python -m e09_pe.run_pipeline --config e09_pe/config/E09_PE.yaml --mode incremental

# Essai hors ligne sur un fichier local : ni email, ni SharePoint, ni état incrémental
python -m e09_pe.run_pipeline --config e09_pe/config/E09_PE.yaml --input tests/fixtures/e09_pe_sample.csv

# Application des corrections métier (onglet Instructions : Champ | Input | Label_Attendu)
python -m e09_pe.apply_corrections --file "<OUTPUT_BASE>\E09_PE\E09_PE_classification.xlsx"
```

`--dry-run` supprime l'email et l'envoi SharePoint, mais écrit quand même le classeur et
enregistre l'état. L'extraction ad hoc n'est pas portée pour cet endpoint (disponible sur E11,
E07 et E10).

## Réglages du YAML

| Clé | Effet |
|---|---|
| `load.initial_since` | Borne d'historique du premier chargement (`dtCr >= 2024-01-01`) |
| `load.chunk_size` | Lecture par paquets avec progression dans les logs — indispensable sur cette table |
| `load.cast_max_text`, `load.retries` | Lecture accélérée des colonnes `NVARCHAR(MAX)`, reprise sur coupure réseau |
| `fields[].columns.*` | Noms réels des colonnes source et de sortie |
| `reports.*`, `email.display_name` | Volume du PDF, bascule CSV, libellé de l'endpoint dans l'email |

## Référentiels et caches

`e09_pe/referentiel/` ne contient que le référentiel ISO 4217 (`devise_referentiel.json`). Le
cache warm-start `validated_classif_devise_e09_pe.json` est créé au premier besoin : la cascade
Devise étant déterministe, il ne sert qu'aux corrections manuelles, qui ont priorité sur la
cascade. Les champs de validation n'ont pas de cache : une anomalie de montant ou de date ne se
« corrige » pas par un libellé.

## Prérequis

- `.env` renseigné : accès base en lecture seule, SMTP, `OUTPUT_BASE`, `STATE_DIR`. Aucune clé
  Claude n'est nécessaire pour cet endpoint.
- État incrémental : `{STATE_DIR}/E09_PE_run_state.json` (défaut `state/`).

## Tests

```bash
python -m pytest tests/test_e09_echeances.py tests/test_echeances_vectorise.py tests/test_e09_reports.py tests/test_devise.py -q
```
