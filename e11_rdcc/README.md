# E11_RDCC — Relevés des comptes correspondants

Pipeline de nettoyage, normalisation et contrôle de cohérence de l'endpoint **E11 — RDCC**
(ticket BCMDG-172). Premier endpoint couvert : les choix structurants du repo (`shared/`,
orchestrateur, livrables, boucle de correction) y ont été établis puis réutilisés tels quels par
les autres APIs.

## Source

| | |
|---|---|
| Table | `E11EtatBcmReleveDesComptesCorrespondants` |
| Colonne d'horodatage | `dtCr` |
| Accès | lecture seule, aucune écriture ni DDL |
| Historique | intégral (aucune borne `load.initial_since`) |

## Champs traités

| Champ | Type | Traitement |
|---|---|---|
| `NomCorrespondant` | catégoriel | Cache warm-start → valeur déjà conforme au référentiel (`MAP_CIBLE`) → référentiel BCM validé → API Claude : une banque n'est retenue que si elle existe réellement avec un code SWIFT/BIC publié, sinon `OUTLIER` |
| `Devise` | catégoriel | Cascade entièrement déterministe, sans LLM : bruit connu → code ISO 4217 exact → code numérique → alias → nettoyage de repli (parenthèses, caractères non alphanumériques, préfixe à 3 lettres) |
| `SoldeDebutJournee`, `TotalMvtsDebiteursJournee`, `TotalMvtsCrediteurs`, `SoldeFinJournee`, `DateFinJournee` | cohérence numérique | 4 règles de cohérence métier, sans valeur normalisée : le résultat est un rapport d'anomalies |

## Règles de cohérence numérique

| Règle | Contrôle | Sévérité |
|---|---|---|
| `ARITHMETIC` | `SoldeFinJournee = SoldeDebutJournee + TotalMvtsCrediteurs − TotalMvtsDebiteursJournee` (tolérance `tolerance.absolute`, défaut 0,01) | ERROR |
| `TEMPORAL_CONTINUITY` | `SoldeFinJournee(J) = SoldeDebutJournee(J+1)` pour un même compte, regroupement `[RefBanque, NomCorrespondant, NumCompte, Devise]` validé par le métier. Deux relevés à la même date pour un même compte : ERROR (chaînage ambigu). Écart de plus d'un jour : WARNING | ERROR / WARNING |
| `NO_ACTIVITY_CONFORMITY` | Conformité du gabarit « sans activité » : champs identifiants tous à `NA` et 4 montants à 0. `NA` partielle = anomalie | ERROR |
| `DATE_VALIDITY` | `DateFinJournee` valide et antérieure ou égale à `dtCr`, comparaison au jour | ERROR |

Une ligne en échec sur plusieurs règles produit une ligne d'anomalie par règle : aucune anomalie
n'est masquée par une autre.

## Règle NA — témoin global, propre à E11

Les champs catégoriels de E11 ne jugent pas une `NA` isolément : le témoin est une colonne
synthétique `_E11_GlobalNoActivite`, calculée une seule fois par run pour les deux champs
(`global_na.py`, appelée depuis `E11Pipeline.preprocess()`).

```
_E11_GlobalNoActivite == 'NA'  <=>  NomCorrespondant, Devise et NumCompte valent tous 'NA'
                                   ET les 4 montants valent 0
Champ == 'NA' ET témoin == 'NA'   -> 'NA'        (ligne sans activité légitime)
Champ == 'NA' ET témoin != 'NA'   -> OUTLIER     (NA suspecte)
Champ vide / NULL                 -> OUTLIER
```

Correctif demandé par le Business Analyst : auparavant chaque champ décidait seul en ne regardant
que `NumCompte`, sans vérifier l'autre champ ni les montants.

## Livrables

| Livrable | Contenu |
|---|---|
| `E11_RDCC_classification.xlsx` | Un onglet par champ catégoriel (table de classification cumulative : référentiel + cache + corrections, indépendante du delta traité), `Anomalies_Numeriques` (lignes en anomalie), `Instructions` (historique des corrections, `Champ \| Input \| Label_Attendu`) |
| `Rapport/Rapport_Qualite_Outliers_E11_RDCC_{AAAAMMJJ}.pdf` | Rapport de qualité du run + rapport des outliers (répartition par champ et par `RefBanque`) |
| Email | Indicateurs du run en liste à puces, les deux livrables en pièce jointe |

Destination : `{OUTPUT_BASE}/E11_RDCC/`, ou `e11_rdcc/outputs/` si `OUTPUT_BASE` est vide.
Le classeur garde un chemin stable et est écrasé à chaque run, pour que Power BI pointe toujours
le même fichier. Au-delà de `reports.max_anomaly_rows_excel` lignes, l'onglet d'anomalies garde un
extrait et le détail intégral part dans un CSV à côté du classeur.

## Commandes

```bash
# Premier chargement (historique complet), à lancer une fois avant d'activer la planification
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode initial

# Delta depuis le dernier run réussi — ce que lance la tâche planifiée
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --mode incremental

# Essai hors ligne sur un fichier local : ni email, ni SharePoint, ni état incrémental
python -m e11_rdcc.run_pipeline --config e11_rdcc/config/E11_RDCC.yaml --input tests/fixtures/e11_rdcc_sample.csv

# Application des corrections métier (onglet Instructions : Champ | Input | Label_Attendu)
python -m e11_rdcc.apply_corrections --file "<OUTPUT_BASE>\E11_RDCC\E11_RDCC_classification.xlsx"

# Extraction ad hoc : requête SQL, table ou fichier local, sortie CSV, sans effet de bord
python -m e11_rdcc.ad_hoc_extraction --config e11_rdcc/config/E11_RDCC.yaml \
    --query "SELECT TOP 100 * FROM [DATAWAREHOUSE_SA_PROD].[dbo].[E11EtatBcmReleveDesComptesCorrespondants]"
```

`--dry-run` supprime l'email et l'envoi SharePoint, mais écrit quand même le classeur et
enregistre l'état : ce n'est pas un essai sans effet.

## Réglages du YAML

| Clé | Effet |
|---|---|
| `columns.ref_banque` | Axe de regroupement des outliers dans les rapports |
| `fields[].referentiel_path` | Référentiel du champ |
| `fields[].llm` | Modèle, taille de lot, nombre de lots en parallèle (`concurrency`), plafond de valeurs nouvelles par run (`max_values_per_run`) |
| `tolerance.absolute` | Tolérance de la règle arithmétique |
| `grouping_key_temporal_continuity` | Clé de regroupement du chaînage J / J+1 |
| `load.chunk_size`, `load.cast_max_text`, `load.retries` | Lecture SQL par paquets, lecture accélérée des colonnes `NVARCHAR(MAX)`, reprise sur coupure réseau |
| `reports.top_n_outliers_detail`, `reports.max_anomaly_rows_excel` | Volume du PDF et bascule CSV des gros onglets d'anomalies |
| `email.display_name` | Libellé de l'endpoint dans le sujet et le corps de l'email |

## Référentiels et caches

`e11_rdcc/referentiel/` contient le référentiel ISO 4217 (`devise_referentiel.json`), le
référentiel BCM des correspondants (`nomcorrespondant_referentiel_E11.json`) et les caches
warm-start `validated_classif_{champ}_e11_rdcc.json`. Les caches sont versionnés : ils portent la
connaissance déjà validée et évitent de repayer les mêmes résolutions Claude à chaque run. Les
corrections manuelles y sont écrites par `apply_corrections` et ont priorité sur toute la cascade.

## Prérequis

- `.env` renseigné : accès base en lecture seule, `ANTHROPIC_API_KEY` (fallback Claude de
  `NomCorrespondant`), SMTP, `OUTPUT_BASE`, `STATE_DIR`.
- État incrémental : `{STATE_DIR}/E11_RDCC_run_state.json` (défaut `state/`).

## Tests

```bash
python -m pytest tests/test_e11_reports.py tests/test_numeric_rules.py tests/test_global_na.py tests/test_devise.py -q
```
