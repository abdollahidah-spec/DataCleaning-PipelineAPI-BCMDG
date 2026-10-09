# Nettoyage et normalisation des données réglementaires BCM — architecture

Nature du projet, architecture logique, fonctionnement et contenu de la chaîne de traitement.

---

## 1. Objet et périmètre

La chaîne traite les états réglementaires remontés par les banques au datawarehouse de la BCM.
Deux natures de traitement coexistent, selon le champ :

- **Normalisation** d'un champ catégoriel : la valeur brute saisie par un opérateur bancaire est
  rapprochée d'une valeur de référence, ou signalée non rattachable (`OUTLIER`) pour validation
  métier.
- **Contrôle** d'un champ numérique ou de date : aucune valeur normalisée n'a de sens ; le
  résultat est un rapport d'anomalies, règle par règle.

| Endpoint | Table source | Champs normalisés | Contrôles |
|---|---|---|---|
| E11 — RDCC | `E11EtatBcmReleveDesComptesCorrespondants` | NomCorrespondant, Devise | 4 règles sur les soldes |
| E09 — PE | `E9EtatBcmPrevisionEcheances` | Devise | 2 règles sur les échéances |
| E08 — OCD | `E8EtatBcmOuvertureCreditDocumentaires` | Devise, NomCorrespondant, Produits, NomDonneurOrdre, Beneficiaire, Pays | — |
| E07 — FS | `E7EtatBcmFluxSortants` | TypeSwift, ModeReglement, Devise, NomDonneurOrdre, Beneficiaire, NatureEconomique, Pays | 4 règles sur les transactions |
| E10 — FE | `E10EtatBcmFluxEntrants` | idem E07 | idem E07 |

Volumétrie relevée en base : E09 ≈ 5,1 M lignes, E10 ≈ 3,0 M, E07 ≈ 1,0 M, E11 ≈ 350 k,
E08 ≈ 13 k. Les tables sont lues en lecture seule stricte : aucune écriture, aucun DDL.

Le sens du flux change le moteur de résolution d'un même nom de champ. Sur les flux sortants
(E07), le donneur d'ordre est une entité mauritanienne — rapprochée de la base fiscale — et le
bénéficiaire une entité étrangère — identifiée par recherche documentaire. Sur les flux entrants
(E10), les rôles sont inversés. Les deux endpoints portent les mêmes champs avec des moteurs
échangés.

---

## 2. Nature du projet : deux volets

Le périmètre fonctionnel — 8 familles de champs réparties sur 5 endpoints — a été couvert en deux
temps, selon deux axes de découpage opposés. Les deux volets existent toujours, avec des rôles
distincts et non interchangeables.

### 2.1 Volet par champ — `DataCleaning-PipelineField-BCMDG`

**Axe de découpage** : un traitement par *champ*, exécuté sur toutes les APIs qui contiennent ce
champ. Le repo est organisé en dossiers de champs (`devise/`, `pays/`, `nomdonneurordre/`,
`beneficiaire/`, `nature_economique/`, `typeswift/`, `mode_reglement/`, `nomcorrespondant/`),
chacun portant sa logique, ses référentiels, ses caches et une configuration par couple
champ/API.

**Ce qui y a été construit, et qui reste la référence :**

- les **référentiels métier** par champ et par flux : codes ISO 4217, codes SWIFT par sens de
  flux, modes de règlement, nature économique (catégories et libellés, listes distinctes pour les
  flux entrants et sortants), raisons sociales des donneurs d'ordre et bénéficiaires par endpoint,
  correspondants bancaires, mots-clés d'adresses ;
- les **cascades de résolution** : ordre des étapes, formes de nettoyage, détection du bruit,
  pré-filtres d'outliers évidents (nom de personne, adresse, date, nombre), seuils de
  rapprochement flou, règles de classification locale des entités ;
- les **caches de classification validés** (`validated_classif_*.json`), constitués au fil des
  campagnes de traitement et des validations métier : plusieurs milliers de modalités déjà
  tranchées, dont le contenu représente l'essentiel de la valeur accumulée ;
- les **analyses transverses par champ** : statistiques comparant l'état avant et après
  traitement sur l'ensemble des couples champ/API.

**Limites constatées à l'usage**, qui ont motivé le second volet :

1. **Lectures redondantes** : couvrir un endpoint de 7 champs impose 7 lectures du même jeu de
   données, chacune rapatriant ses colonnes et ses millions de lignes.
2. **Aucune vision consolidée** : la qualité d'un endpoint n'existe nulle part comme un tout ;
   elle se reconstitue en recollant les livrables de chaque champ.
3. **Livrables fragmentés** : un classeur par couple champ/API, soit 22 fichiers à produire,
   nommer, diffuser et réconcilier manuellement.
4. **Couplage des traitements** : une évolution sur un champ touche tous les endpoints qui le
   portent, sans possibilité de divergence assumée par endpoint.

**Statut** : figé. Reste la source de vérité métier pour l'origine des référentiels et des
caches, et l'outil des analyses par champ.

### 2.2 Volet par API — `DataCleaning-PipelineAPI-BCMDG`

**Axe de découpage** : un traitement par *endpoint*, qui applique tous ses champs en un seul run
et produit un seul jeu de livrables. C'est la chaîne de production.

**Ce que ce volet ajoute :**

- **un run, une lecture** : le jeu de données est chargé une fois, puis distribué aux champs ;
- **une qualité consolidée par endpoint** : taux de conformité et de normalisation du run,
  compteurs cumulés sur l'historique traité, répartition des outliers par champ et par banque ;
- **des livrables uniques et stables** : un classeur de classification par endpoint, à chemin
  fixe pour la restitution Power BI, et un rapport PDF ;
- **le mode incrémental** : un état par endpoint mémorise le dernier horodatage traité, le run
  suivant ne traite que le delta ;
- **la boucle de correction métier** : les valeurs non rattachées sont proposées à validation,
  les corrections appliquées priment sur toute la cascade dès le run suivant ;
- **l'exploitation** : planification, journaux par run, notification de fin de run, extraction ad
  hoc pour les analyses ponctuelles.

**Ce qui a été reporté depuis le volet par champ** : référentiels, caches validés et cascades, à
l'identique. Les caches sont donc déjà « chauds » dès le premier run d'un endpoint — l'essentiel
des modalités connues est déjà tranché, et seules les valeurs nouvelles sont à résoudre.

**Ce qui a changé au passage** : un moteur d'embeddings local (modèle mpnet) et un modèle de
langage auto-hébergé, utilisés par le volet par champ, ont été remplacés par des appels à une API
de modèle de langage sur **liste fermée** de valeurs de référence — aucune valeur inventée n'est
acceptable — ce qui supprime l'infrastructure locale à héberger et à maintenir.

<!-- saut-de-page -->

### 2.3 Comparaison

| | Volet par champ | Volet par API |
|---|---|---|
| Unité de traitement | un champ, toutes ses APIs | une API, tous ses champs |
| Exécutions du périmètre | 8 champs × 22 couples | 5 runs |
| Lectures du jeu de données | une par champ | une par run |
| Livrables | un classeur par couple | un classeur et un rapport par endpoint |
| Qualité | par champ | par endpoint, du run et cumulée |
| Mode delta | non | oui, état par endpoint |
| Divergence par endpoint | impossible | assumée et isolée |
| Rôle actuel | socle métier, analyses par champ | chaîne de production |

---

## 3. Architecture logique du volet par API

### 3.1 Deux couches, une frontière nette

```
shared/                   Infrastructure générique — aucune logique métier de champ
e0X_xxx/                  Un package autonome par endpoint — toute la logique métier
```

**Principe directeur** : un package d'endpoint ne réutilise que `shared/`, et rien d'un autre
package. Conséquences directes : ajouter un endpoint ne modifie aucun endpoint existant ; une
évolution métier sur un endpoint ne peut pas en casser un autre ; un endpoint peut diverger
(règle NA particulière, moteur de résolution inversé, champ hors périmètre) sans négociation avec
les autres.

**Contrepartie assumée** : les champs communs à plusieurs endpoints sont des **copies**, et non
un module partagé. Le risque est qu'un correctif appliqué à une copie soit oublié dans les
autres. Il est traité par des tests de cohérence inter-packages qui vérifient, pour chaque copie :
la présence des étapes obligatoires de la cascade, la parallélisation des appels au modèle de
langage, l'identité du code entre copies — comparaison de l'arbre syntaxique, commentaires et
docstrings exclus — et l'absence de toute redéfinition locale de la règle NA.

### 3.2 Contenu de `shared/`

**Orchestration**
- `base_api_pipeline.py` — classe de base d'un run : résolution du mode, chargement, découpage
  des colonnes par champ, exécution parallèle, calcul de la qualité, assemblage des livrables,
  persistance de l'état, notification. Ne connaît ni les champs ni les endpoints.
- `field_processor.py` — contrat `FieldProcessor` / `FieldResult`, et implémentation générique du
  champ catégoriel (`CategoricalFieldProcessor`) : appel de la fonction de traitement, extraction
  des outliers par banque, table de classification, statistiques, application des corrections.
- `config.py`, `config_validate.py` — fusion profonde de `config_base.yaml` avec le YAML de
  l'endpoint, puis validation stricte : type de champ connu, colonnes obligatoires présentes pour
  ce type, référentiel déclaré.

**Accès aux données**
- `db_connector.py` — moteur SQL Server en lecture seule : projection explicite, lecture par
  paquets avec progression, conversion des colonnes texte `NVARCHAR(MAX)` à la lecture, nouvelle
  tentative sur coupure réseau passagère, garde-fou interdisant toute requête non `SELECT`,
  lecture de fichiers locaux avec détection de l'encodage et du séparateur.
- `query_columns.py` — calcul de la projection depuis le YAML (colonnes des champs, témoins,
  colonnes lues par un contrôle de gabarit) et confrontation au schéma réel de la table : une
  colonne optionnelle absente est retirée, une colonne principale absente arrête le run.
- `state_store.py` — état incrémental par endpoint : dernier horodatage traité, statut, compteurs
  cumulatifs.

**Règles transverses**
- `na_rule.py` — règle NA/OUTLIER commune, vectorisée, avec son équivalent ligne à ligne conservé
  comme référence d'équivalence.
- `referentiel_cibles.py` — index inverse d'un référentiel : une valeur brute déjà égale à une
  valeur de référence est résolue par elle-même, sans appel coûteux.
- `dgi_cache.py` — mémoire des rapprochements fiscaux déterministes, invalidée par empreinte de
  la base fiscale.

**Appels au modèle de langage**
- `claude_client.py` — quatre modes d'appel : résolution sur liste fermée de valeurs de
  référence, identification d'une banque par code SWIFT/BIC publié, arbitrage entre candidats
  fiscaux proches, identification documentaire d'une entité étrangère par recherche web. Toute
  réponse hors liste est rejetée ; un échec technique est distingué d'un verdict `OUTLIER`.
- `claude_batches.py` — découpage en lots, exécution parallèle, plafond de valeurs nouvelles par
  run, et séparation stricte entre échec technique (jamais mis en cache) et verdict.

**Restitution**
- `writer.py` — écriture du classeur multi-onglets et bascule CSV au-delà d'un seuil de lignes.
- `report_templates.py`, `pdf_report.py` — gabarit de rapport commun et conversion en PDF.
- `quality_report.py` — indicateurs du run et compteurs cumulés.
- `email_notifier.py` — notification de fin de run, succès comme échec.
- `corrections_history.py` — historique des corrections métier appliquées.

### 3.3 Contenu d'un package d'endpoint

```
e0X_xxx/
  config/{API}.yaml      Table source, champs, colonnes, seuils, référentiels, livrables
  fields/*.py            Un module par champ : cascade, référentiel, cache, fabrique de processeur
  referentiel/*.json     Référentiels métier et caches de classification validés
  pipeline.py            Classe de run de l'endpoint + fabrique des processeurs depuis le YAML
  run_pipeline.py        Point d'entrée : mode, fichier local, exécution à blanc
  apply_corrections.py   Application des corrections métier aux caches
  reports.py             Libellés et rattachement des règles aux champs pour le rapport
  ad_hoc_extraction.py   Extraction sur requête libre, sans effet de bord
```

<!-- saut-de-page -->

### 3.4 Contrat entre les couches

Chaque champ expose une fabrique `build_{champ}_processor(field_cfg)` et un processeur dont la
méthode `process(df, api_id)` retourne un `FieldResult` :

| Attribut | Contenu |
|---|---|
| `df` | jeu de données annoté des colonnes produites par le champ |
| `classification_df` | table valeur brute → valeur normalisée, **cumulative** : référentiel, cache et corrections, indépendante du delta traité |
| `outliers_df` | outliers par banque pour un champ catégoriel, lignes en anomalie pour un champ de contrôle |
| `exclude_from_export` | colonnes intermédiaires à ne pas publier |
| `stats` | indicateurs alimentant le rapport de qualité |
| `sheet_names` | noms des onglets du classeur |

L'orchestrateur ne manipule que des `FieldResult` : il ignore ce qu'est une devise, un pays ou un
solde. Le `pipeline.py` de l'endpoint fait le lien entre la configuration et le code : le `type`
déclaré dans le YAML sélectionne la famille de traitement, et pour un champ catégoriel le `name`
sélectionne le module. Un `type` ou un `name` inconnu échoue explicitement, au démarrage.

La table de classification est **cumulative par construction** : en mode incrémental, une
modalité vue lors d'un run précédent mais absente du delta courant doit rester présente dans le
classeur, qui est écrasé à chaque run.

### 3.5 Trois familles de champs

| Famille | Type YAML | Résultat | Champs |
|---|---|---|---|
| Catégoriel | `categorical` | valeur normalisée et méthode de résolution, par ligne | Devise, Pays, NomCorrespondant, NomDonneurOrdre, Beneficiaire, NatureEconomique, TypeSwift, ModeReglement, Produits |
| Contrôle ligne à ligne | `numeric_validation`, `transaction_validation` | rapport d'anomalies | Échéances (E09), Transactions (E07, E10) |
| Cohérence inter-lignes | `numeric_coherence` | rapport d'anomalies, avec chaînage temporel | Soldes RDCC (E11) |

Un endpoint combine librement ces familles. La validation de configuration exige, pour chaque
type, un jeu de colonnes précis : l'absence d'une colonne obligatoire est détectée au chargement
de la configuration, pas au milieu du traitement.

### 3.6 Modèle d'exécution et empreinte mémoire

Les champs catégoriels d'un endpoint sont indépendants : aucun ne lit la colonne de sortie d'un
autre. Ils sont donc exécutés **en parallèle**, un fil par champ. Trois mécanismes bornent la
mémoire et le temps :

1. **Projection par champ** : chaque processeur déclare les colonnes dont il a besoin ; il ne
   reçoit que celles-là, et non une copie du jeu de données complet.
2. **Résolution sur valeurs distinctes** : les cascades travaillent sur les modalités uniques,
   puis rediffusent le résultat sur les lignes par jointure ou projection vectorisée. Aucune
   fonction métier n'est appliquée ligne par ligne.
3. **Parallélisme des appels au modèle de langage** : au sein d'un champ, les lots sont lancés
   simultanément, le temps d'un appel étant dominé par l'attente réseau.

### 3.7 Mémoires et ordre de priorité

Trois mémoires distinctes, de la plus prioritaire à la plus volatile :

1. **Cache de classification validé** (`validated_classif_{champ}_{api}.json`, versionné) —
   résolutions déjà tranchées et corrections métier. Consulté en premier, prime sur toute la
   cascade. Ne contient jamais un échec technique d'appel au modèle de langage.
2. **Mémoire des rapprochements fiscaux** (hors dépôt) — résultats déterministes du rapprochement
   contre la base fiscale, porteurs d'une empreinte de cette base : si la base change,
   l'empreinte diffère et le rapprochement est intégralement recalculé.
3. **État incrémental** (hors dépôt) — dernier horodatage traité, statut, compteurs cumulés. Un
   échec laisse l'état inchangé : le delta est repris au run suivant.

### 3.8 Ajouter un endpoint ou un champ

Ajouter un endpoint consiste à créer un package : un YAML décrivant la table et les champs, les
modules de champs (repris d'un autre endpoint quand la logique est identique, puis libres de
diverger), les référentiels et caches, et les quatre points d'entrée. Aucun fichier de `shared/`
ni d'un autre package n'est modifié, à l'exception de la déclaration d'un **nouveau type** de
champ dans la validation de configuration.

Ajouter un champ à un endpoint existant consiste à écrire son module, sa fabrique, et à le
déclarer dans le YAML et dans la table de correspondance du `pipeline.py` de l'endpoint.

---

## 4. Fonctionnement d'un run

1. **Résolution du mode** : `initial` (historique, éventuellement borné par une date de début) ou
   `incremental` (lignes dont l'horodatage de création dépasse le dernier horodatage traité).
2. **Confrontation au schéma réel** de la table, avant la requête principale.
3. **Chargement** : projection explicite, lecture par paquets avec progression, colonnes texte
   `NVARCHAR(MAX)` converties à la lecture, nouvelle tentative sur coupure réseau passagère.
4. **Prétraitement** propre à l'endpoint quand il existe : E11 calcule une colonne témoin
   « sans activité » commune à ses deux champs catégoriels, une seule fois par run.
5. **Traitement des champs catégoriels en parallèle**, puis des champs de contrôle.
6. **Cascade de résolution**, dans cet ordre invariant pour tout champ catégoriel :

   ```
   cache des corrections validées         (une correction métier prime sur tout le reste)
   -> valeur déjà conforme au référentiel
   -> référentiel métier
   -> règles déterministes du champ       (alias, bruit connu, codes, mots-clés)
   -> rapprochement ou modèle de langage  (base fiscale, liste fermée, recherche documentaire)
   -> OUTLIER
   ```

   La règle NA/OUTLIER est appliquée en fin de cascade, de façon vectorisée.
7. **Qualité** : indicateurs du run et compteurs cumulés sur l'historique traité.
8. **Livrables** : classeur de classification à chemin stable, rapport PDF, notification.
9. **État** : horodatage traité, statut, compteurs, pour le run suivant.

Un échec à n'importe quelle étape laisse l'état inchangé, notifie l'échec et retourne un code de
sortie non nul : le run suivant reprend le même delta. Une exécution sur fichier local est
entièrement hors ligne — ni notification, ni dépôt distant, ni écriture d'état.

---

## 5. Méthodes de résolution et traçabilité

Chaque valeur normalisée porte la méthode qui l'a produite, conservée dans les livrables. Elle
distingue ce qui était déjà connu avant le run de ce qui a été tranché pendant le run, et alimente
les indicateurs « nouvelles valeurs » des notifications.

**Déjà connu avant le run** — cache validé (`WARM`), référentiel (`MAP`), valeur déjà conforme au
référentiel (`MAP_CIBLE`), alias, code numérique, préfixe déduit, bruit connu, NIF exact,
entreprise publique reconnue, rapprochement fiscal exact ou net.

**Tranché pendant le run** — résolution par modèle de langage, arbitrage entre candidats
fiscaux, absence de candidat, classification locale par mots-clés (particulier, établissement),
`OUTLIER`.

Les déductions par mots-clés sont volontairement classées « nouvelles » : elles portent sur une
valeur jamais validée par un humain ni trouvée dans une source de référence.

---

<!-- saut-de-page -->

## 6. Données et persistance

| Élément | Nature | Emplacement | Versionné |
|---|---|---|---|
| Tables source | lecture seule stricte | SQL Server | — |
| Référentiels métier | validés par le métier | `{api}/referentiel/` | oui |
| Caches de classification | résolutions validées et corrections | `{api}/referentiel/` | oui |
| Historique des corrections | traçabilité métier | `{api}/referentiel/` | oui |
| État incrémental | horodatage, statut, compteurs | `{STATE_DIR}/` | non |
| Mémoire des rapprochements fiscaux | données dérivées, reproductibles | `{STATE_DIR}/` | non |
| Livrables | classeur, rapport PDF | `{OUTPUT_BASE}/{API}/` | non |
| Fichiers de référence externes | base fiscale, entreprises publiques | `req/` | non |
| Secrets | base, modèle de langage, messagerie | `.env` | non |

Les caches de classification sont la mémoire économique de la chaîne : une valeur résolue une
fois par un appel payant n'est jamais repayée.

---

## 7. Boucle de correction métier

Les valeurs non rattachées apparaissent dans les onglets de classification et dans le rapport
PDF. Le métier renseigne un classeur comportant un onglet `Instructions`
(`Champ | Input | Label_Attendu`), qui est ensuite appliqué : chaque ligne est routée vers le
cache du champ nommé, sous plusieurs formes de clé, pour que la correction soit retrouvée quelle
que soit la convention de nettoyage du champ. La correction prime dès le run suivant — la valeur
ressort avec la méthode `WARM` — et reste tracée dans l'historique des corrections, affiché en
lecture seule dans le classeur suivant.

---

<!-- saut-de-page -->

## 8. Dépendances externes et modes de défaillance

| Dépendance | Usage | Défaillance |
|---|---|---|
| SQL Server, lecture seule | source unique des données | arrêt du run, état inchangé, delta repris |
| Base fiscale, entreprises publiques | rapprochement des entités locales | base fiscale absente : erreur explicite au démarrage ; entreprises publiques absentes : dégradation propre |
| API de modèle de langage | valeurs nouvelles, arbitrage, recherche documentaire | échec technique : valeur en `OUTLIER`, jamais mise en cache, réessayée |
| Messagerie | notification de fin de run | run réussi, échec d'envoi journalisé |
| Dépôt de fichiers distant | diffusion des livrables | non bloquant |

Aucune dépendance externe n'est sur le chemin critique de l'intégrité : un échec d'appel au
modèle de langage, de notification ou de diffusion ne corrompt ni les caches, ni l'état, ni les
livrables déjà produits.

---

## 9. Exécution actuelle

Un poste Windows exécute une tâche planifiée qui enchaîne les 5 endpoints en mode incrémental.
Chaque endpoint gère son succès, son échec et son état : l'échec de l'un n'empêche pas le
lancement des suivants. Les journaux sont écrits par run et par endpoint. Un chargement initial
manuel par endpoint est nécessaire avant la première mise sous planification, pour amorcer l'état
incrémental.

---

## 10. Limites connues de l'existant

- Exécution sur un poste utilisateur, en session interactive, avec accès par réseau privé virtuel
  et pilote ODBC historique : la disponibilité et le débit de lecture dépendent de ce poste.
- Secrets portés par un fichier local ; fichiers de référence externes déposés manuellement, sans
  contrôle d'empreinte entre postes.
- Enchaînement des endpoints par script séquentiel : pas de reprise par étape, pas d'historique
  d'exécution consolidé, pas d'alerte en dehors de la notification de fin de run.
- Livrables au format Excel : plafond de lignes atteint sur les gros volumes d'anomalies, d'où
  une bascule automatique vers un fichier CSV complet au-delà d'un seuil configuré.

---

Les règles métier détaillées figurent dans [FUNCTIONAL.md](FUNCTIONAL.md), les choix de conception
dans [ARCHITECTURE.md](ARCHITECTURE.md), les réglages dans [CONFIGURATION.md](CONFIGURATION.md).
