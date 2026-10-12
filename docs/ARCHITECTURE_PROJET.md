# Nettoyage et normalisation des données des Banques primaires

## 1. Objet

Les banques primaires déclarent quotidiennement leurs opérations à la BCM à travers un ensemble
d'**APIs de déclaration**, une par type d'état réglementaire : flux sortants, flux entrants,
ouvertures de crédits documentaires, prévisions d'échéances, relevés des comptes correspondants.
Chaque API alimente sa propre table dans la **base de collecte** de la BCM. C'est cette base,
et non un entrepôt de données consolidé, qui constitue la source du traitement.

Les champs de ces déclarations sont saisis librement par les opérateurs des banques : une même
devise, un même pays ou une même raison sociale y apparaissent sous des dizaines d'orthographes,
avec des abréviations, des fautes, du bruit de saisie et des valeurs hors sujet. En l'état, ces
données ne sont exploitables ni pour la restitution, ni pour les traitements en aval.

Le traitement produit, pour chaque champ, une **mapping table** : la liste des **valeurs uniques
rencontrées** dans les données, chacune associée à sa **valeur propre** telle que résolue par le
pipeline. Les valeurs qu'aucune règle ne permet de rattacher sont isolées comme `OUTLIER` et
soumises à validation métier ; une fois validées, elles rejoignent la mémoire du pipeline et ne
sont plus à retraiter. La mapping table est donc **réactualisée au fil des exécutions**, et
constitue le livrable exploitable par la restitution et par les traitements en aval.

Deux chaînes produisent ces mapping tables, selon deux axes de lancement :

| Chaîne | Ce qu'une commande traite | Dépôt |
|---|---|---|
| **Pipeline par champ** | un champ, sur **toutes** les APIs où il existe, ou sur **une seule** API | `DataCleaning-PipelineField-BCMDG` |
| **Pipeline par API** | **tous** les champs d'une API, en une seule exécution | `DataCleaning-PipelineAPI-BCMDG` |

Les deux chaînes appliquent les mêmes règles métier et alimentent la même mémoire de valeurs
validées. Elles diffèrent par l'unité de lancement et par la forme des livrables.

---

<!-- saut-de-page -->

## 2. Périmètre : champs et APIs

**9 champs** traités, sur les **5 APIs de déclaration**, soit **23 couples champ/API** :

| Champ | E07 | E08 | E09 | E10 | E11 |
|---|---|---|---|---|---|
| Devise | oui | oui | oui | oui | oui |
| Pays | oui | oui | — | oui | — |
| NomDonneurOrdre | oui | oui | — | oui | — |
| Beneficiaire | oui | oui | — | oui | — |
| NomCorrespondant | — | oui | — | — | oui |
| NatureEconomique | oui | — | — | oui | — |
| TypeSwift | oui | — | — | oui | — |
| ModeReglement | oui | — | — | oui | — |
| Produits | — | oui | — | — | — |

`Produits` n'est traité que par la chaîne par API. La chaîne par champ en couvre donc 8, soit 22 couples.

**APIs de déclaration et tables alimentées :**

| API de déclaration | Objet | Table | Volumétrie (lignes) |
|---|---|---|---|
| E07 — FS | flux sortants | `E7EtatBcmFluxSortants` | env. 1,0 M |
| E08 — OCD | ouvertures de crédits documentaires | `E8EtatBcmOuvertureCreditDocumentaires` | env. 13 k |
| E09 — PE | prévisions d'échéances | `E9EtatBcmPrevisionEcheances` | env. 5,1 M |
| E10 — FE | flux entrants | `E10EtatBcmFluxEntrants` | env. 3,0 M |
| E11 — RDCC | relevés des comptes correspondants | `E11EtatBcmReleveDesComptesCorrespondants` | env. 350 k |

Les tables sont lues en lecture seule stricte : aucune écriture, aucun DDL.

Outre les champs normalisés, trois endpoints portent des champs **numériques ou de date** qui ne
se normalisent pas et sont soumis à des règles de contrôle produisant un rapport d'anomalies :
soldes et dates de relevé (E11, 4 règles dont un chaînage J / J+1), montant et date d'échéance
(E09, 2 règles), montant, taux de change, date de transaction et conformité des messages sans
activité (E07 et E10, 4 règles).

<!-- saut-de-page -->

## 3. Nature des mapping tables

### 3.1 Contenu

Une mapping table est une relation **n → 1** entre les valeurs brutes et la valeur normalisée :

| Colonne | Contenu |
|---|---|
| valeur brute | valeur unique telle que saisie, après déduplication |
| valeur normalisée | valeur propre retenue, issue du référentiel métier |

Exemple, champ Devise :

```
USD           -> USD
dollar US     -> USD
840           -> USD
US DOLLAR     -> USD
EURO          -> EUR
978           -> EUR
XYZ           -> OUTLIER
```

Les valeurs non rattachables portent `OUTLIER` et sont isolées avec leur poids : nombre
d'occurrences, ratio, et répartition par banque. C'est le matériau de la validation métier.

### 3.2 Ce qui alimente une mapping table

Trois sources, par ordre de priorité décroissante :

1. **les corrections métier validées** — une valeur tranchée par le métier prime sur toute règle ;
2. **le référentiel métier du champ** — codes ISO 4217, codes SWIFT par sens de flux, modes de
   règlement, catégories et libellés de nature économique, raisons sociales des entités, codes
   pays, libellés de produits ;
3. **la résolution automatique** — règles déterministes du champ, rapprochement contre la base
   fiscale, ou modèle de langage contraint à choisir dans une liste fermée de valeurs de
   référence.

### 3.3 Cycle de réactualisation

```
exécution -> mapping table + liste des OUTLIERS
          -> validation métier des OUTLIERS (onglet Instructions)
          -> application des corrections : enrichissement de la mémoire du champ
          -> exécution suivante : la valeur sort déjà normalisée, sans nouveau calcul
```

La mémoire de chaque champ (`validated_classif_{champ}_{api}.json`, 14 fichiers côté pipeline par
champ) contient les modalités déjà tranchées. Elle est consultée avant toute autre étape, ce qui
rend chaque exécution plus rapide et moins coûteuse que la précédente : seules les valeurs
**nouvelles** sont à résoudre.

La mapping table livrée par le pipeline par API est **cumulative** : elle contient la
connaissance complète du champ — référentiel, mémoire validée et corrections — et non les seules
valeurs du lot traité. Une modalité vue lors d'une exécution précédente reste donc présente,
même si elle n'apparaît pas dans les données du jour.

<!-- saut-de-page -->

## 4. Pipeline par champ

### 4.1 Principe

L'unité de lancement est le **champ**. Une commande applique la chaîne complète de ce champ —
nettoyage, référentiel, résolution, isolement des outliers — et produit sa mapping table
réactualisée. Le champ est traité soit sur **toutes** les APIs où il existe, soit sur **une
seule**.

Le dépôt est organisé par champ : un dossier par champ, portant sa logique de normalisation, son
référentiel, sa mémoire de valeurs validées et une configuration par API.

```
devise/
  config/E07_FS.yaml  E08_OCD.yaml  E09_PE.yaml  E10_FE.yaml  E11_RDCC.yaml
  normalize_devise.py        logique de normalisation du champ
  pipeline_devise.py         point d'entrée : une API, ou toutes
  referentiel/               référentiel ISO 4217 + mémoire des valeurs validées
```

### 4.2 Commandes

| Action | Commande |
|---|---|
| Nettoyer un champ sur **toutes** ses APIs | `python devise/pipeline_devise.py --all --config-dir devise/config/ --workers 4` |
| Nettoyer un champ sur **une** API | `python devise/pipeline_devise.py --config devise/config/E07_FS.yaml` |
| Réutiliser la mémoire des valeurs déjà validées | ajouter `--warm-start` |
| Traiter un extrait local au lieu de la base | ajouter `--input data/extrait.csv` |
| Exécution périodique | ajouter `--schedule 7` (intervalle en jours) |
| Appliquer les corrections métier | `python nomdonneurordre/apply_corrections.py --file <fichier mapping>` |
| Reconstruire un référentiel depuis un fichier métier | `python nomdonneurordre/build_referentiel.py` |

Le mode `--all` exécute les APIs du champ **en parallèle** (`--workers`). Les corrections et la
reconstruction de référentiel existent pour les champs qui en ont besoin : NomDonneurOrdre,
Beneficiaire et NomCorrespondant.

### 4.3 Livrables, par couple champ/API

| Fichier | Contenu |
|---|---|
| `{API}_mapping_outliers_{horodatage}.xlsx` | onglet `Mapping_Clean` : la mapping table, valeurs uniques résolues, relation n → 1, triée ; onglet `Analyse_Outliers` : valeurs non rattachées avec nombre d'occurrences, ratio, totaux par banque ; onglet `Instructions` : historique des corrections déjà appliquées |
| `{API}_extraction_{horodatage}.csv` | les lignes traitées, avec la colonne normalisée insérée à côté de la colonne brute |

Un horodatage dans le nom : chaque exécution conserve sa trace, les livrables ne s'écrasent pas.

### 4.4 Ce que cette chaîne apporte, et ses limites

Elle reste la référence métier : c'est là qu'ont été constitués et éprouvés les référentiels, les
cascades de résolution et la mémoire des valeurs validées, et c'est l'outil adapté à une
campagne ciblée sur un champ — ajout d'un référentiel, revalidation d'un champ sur l'ensemble des
APIs, comparaison du même champ d'une API à l'autre.

Ses limites, pour un usage de production récurrent : couvrir une API de 7 champs impose 7
lectures du même jeu de données ; la qualité d'une API n'existe nulle part comme un tout ; et il
faut recoller 22 livrables pour obtenir une vue d'ensemble.

<!-- saut-de-page -->

## 5. Pipeline par API

### 5.1 Principe

L'unité de lancement est l'**endpoint**. Une commande lit le jeu de données **une fois**, traite
**tous** les champs de cette API — y compris les contrôles numériques et de date — et retourne
l'ensemble des mapping tables dans un livrable unique, accompagné d'un rapport de qualité.

C'est la chaîne de production : elle ajoute le traitement par delta, la boucle de correction, la
planification et les notifications.

```
e07_fs/
  config/E07_FS.yaml       table source, champs, colonnes, seuils, livrables
  fields/*.py              un module par champ de cette API
  referentiel/*.json       référentiels et mémoire des valeurs validées
  pipeline.py              assemblage des champs depuis la configuration
  run_pipeline.py          point d'entrée d'une exécution
  apply_corrections.py     application des corrections métier
  ad_hoc_extraction.py     extraction sur requête libre
```

### 5.2 Commandes

| Action | Commande |
|---|---|
| Première exécution, sur l'historique | `python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml --mode initial` |
| Exécution courante, sur le delta depuis la dernière réussie | `python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml --mode incremental` |
| Traiter un extrait local, hors ligne | `python -m e07_fs.run_pipeline --config e07_fs/config/E07_FS.yaml --input extrait.csv` |
| Appliquer les corrections métier | `python -m e07_fs.apply_corrections --file "<sortie>\E07_FS_classification.xlsx"` |
| Extraction sur requête libre, nettoyée | `python -m e07_fs.ad_hoc_extraction --config e07_fs/config/E07_FS.yaml --query "SELECT TOP 100 * FROM ..."` |
| Enchaîner les 5 APIs | `powershell -File scripts\run_pipeline.ps1` |

Le préfixe de module désigne l'API : `e07_fs`, `e08_ocd`, `e09_pe`, `e10_fe`, `e11_rdcc`. Les
mêmes commandes existent pour chacune.

<!-- saut-de-page -->

### 5.3 Livrables, par API

| Fichier | Contenu |
|---|---|
| `{API}_classification.xlsx` | **une mapping table par champ, un onglet par champ** : valeur brute → valeur normalisée, contenu cumulatif ; plus un onglet d'anomalies pour les champs numériques et de date ; plus l'onglet `Instructions`, historique des corrections |
| `Rapport_Qualite_Outliers_{API}_{date}.pdf` et `.md` | rapport de qualité de l'exécution et rapport des outliers, par champ et par banque |
| Notification | indicateurs de l'exécution, les deux livrables en pièce jointe |

Le classeur porte un **chemin stable** et est écrasé à chaque exécution : la restitution Power BI
pointe toujours le même fichier, dont le contenu se réactualise.

<!-- saut-de-page -->

## 6. Les deux chaînes en regard

| | Pipeline par champ | Pipeline par API |
|---|---|---|
| Unité de lancement | un champ | un endpoint |
| Portée d'une commande | toutes les APIs du champ, ou une seule | tous les champs de l'API |
| Exécutions pour couvrir le périmètre | 8, soit 22 couples | 5 |
| Lectures du jeu de données | une par champ | une par exécution |
| Livrables | un classeur horodaté par couple | un classeur et un rapport par endpoint |
| Mapping table | valeurs du lot traité | cumulative, toute la connaissance du champ |
| Traitement par delta | non | oui, état par endpoint |
| Champs numériques et de date | hors périmètre | inclus, avec rapport d'anomalies |
| Usage | campagne ciblée sur un champ, constitution des référentiels | production récurrente, vue consolidée par endpoint |

---

## 7. Architecture logique du pipeline par API

### 7.1 Deux couches

```
shared/        infrastructure générique, aucune logique métier de champ
e0X_xxx/       un package autonome par endpoint, toute la logique métier
```

Un package d'endpoint ne réutilise que `shared/`, et rien d'un autre package. Ajouter un endpoint
ne modifie aucun endpoint existant ; un endpoint peut diverger — règle particulière, moteur de
résolution inversé, champ hors périmètre — sans négociation avec les autres.

**Contrepartie assumée** : les champs communs à plusieurs endpoints sont des copies. Des tests de
cohérence inter-packages vérifient donc, pour chaque copie, la présence des étapes obligatoires
de la cascade, la parallélisation des appels au modèle de langage, l'identité du code entre
copies — arbre syntaxique comparé, commentaires exclus — et l'absence de redéfinition locale de la
règle de traitement des valeurs `NA`.

### 7.2 Contenu de `shared/`

- **Orchestration** — `base_api_pipeline.py` (déroulé d'une exécution), `field_processor.py`
  (contrat entre un champ et l'orchestrateur), `config.py` et `config_validate.py` (fusion et
  validation stricte de la configuration).
- **Accès aux données** — `db_connector.py` (lecture seule, projection, lecture par paquets,
  conversion des colonnes texte volumineuses, reprise sur coupure réseau, lecture de fichiers
  locaux avec détection d'encodage), `query_columns.py` (projection et confrontation au schéma
  réel), `state_store.py` (état incrémental).
- **Règles transverses** — `na_rule.py` (traitement des valeurs `NA`, vectorisé),
  `referentiel_cibles.py` (valeur déjà conforme au référentiel), `dgi_cache.py` (mémoire des
  rapprochements fiscaux déterministes).
- **Résolution assistée** — `claude_client.py` (liste fermée, identification d'une banque par code
  SWIFT/BIC publié, arbitrage entre candidats fiscaux, identification documentaire d'une entité
  étrangère), `claude_batches.py` (lots parallèles, plafond par exécution).
- **Restitution** — `writer.py`, `report_templates.py`, `pdf_report.py`, `quality_report.py`,
  `email_notifier.py`, `corrections_history.py`.

### 7.3 Contrat entre les couches

Chaque champ expose une fabrique et un processeur dont la méthode de traitement retourne une
structure uniforme : jeu de données annoté, **mapping table**, anomalies ou outliers, colonnes
intermédiaires à exclure, indicateurs, noms d'onglets. L'orchestrateur ne manipule que cette
structure : il ignore ce qu'est une devise, un pays ou un solde.

La configuration fait le lien avec le code : le type déclaré pour un champ sélectionne la famille
de traitement, et son nom sélectionne le module. Un type ou un nom inconnu échoue explicitement,
au démarrage.

Trois familles de champs, qu'un endpoint combine librement :

| Famille | Résultat |
|---|---|
| Catégoriel | mapping table et méthode de résolution par ligne |
| Contrôle ligne à ligne | rapport d'anomalies, règle par règle |
| Cohérence inter-lignes | rapport d'anomalies, avec chaînage temporel |

### 7.4 Exécution et empreinte mémoire

Les champs d'une API sont indépendants : aucun ne lit le résultat d'un autre. Ils sont donc
traités **en parallèle**, un fil par champ. Trois mécanismes bornent le temps et la mémoire :
chaque champ ne reçoit que les colonnes dont il a besoin ; les cascades travaillent sur les
**valeurs distinctes** puis rediffusent le résultat sur les lignes ; les appels au modèle de
langage partent par lots simultanés.

<!-- saut-de-page -->

## 8. Déroulé d'une exécution

1. **Mode** : historique complet, ou delta depuis le dernier horodatage traité.
2. **Confrontation au schéma réel** de la table, avant la requête principale : une colonne
   optionnelle absente est retirée, une colonne indispensable absente arrête l'exécution.
3. **Chargement** : projection des seules colonnes utiles, lecture par paquets, conversion des
   colonnes texte volumineuses, reprise sur coupure réseau passagère.
4. **Traitement des champs** en parallèle, puis des contrôles numériques et de date.
5. **Cascade de résolution**, dans cet ordre invariant :

   ```
   mémoire des valeurs validées       (une correction métier prime sur tout le reste)
   -> valeur déjà conforme au référentiel
   -> référentiel métier
   -> règles déterministes du champ   (alias, bruit connu, codes, mots-clés)
   -> résolution assistée             (base fiscale, liste fermée, recherche documentaire)
   -> OUTLIER
   ```

6. **Qualité** : taux de conformité et de normalisation, compteurs cumulés, répartition des
   outliers par champ et par banque.
7. **Livrables** : classeur des mapping tables, rapport, notification.
8. **État** : horodatage traité, statut, compteurs, pour l'exécution suivante.

Un échec laisse l'état inchangé : le delta est repris à l'exécution suivante. Une exécution sur
extrait local est entièrement hors ligne — ni notification, ni dépôt distant, ni écriture d'état.

Chaque valeur normalisée porte la **méthode** qui l'a produite, ce qui distingue dans les
livrables ce qui était déjà connu — mémoire validée, référentiel, valeur déjà conforme, alias,
code, NIF exact, rapprochement fiscal net — de ce qui a été tranché pendant l'exécution —
résolution assistée, arbitrage, classification par mots-clés, `OUTLIER`.

---

## 9. Données et persistance

| Élément | Nature | Versionné |
|---|---|---|
| Tables source | lecture seule stricte | — |
| Référentiels métier | validés par le métier | oui |
| Mémoire des valeurs validées | résolutions et corrections déjà tranchées | oui |
| Historique des corrections | traçabilité des validations | oui |
| État incrémental | horodatage traité, statut, compteurs | non |
| Mémoire des rapprochements fiscaux | données dérivées, reproductibles | non |
| Livrables | classeurs, rapports | non |
| Fichiers de référence externes | base fiscale, entreprises publiques | non |
| Secrets | base, modèle de langage, messagerie | non |

---

## 10. Dépendances externes et modes de défaillance

| Dépendance | Usage | Défaillance |
|---|---|---|
| SQL Server, lecture seule | source unique des données | arrêt de l'exécution, état inchangé, delta repris |
| Base fiscale, entreprises publiques | rapprochement des entités locales | base fiscale absente : erreur explicite au démarrage ; entreprises publiques absentes : dégradation propre |
| Modèle de langage | valeurs nouvelles, arbitrage, recherche documentaire | échec technique : valeur en `OUTLIER`, jamais mémorisée, réessayée |
| Messagerie | notification de fin d'exécution | exécution réussie, échec d'envoi journalisé |

Aucune dépendance externe n'est sur le chemin critique de l'intégrité : un échec de résolution
assistée, de notification ou de diffusion ne corrompt ni la mémoire, ni l'état, ni les livrables
déjà produits.

---

## 11. Exécution actuelle et limites connues

Un poste Windows exécute une tâche planifiée qui enchaîne les 5 endpoints sur leur delta. Chaque
endpoint gère son succès, son échec et son état : l'échec de l'un n'empêche pas les suivants. Les
journaux sont écrits par exécution et par endpoint. Une première exécution sur l'historique est
nécessaire par endpoint avant la mise sous planification.

- Exécution sur un poste utilisateur, en session interactive, accès par réseau privé virtuel et
  pilote ODBC historique : disponibilité et débit de lecture dépendent de ce poste.
- Secrets portés par un fichier local ; fichiers de référence externes déposés manuellement, sans
  contrôle d'empreinte entre postes.
- Enchaînement par script séquentiel : pas de reprise par étape, pas d'historique d'exécution
  consolidé, pas d'alerte en dehors de la notification de fin d'exécution.
- Livrables au format Excel : au-delà d'un seuil de lignes, le détail des anomalies bascule
  automatiquement dans un fichier CSV complet.

---

Le détail par endpoint figure dans le README de chaque package. Les règles métier sont décrites
dans [FUNCTIONAL.md](FUNCTIONAL.md), les choix de conception dans [ARCHITECTURE.md](ARCHITECTURE.md),
les réglages dans [CONFIGURATION.md](CONFIGURATION.md).
