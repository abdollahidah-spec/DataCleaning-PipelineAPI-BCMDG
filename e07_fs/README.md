# E07_FS — Flux sortants

Pipeline de nettoyage, normalisation et validation de l'endpoint **E07 — FS** : 7 champs
catégoriels et un moteur de validation des transactions, en un seul run et un seul jeu de
livrables.

## Source

| | |
|---|---|
| Table | `E7EtatBcmFluxSortants` |
| Colonne d'horodatage | `dtCr` |
| Accès | lecture seule, aucune écriture ni DDL |
| Historique | borné aux lignes créées depuis le **01/01/2024** (`load.initial_since`) |

Volumétrie relevée en base : 1 010 293 lignes au total, dont 981 846 depuis 2024.

## Rôle de chaque colonne

| Rôle | Colonnes |
|---|---|
| Normalisées | `TypeSwfit`, `ModeReglement`, `Devise`, `NomDonneurOrdre`, `Beneficiaire`, `NatureEconomique`, `Pays` |
| Validées | `MontantTransaction`, `TauxDeChange`, `DateTransaction` |
| Conservées sans normalisation | `ReferenceTransaction` (témoin de la règle NA et discriminant des messages sans activité), `NifNni` (rapprochement DGI), `SourceDevise` (décision métier), `RefBanque` (champ « banque » du ticket, axe de regroupement des rapports) |
| Hors périmètre | `Produit` (décision métier, non normalisé, uniquement contrôlé par le gabarit « sans activité ») ; `FkId`, `idSysLog`, `fkidSyncOperation`, `Id`, `Banque` (techniques) |

La table porte également une colonne `Banque` (nom complet de la banque) ; `RefBanque` (code)
sert d'axe de regroupement, la correspondance entre les deux étant constante.

## Champs traités

| Champ | Traitement |
|---|---|
| `TypeSwfit` | Codes SWIFT valides des **flux sortants** (`valid_fs`), comparés sans espaces. Un numéro seul à 3 chiffres (« 103 ») est préfixé en `MT 103` s'il est valide ; bruit connu et valeurs ressemblant à un montant → `OUTLIER`. L'orthographe `TypeSwfit` est celle du schéma source |
| `ModeReglement` | Codes `CD`, `RD`, `TL`, alias (`TR` → `TL`), bruit connu |
| `Devise` | Cascade déterministe ISO 4217, identique aux autres endpoints |
| `NomDonneurOrdre` | Donneur d'ordre LOCAL : cache → valeur déjà conforme au référentiel (`MAP_CIBLE`) → référentiel → NIF exact (`NifNni` contre la base fiscale DGI) → entreprise publique → outlier évident → classification locale → rapprochement DGI (exact normalisé, puis flou tranché en match net, `OUTLIER`, ou arbitrage Claude) |
| `Beneficiaire` | Bénéficiaire ÉTRANGER : pas de base fiscale pertinente → cache → `MAP_CIBLE` → référentiel → entreprise publique → classification locale → Claude avec recherche web réelle |
| `NatureEconomique` | Référentiel des flux sortants (14 catégories, 78 libellés) + mapping direct + pré-filtre des outliers évidents (nom de personne, adresse, nombre, date, modalité vague) → Claude sur la **liste fermée** des libellés. Une colonne `NatureEconomique_Categorie` est déduite du libellé retenu |
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

Un message est considéré sans activité **si et seulement si** `ReferenceTransaction` vaut `NA` :
une vraie transaction porte toujours une référence. Le gabarit attendu est alors celui du ticket :

```json
{ "banque":"string", "referenceTransaction":"NA", "dateTransaction":"2024-04-27",
  "typeSwfit":"NA", "modeReglement":"NA", "devise":"NA", "montantTransaction":0,
  "tauxDeChange":0, "nomDonneurOrdre":"NA", "nifNni":"NA", "sourceDevise":"NA",
  "beneficiaire":"NA", "produit":"NA", "natureEconomique":"NA", "pays":"NoAs" }
```

Les colonnes contrôlées sont listées dans `no_activity.template_na_columns` : une colonne absente
des données est ignorée, et celles qui ne sont normalisées par aucun champ (`SourceDevise`,
`Produit`) sont tout de même rapatriées par la requête pour ce contrôle.

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
| `E07_FS_classification.xlsx` | Un onglet par champ catégoriel (table de classification cumulative : référentiel + cache + corrections, indépendante du delta traité), `Anomalies_Transactions`, `Instructions` (historique des corrections, `Champ \| Input \| Label_Attendu`) |
| `Rapport/Rapport_Qualite_Outliers_E07_FS_{AAAAMMJJ}.pdf` | Rapport de qualité du run + rapport des outliers par champ et par `RefBanque`, chaque règle de validation étant rattachée à son champ |
| Email | Indicateurs du run en liste à puces, les deux livrables en pièce jointe |

Destination : `{OUTPUT_BASE}/E07_FS/`, ou `e07_fs/outputs/` si `OUTPUT_BASE` est vide. Chemin
stable, écrasé à chaque run. Au-delà de `reports.max_anomaly_rows_excel` lignes, le détail
intégral des anomalies part dans un CSV à côté du classeur.

## Commandes

```bash
# Premier chargement (depuis le 01/01/2024), à lancer une fois avant d'activer la planification
python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml --mode initial

# Delta depuis le dernier run réussi — ce que lance la tâche planifiée
python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml --mode incremental

# Essai hors ligne sur un fichier local : ni email, ni SharePoint, ni état incrémental
python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml --input tests/fixtures/e07_fs_sample.csv

# Application des corrections métier (onglet Instructions : Champ | Input | Label_Attendu)
python -m e07_fs.apply_corrections --file "<OUTPUT_BASE>\E07_FS\E07_FS_classification.xlsx"

# Extraction ad hoc : requête SQL, table ou fichier local, sortie CSV, sans effet de bord
python -m e07_fs.ad_hoc_extraction --config e07_fs/config/E07_FS.yaml \
    --query "SELECT TOP 100 * FROM [DATAWAREHOUSE_SA_PROD].[dbo].[E7EtatBcmFluxSortants]"
```

Un fichier passé à `--input` n'a pas besoin d'être en UTF-8 : l'encodage (cp1252, latin-1) et le
séparateur réels sont détectés, avec un avertissement dans les logs. `--dry-run` supprime l'email
et l'envoi SharePoint, mais écrit quand même le classeur et enregistre l'état.

## Réglages du YAML

| Clé | Effet |
|---|---|
| `fields[].flux` (`TypeSwift`) | `FS` sélectionne la liste `valid_fs` du référentiel SWIFT |
| `fields[].columns.nif_nni` | Colonne NIF/NNI de `NomDonneurOrdre`, active le rapprochement par NIF exact |
| `matching.*` | Seuils du rapprochement DGI : 92 / 8 / 70 / 300 |
| `matching.cache_resolutions` | Mémorise les résolutions DGI déterministes avec une empreinte de la base DGI : le rapprochement n'est pas refait au run suivant |
| `llm.*` | Modèle, taille de lot, lots en parallèle (`concurrency`), plafond de valeurs nouvelles par run (`max_values_per_run`), `web_search_max_uses` pour `Beneficiaire` |
| `date_inclusive` | `false` (défaut) : une transaction datée du jour de `dtCr` est une anomalie |
| `no_activity.template_na_columns`, `no_activity.pays_value` | Gabarit du message sans activité |
| `load.initial_since`, `load.chunk_size`, `load.cast_max_text`, `load.retries` | Borne d'historique, lecture par paquets, lecture accélérée des colonnes `NVARCHAR(MAX)`, reprise sur coupure réseau |

## Référentiels et caches

`e07_fs/referentiel/` contient les référentiels (SWIFT, modes de règlement, devises, nature
économique FS, donneurs d'ordre, bénéficiaires, mots-clés d'adresse) et les caches warm-start
`validated_classif_{champ}_e07_fs.json`. Ces données sont reprises du traitement par champ
antérieur : les caches sont donc déjà « chauds » (1 075 modalités pour la nature économique,
par exemple). Les corrections manuelles y sont écrites par `apply_corrections` et priment sur
toute la cascade.

## Prérequis

- `.env` renseigné : accès base en lecture seule, `ANTHROPIC_API_KEY`, SMTP, `OUTPUT_BASE`,
  `STATE_DIR`, `DGI_BASE_PATH` / `PUBLIC_ENT_PATH` (fichiers externes non versionnés, dans `req/`).
- Dépendances : `rapidfuzz`, `pycountry`, `babel`, `geonamescache`.
- État incrémental : `{STATE_DIR}/E07_FS_run_state.json` (défaut `state/`).

## Points de coût du premier chargement

| Étape | Coût mesuré |
|---|---|
| Lecture de la table | quelques minutes, colonnes `NVARCHAR(MAX)` lues en `NVARCHAR(4000)` |
| Rapprochement DGI (`NomDonneurOrdre`) | ~25 min pour 8 000 libellés nouveaux, puis mémorisé |
| `Beneficiaire` (recherche web) | 1 771 valeurs nouvelles, soit 355 appels : ~45 à 90 min avec 4 lots en parallèle |

Il s'agit d'une dépense unique : les résolutions sont mises en cache et les runs incrémentaux
suivants ne traitent que les valeurs nouvelles. Pour un essai rapide, `llm.max_values_per_run`
borne le nombre de valeurs envoyées par run.

## Tests

```bash
python -m pytest tests/test_e07_fields.py tests/test_e07_transactions.py tests/test_e07_reports.py tests/test_e07_map_cible.py tests/test_ad_hoc_extraction_e07.py -q
```
