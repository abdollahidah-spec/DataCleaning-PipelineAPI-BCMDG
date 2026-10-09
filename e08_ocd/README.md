# E08_OCD — Ouvertures de crédits documentaires

Pipeline de nettoyage et normalisation de l'endpoint **E08 — OCD**, 6 champs catégoriels. C'est
l'endpoint qui réunit le plus de familles de traitement : cascade déterministe, liste fermée
soumise à Claude, rapprochement contre la base fiscale DGI et recherche web.

## Source

| | |
|---|---|
| Table | `E8EtatBcmOuvertureCreditDocumentaires` |
| Colonne d'horodatage | `dtCr` |
| Accès | lecture seule, aucune écriture ni DDL |
| Historique | intégral (aucune borne `load.initial_since`) |

## Champs traités

| Champ | Famille | Traitement |
|---|---|---|
| `Devise` | déterministe | Bruit connu → code ISO 4217 exact → code numérique → alias → nettoyage de repli |
| `NomCorrespondant` | référentiel + Claude | Cache → valeur déjà conforme au référentiel (`MAP_CIBLE`) → référentiel BCM validé → Claude (banque réelle avec code SWIFT/BIC publié, sinon `OUTLIER`) |
| `Produits` | référentiel + Claude sur liste fermée | Cache → libellé déjà conforme au référentiel → bruit connu → alias → Claude qui choisit **dans la liste fermée des libellés** du référentiel métier, ou répond `OUTLIER` : jamais de libellé inventé. Une colonne `Produit_Categorie` est déduite du libellé retenu |
| `NomDonneurOrdre` | rapprochement fiscal DGI | Cache → `MAP_CIBLE` → référentiel → NIF exact (`NifNni` contre la base DGI) → entreprise publique connue → outlier évident → classification locale (particulier, établissement) → rapprochement DGI : exact après hyper-normalisation, puis rapprochement flou (rapidfuzz) tranché en match net, `OUTLIER`, ou arbitrage Claude entre candidats proches |
| `Beneficiaire` | Claude + recherche web | Bénéficiaire ÉTRANGER d'un crédit documentaire import : pas de base fiscale mauritanienne pertinente. Cache → `MAP_CIBLE` → référentiel → entreprise publique → outlier évident → classification locale → Claude avec recherche web réelle, qui ne retient une raison sociale que si une source fiable la lie au libellé |
| `Pays` | référentiel géographique + Claude | Référentiel construit en code (pycountry, babel, geonamescache) + alias manuels + mots-clés d'adresse → Claude sur la liste fermée des codes ISO-3166-1 alpha-2 |

`NumCredoc` (référence du crédit documentaire) n'est pas normalisée : elle sert de témoin à la
règle NA de chaque champ, indépendamment.

## Règle NA

```
Champ == 'NA' ET NumCredoc == 'NA'   -> 'NA'        (ligne sans activité)
Champ == 'NA' ET NumCredoc != 'NA'   -> OUTLIER     (NA suspecte)
Champ vide / NULL                    -> OUTLIER
```

Témoin simple par champ : aucun gabarit « sans activité » multi-champs n'est défini pour E08,
contrairement à E11, E07 et E10.

## Méthodes de résolution

La colonne `{champ}_method` trace la façon dont chaque valeur a été résolue, et sépare ce qui
était **déjà connu avant le run** de ce qui a été tranché **pendant** le run — c'est cette
distinction qui alimente les indicateurs « nouvelles valeurs » de l'email.

| Déjà connu avant le run | Tranché pendant le run |
|---|---|
| `WARM` (cache), `MAP`, `MAP_CIBLE`, `ALIAS`, `NUM`, `STRIP`, `NOISE`, `NIF_EXACT`, `PUBLIC_ENT`, `DGI_EXACT_NORM`, `DGI_FUZZY_STRONG` | `CLAUDE`, `DGI_CLAUDE_ARBITRAGE`, `DGI_NO_MATCH`, `PARTICULIER`, `ETS_PERSONNEL`, `ETS_OUTLIER`, `OUTLIER` |

Les méthodes `PARTICULIER` et `ETS_*` sont volontairement classées « nouvelles » : ce sont des
déductions par mots-clés sur une valeur jamais validée par un humain ni trouvée dans une source
de référence.

## Livrables

| Livrable | Contenu |
|---|---|
| `E08_OCD_classification.xlsx` | Un onglet par champ (table de classification cumulative : référentiel + cache + corrections, indépendante du delta traité), `Instructions` (historique des corrections, `Champ \| Input \| Label_Attendu`) |
| `Rapport/Rapport_Qualite_Outliers_E08_OCD_{AAAAMMJJ}.pdf` | Rapport de qualité du run + rapport des outliers par champ et par `RefBanque` |
| Email | Indicateurs du run en liste à puces, les deux livrables en pièce jointe |

Destination : `{OUTPUT_BASE}/E08_OCD/`, ou `e08_ocd/outputs/` si `OUTPUT_BASE` est vide. Chemin
stable, écrasé à chaque run.

## Commandes

```bash
# Premier chargement (historique complet), à lancer une fois avant d'activer la planification
python -m e08_ocd.run_pipeline --config e08_ocd/config/E08_OCD.yaml --mode initial

# Delta depuis le dernier run réussi — ce que lance la tâche planifiée
python -m e08_ocd.run_pipeline --config e08_ocd/config/E08_OCD.yaml --mode incremental

# Essai hors ligne sur un fichier local : ni email, ni SharePoint, ni état incrémental
python -m e08_ocd.run_pipeline --config e08_ocd/config/E08_OCD.yaml --input tests/fixtures/e08_ocd_sample.csv

# Application des corrections métier (onglet Instructions : Champ | Input | Label_Attendu)
python -m e08_ocd.apply_corrections --file "<OUTPUT_BASE>\E08_OCD\E08_OCD_classification.xlsx"
```

`--dry-run` supprime l'email et l'envoi SharePoint, mais écrit quand même le classeur et
enregistre l'état. L'extraction ad hoc n'est pas portée pour cet endpoint (disponible sur E11,
E07 et E10).

## Réglages du YAML

| Clé | Effet |
|---|---|
| `fields[].referentiel_path` | Référentiel du champ. `Produits` s'appuie sur un référentiel converti depuis `req/DataCleaning_E08 OCD_Produits.xlsx` ; `Pays` pointe un fichier de mots-clés d'adresse, le reste du référentiel étant construit en code |
| `columns.nif_nni` | Colonne NIF/NNI de `NomDonneurOrdre` ; absente des données, l'étape de NIF exact est simplement sautée |
| `matching.*` | Seuils du rapprochement DGI : `strong_threshold` (92), `strong_gap` (8), `arbitrage_min` (70), `batch_size` (300, borne la mémoire face aux ~52 000 raisons sociales) |
| `matching.cache_resolutions` | Mémorise les résolutions DGI déterministes dans `{STATE_DIR}`, avec une empreinte de la base DGI : le rapprochement n'est pas refait au run suivant |
| `llm.*` | Modèle, taille de lot, lots en parallèle (`concurrency`), plafond de valeurs nouvelles par run (`max_values_per_run`), `web_search_max_uses` pour `Beneficiaire` |
| `load.*`, `reports.*`, `email.display_name` | Lecture SQL, volume du PDF, libellé de l'endpoint dans l'email |

## Référentiels et caches

`e08_ocd/referentiel/` contient les référentiels métier (correspondants, donneurs d'ordre,
bénéficiaires, produits, devises, mots-clés d'adresse) et les caches warm-start
`validated_classif_{champ}_e08_ocd.json`. Les caches sont versionnés et déjà « chauds » : repris
de l'historique de traitement, ils évitent de repayer les mêmes résolutions Claude. Les
corrections manuelles y sont écrites par `apply_corrections` et priment sur toute la cascade.

## Prérequis

- `.env` renseigné : accès base en lecture seule, `ANTHROPIC_API_KEY`, SMTP, `OUTPUT_BASE`,
  `STATE_DIR`, et surtout `DGI_BASE_PATH` / `PUBLIC_ENT_PATH` (fichiers externes non versionnés,
  dans `req/`). `DGI_BASE_PATH` absent provoque une erreur explicite au chargement ;
  `PUBLIC_ENT_PATH` absent dégrade proprement, sans entreprise publique reconnue.
- Dépendances : `rapidfuzz` (rapprochement flou), `pycountry`, `babel`, `geonamescache` (Pays).
- État incrémental : `{STATE_DIR}/E08_OCD_run_state.json` (défaut `state/`).

## Points de coût

`Beneficiaire` déclenche une recherche web facturée par valeur nouvelle, et le rapprochement DGI
compare chaque libellé nouveau à l'ensemble de la base fiscale. Les deux sont bornés par le
cache, par `llm.max_values_per_run` et par la mémoire des résolutions DGI. Le premier chargement
reste la seule exécution réellement coûteuse ; les runs incrémentaux ne traitent que les valeurs
nouvelles.

## Tests

```bash
python -m pytest tests/test_e08_reports.py tests/test_produits.py tests/test_nomdonneurordre.py tests/test_beneficiaire.py tests/test_pays.py tests/test_entity_matching.py tests/test_map_cible_e08_e11.py -q
```
