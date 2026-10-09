# E10_FE — Flux entrants

Pipeline de nettoyage, normalisation et validation de l'endpoint **E10 — FE** : 7 champs
catégoriels et un moteur de validation des transactions, en un seul run et un seul jeu de
livrables. Structure identique à E07, avec les données propres aux flux entrants et les deux
champs d'entités inversés.

## Source

| | |
|---|---|
| Table | `E10EtatBcmFluxEntrants` |
| Colonne d'horodatage | `dtCr` |
| Accès | lecture seule, aucune écriture ni DDL |
| Historique | borné aux lignes créées depuis le **01/01/2024** (`load.initial_since`) |

Volumétrie relevée en base : 3 000 818 lignes au total, dont 2 980 497 depuis 2024. Un chargement
initial complet, sorties et rapport compris, prend environ 12 minutes.

## Rôle de chaque colonne

| Rôle | Colonnes |
|---|---|
| Normalisées | `TypeSwfit`, `ModeReglement`, `Devise`, `NomDonneurOrdre`, `Beneficiaire`, `NatureEconomique`, `Pays` |
| Validées | `MontantTransaction`, `TauxDeChange`, `DateTransaction` |
| Conservées sans normalisation | `ReferenceTransaction` (témoin de la règle NA et discriminant des messages sans activité), `NifNni` (rapprochement DGI du bénéficiaire), `RefBanque` (champ « banque » du ticket, axe de regroupement des rapports) |
| Hors périmètre | `Produit` (décision métier, non normalisé, uniquement contrôlé par le gabarit « sans activité ») ; `Id`, `Banque`, `FkId`, `idSysLog`, `fkidSyncOperation` (techniques) |

**La table ne contient pas de colonne `SourceDevise`**, contrairement au ticket et à E07 : elle
est donc absente du gabarit « sans activité ».

## Sens du flux : entités inversées par rapport à E07

| Champ | E10 — flux entrants | E07 — flux sortants |
|---|---|---|
| `NomDonneurOrdre` | émetteur **étranger** : Claude + recherche web | donneur d'ordre local : rapprochement DGI |
| `Beneficiaire` | bénéficiaire **local** : rapprochement DGI avec `NifNni` | bénéficiaire étranger : Claude + recherche web |

## Champs traités

| Champ | Traitement |
|---|---|
| `TypeSwfit` | Codes SWIFT valides des **flux entrants** (`valid_fe`, `pacs.004` inclus), comparés sans espaces. Un numéro seul à 3 chiffres est préfixé en `MT …` s'il est valide ; bruit connu et valeurs ressemblant à un montant → `OUTLIER`. L'orthographe `TypeSwfit` est celle du schéma source |
| `ModeReglement` | Codes `CD`, `RD`, `TL`, alias (`TR` → `TL`), bruit connu |
| `Devise` | Cascade déterministe ISO 4217, identique aux autres endpoints |
| `NomDonneurOrdre` | Cache → valeur déjà conforme au référentiel (`MAP_CIBLE`) → référentiel E10 (12 289 correspondances) → entreprise publique → outlier évident → classification locale → Claude avec recherche web réelle. Sur l'historique depuis 2024, le référentiel et le cache couvrent la totalité des 15 245 valeurs distinctes : aucun appel n'est nécessaire |
| `Beneficiaire` | Cache → `MAP_CIBLE` → référentiel E10 (7 574 correspondances) → NIF exact (`NifNni` contre la base fiscale DGI) → entreprise publique → outlier évident → classification locale → rapprochement DGI (exact normalisé, puis flou tranché en match net, `OUTLIER`, ou arbitrage Claude entre candidats proches) |
| `NatureEconomique` | Référentiel des flux entrants (14 catégories, 78 libellés) + mapping direct FE + pré-filtre des outliers évidents → Claude sur la **liste fermée** des libellés. Une colonne `NatureEconomique_Categorie` est déduite du libellé retenu. Les entrées héritées valant « NON CLASSE », catégorie supprimée, sont lues comme `OUTLIER` |
| `Pays` | Référentiel géographique construit en code (pycountry, babel, geonamescache) + alias + mots-clés d'adresse → Claude sur la liste fermée des codes ISO-2. Règle `NoAs`/`OUTLIER` propre à ce champ, le code ISO de la Namibie étant littéralement `NA` |

## Règles de validation des transactions

| Règle | Contrôle | Champ du rapport |
|---|---|---|
| `AMOUNT_NON_NEGATIVE` | `MontantTransaction` numérique et **supérieur ou égal à 0** | montantTransaction |
| `RATE_NON_NEGATIVE` | `TauxDeChange` numérique et **supérieur ou égal à 0**. Environ 1 % des lignes ont un taux vide : signalé comme valeur non numérique | tauxDeChange |
| `DATE_VALIDITY` | `DateTransaction` parsable et **strictement antérieure** à `dtCr`, comparaison au jour. `date_inclusive: true` autorise le même jour que `dtCr` | dateTransaction |
| `NO_ACTIVITY_CONFORMITY` | Conformité du gabarit « sans activité » | referenceTransaction |

Une ligne en échec sur plusieurs règles produit une ligne d'anomalie par règle, dans l'onglet
`Anomalies_Transactions`.

## Message « sans activité »

Un message est considéré sans activité **si et seulement si** `ReferenceTransaction` vaut `NA`.
Les champs texte du gabarit doivent alors valoir `NA`, `Pays` doit valoir `NoAs`, et le montant
comme le taux doivent être à 0. Les colonnes contrôlées sont listées dans
`no_activity.template_na_columns` (sans `SourceDevise`, absente de cette table) ; une colonne
absente des données est ignorée, et `Produit`, qui n'est normalisé par aucun champ, est tout de
même rapatriée par la requête pour ce contrôle.

## Règle NA

```
Champ == 'NA' ET ReferenceTransaction == 'NA'   -> 'NA'        (message sans activité)
Champ == 'NA' ET ReferenceTransaction != 'NA'   -> OUTLIER     (NA suspecte)
Champ vide / NULL                               -> OUTLIER
```

`Pays` garde sa règle `NoAs` dédiée, décrite plus haut.

## Livrables

| Livrable | Contenu |
|---|---|
| `E10_FE_classification.xlsx` | Un onglet par champ catégoriel (table de classification cumulative : référentiel + cache + corrections, indépendante du delta traité), `Anomalies_Transactions`, `Instructions` (historique des corrections, `Champ \| Input \| Label_Attendu`) |
| `Rapport/Rapport_Qualite_Outliers_E10_FE_{AAAAMMJJ}.pdf` | Rapport de qualité du run + rapport des outliers par champ et par `RefBanque`, chaque règle de validation étant rattachée à son champ |
| Email | Indicateurs du run en liste à puces, les deux livrables en pièce jointe |

Destination : `{OUTPUT_BASE}/E10_FE/`, ou `e10_fe/outputs/` si `OUTPUT_BASE` est vide. Chemin
stable, écrasé à chaque run. Au-delà de `reports.max_anomaly_rows_excel` lignes, le détail
intégral des anomalies part dans un CSV à côté du classeur.

## Commandes

```bash
# Premier chargement (depuis le 01/01/2024), à lancer une fois avant d'activer la planification
python -m e10_fe.run_pipeline --config e10_fe/config/E10_FE.yaml --mode initial

# Delta depuis le dernier run réussi — ce que lance la tâche planifiée
python -m e10_fe.run_pipeline --config e10_fe/config/E10_FE.yaml --mode incremental

# Essai hors ligne sur un fichier local : ni email, ni SharePoint, ni état incrémental
python -m e10_fe.run_pipeline --config e10_fe/config/E10_FE.yaml --input tests/fixtures/e10_fe_sample.csv

# Application des corrections métier (onglet Instructions : Champ | Input | Label_Attendu)
python -m e10_fe.apply_corrections --file "<OUTPUT_BASE>\E10_FE\E10_FE_classification.xlsx"

# Extraction ad hoc : requête SQL, table ou fichier local, sortie CSV, sans effet de bord
python -m e10_fe.ad_hoc_extraction --config e10_fe/config/E10_FE.yaml \
    --query "SELECT TOP 100 * FROM [DATAWAREHOUSE_SA_PROD].[dbo].[E10EtatBcmFluxEntrants]"
```

Un fichier passé à `--input` n'a pas besoin d'être en UTF-8 : l'encodage (cp1252, latin-1) et le
séparateur réels sont détectés, avec un avertissement dans les logs. `--dry-run` supprime l'email
et l'envoi SharePoint, mais écrit quand même le classeur et enregistre l'état.

## Réglages du YAML

| Clé | Effet |
|---|---|
| `fields[].flux` (`TypeSwift`) | `FE` sélectionne la liste `valid_fe` du référentiel SWIFT |
| `fields[].columns.nif_nni` (`Beneficiaire`) | Active le rapprochement par NIF exact du bénéficiaire local |
| `matching.*` (`Beneficiaire`) | Seuils du rapprochement DGI : 92 / 8 / 70 / 300 |
| `matching.cache_resolutions` | Mémorise les résolutions DGI déterministes avec une empreinte de la base DGI : le rapprochement n'est pas refait au run suivant |
| `llm.*` | Modèle, taille de lot, lots en parallèle (`concurrency`), plafond de valeurs nouvelles par run (`max_values_per_run`), `web_search_max_uses` pour `NomDonneurOrdre` |
| `date_inclusive` | `false` (défaut) : une transaction datée du jour de `dtCr` est une anomalie |
| `no_activity.template_na_columns`, `no_activity.pays_value` | Gabarit du message sans activité |
| `load.initial_since`, `load.chunk_size`, `load.cast_max_text`, `load.retries` | Borne d'historique, lecture par paquets, lecture accélérée des colonnes `NVARCHAR(MAX)`, reprise sur coupure réseau |

## Référentiels et caches

`e10_fe/referentiel/` contient les référentiels (SWIFT, modes de règlement, devises, nature
économique FE, donneurs d'ordre E10, bénéficiaires E10, mots-clés d'adresse) et les caches
warm-start `validated_classif_{champ}_e10_fe.json`. Ces données sont reprises du traitement par
champ antérieur, les caches sont donc déjà « chauds ». Les corrections manuelles y sont écrites
par `apply_corrections` et priment sur toute la cascade.

## Prérequis

- `.env` renseigné : accès base en lecture seule, `ANTHROPIC_API_KEY`, SMTP, `OUTPUT_BASE`,
  `STATE_DIR`, `DGI_BASE_PATH` / `PUBLIC_ENT_PATH` (fichiers externes non versionnés, dans `req/`).
- Dépendances : `rapidfuzz`, `pycountry`, `babel`, `geonamescache`.
- État incrémental : `{STATE_DIR}/E10_FE_run_state.json` (défaut `state/`).

## Points de coût du premier chargement

| Étape | Coût mesuré |
|---|---|
| Lecture de 2,98 M de lignes | ~5 à 6 min, colonnes `NVARCHAR(MAX)` lues en `NVARCHAR(4000)` |
| Traitement des 7 champs (en parallèle) | ~4 min |
| Validation des transactions | ~1 min |
| Classeur et rapport PDF | ~20 s |
| Appels Claude | 1 appel pour la nature économique, 1 pour les pays, 67 appels d'arbitrage DGI (texte) |

Le rapprochement DGI du bénéficiaire est l'étape la plus lourde en cas de volume de libellés
nouveaux ; il est mémorisé d'un run à l'autre. Les runs incrémentaux ne traitent que les valeurs
nouvelles.

## Tests

```bash
python -m pytest tests/test_e10_fields.py tests/test_e10_transactions.py tests/test_e10_reports.py tests/test_dgi_cache.py tests/test_ad_hoc_extraction_e10.py -q
```
