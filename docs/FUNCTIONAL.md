# Règles métier

Règles appliquées par les deux chaînes : la cascade de résolution champ par champ, les règles
transverses, les contrôles numériques et de date, et les lectures retenues là où l'énoncé
d'origine était ambigu.

L'organisation du code est décrite dans [ARCHITECTURE.md](ARCHITECTURE.md), les réglages dans
[CONFIGURATION.md](CONFIGURATION.md), les commandes et les livrables dans le README de chaque
package.

## 1. Les champs traités

| Champ | Colonne source | Endpoints | Nature de la résolution | Référentiel |
|---|---|---|---|---|
| Devise | `Devise` | E07, E08, E09, E10, E11 | entièrement déterministe | codes ISO 4217 |
| TypeSwift | `TypeSwfit` | E07, E10 | entièrement déterministe | codes de messages SWIFT, une liste par sens de flux |
| ModeReglement | `ModeReglement` | E07, E10 | entièrement déterministe | modalités validées |
| Pays | `Pays` | E07, E08, E10 | déterministe, puis assistée | construit en code, plus des mots-clés d'adresse |
| NatureEconomique | `NatureEconomique` | E07, E10 | règles de mots-clés, puis assistée | catégories et labels validés |
| NomCorrespondant | `NomCorrespondant` | E08, E11 | référentiel, puis assistée | banques correspondantes validées |
| NomDonneurOrdre | `NomDonneurOrdre` | E07, E08, E10 | référentiel, rapprochement fiscal **ou** résolution assistée selon le sens du flux | raisons sociales validées, base fiscale |
| Beneficiaire | `Beneficiaire` | E07, E08, E10 | idem, en miroir | idem |
| Produits | `Produits` | E08 | référentiel, puis assistée | construit depuis le fichier métier |

`TypeSwfit` est l'orthographe réelle de la colonne dans le schéma source.

Sur un flux **sortant** (E07), le donneur d'ordre est local et le bénéficiaire étranger ; sur un
flux **entrant** (E10), les rôles s'inversent. Le moteur de résolution suit l'entité, pas le nom
du champ : rapprochement sur la base fiscale pour l'entité locale, résolution assistée avec
recherche web pour l'entité étrangère.

## 2. Principe de la cascade

Chaque champ catégoriel est résolu par une cascade d'étapes ordonnées, de la moins coûteuse à la
plus coûteuse. La première étape qui tranche l'emporte, et la **méthode** qui a résolu la valeur
est conservée dans les livrables : elle distingue ce qui était déjà connu de ce qui a été tranché
pendant l'exécution.

La cascade travaille sur les **valeurs distinctes** du champ, jamais ligne par ligne, puis le
résultat est rediffusé sur les lignes.

| Méthode | Signification |
|---|---|
| `WARM` | mémoire des valeurs validées — résolution ou correction déjà tranchée |
| `MAP_CIBLE` | la valeur **est déjà** une valeur cible du référentiel |
| `MAP` | entrée exacte du référentiel |
| `ALIAS` | variante connue d'une entrée du référentiel |
| `NUM` | code numérique de la devise |
| `STRIP` | nettoyage de repli : description entre parenthèses, caractères non alphanumériques, préfixe de trois lettres |
| `PREFIX` | numéro seul complété en code de message valide |
| `NOISE` | bruit de saisie connu, en-tête, valeur hors sujet |
| `ADDR` | pays déduit d'un mot-clé d'adresse |
| `FUZZY` | rapprochement approché sur le référentiel |
| `NIF_EXACT` | identifiant fiscal valide trouvé tel quel dans la base fiscale |
| `PUBLIC_ENT` | entreprise publique reconnue |
| `PARTICULIER`, `ETS_PERSONNEL`, `ETS_OUTLIER` | classification locale d'un nom : personne physique, établissement personnel, établissement non identifiable |
| `DGI_EXACT_NORM` | correspondance exacte après normalisation poussée contre la base fiscale |
| `DGI_FUZZY_STRONG` | correspondance approchée nette : score élevé et écart franc avec le candidat suivant |
| `DGI_CLAUDE_ARBITRAGE` | candidats fiscaux proches, tranchés par le modèle de langage |
| `DGI_NO_MATCH` | aucun candidat plausible, aucun appel au modèle |
| `CLAUDE` | résolution assistée : liste fermée, identification bancaire, ou recherche web |
| `EMBED`, `REGLE`, `LLM` | chaîne par champ uniquement : embeddings locaux, règles de mots-clés, modèle local |
| `NA` | ligne sans activité légitime |
| `OUTLIER` | non résolu, à soumettre à validation métier |

Une résolution assistée est mémorisée pour ne pas être repayée. Un **échec technique** ne l'est
jamais : la valeur est reproposée à l'exécution suivante.

## 3. Cascade, champ par champ

### 3.1 Devise

Aucun appel à un modèle. Mémoire des valeurs validées, puis code ISO 4217 exact, puis code
numérique, puis alias connu, puis nettoyage de repli, puis bruit connu, sinon `OUTLIER`. Seul
champ présent sur les cinq endpoints.

### 3.2 TypeSwift

Mémoire, puis bruit connu ou valeur ressemblant à un montant, puis code valide **du sens de
flux** — la liste des codes dépend du flux, sortant ou entrant —, puis numéro seul à trois
chiffres complété en code de message s'il est valide, sinon `OUTLIER`.

### 3.3 ModeReglement

Mémoire, puis entrée exacte du référentiel, puis alias, puis bruit connu, puis valeur nulle ou
vide traitée explicitement, sinon `OUTLIER`.

### 3.4 Pays

Mémoire, puis normalisation Unicode, puis référentiel — construit en code à partir des
bibliothèques de codes pays, et non d'un fichier métier —, puis mot-clé d'adresse, puis
rapprochement approché, puis résolution assistée, sinon `OUTLIER`.

Particularité : `NA` est le code ISO de la Namibie. Une déclaration sans activité porte donc la
valeur `NoAs` pour ce champ, et non `NA`.

### 3.5 NatureEconomique

1. Valeur vide : non résolue, traitée par la règle NA.
2. Mémoire des valeurs validées, testée sur plusieurs formes de la valeur brute.
3. En-tête, outlier évident — adresse, date, nom propre, nombre — ou modalité vague : `OUTLIER`.
4. Label exact du référentiel ou mapping direct : `MAP`.
5. Résolution assistée sur la **liste fermée** des labels du référentiel : le modèle choisit un
   label existant ou répond `OUTLIER`, jamais un libellé inventé.

Le champ porte deux sorties : le label normalisé et sa **catégorie**, obtenue par consultation du
référentiel.

### 3.6 NomCorrespondant

Mémoire, puis valeur déjà cible du référentiel, puis référentiel des banques validées, puis
résolution assistée restreinte à l'identification d'une banque réelle disposant d'un code
SWIFT/BIC publié, sinon `OUTLIER`.

### 3.7 Entité locale — NomDonneurOrdre sur E07 et E08, Beneficiaire sur E10

Cascade la plus longue, la seule qui mobilise la base fiscale :

1. Mémoire des valeurs validées.
2. Valeur déjà cible du référentiel, puis référentiel des raisons sociales validées.
3. Raccourci par la règle NA, puis entreprise publique reconnue, puis outlier évident.
4. Identifiant fiscal valide trouvé tel quel dans la base fiscale.
5. Classification locale du libellé : personne physique, établissement personnel, établissement
   non identifiable.
6. Rapprochement sur la base fiscale : correspondance exacte après normalisation poussée, puis
   correspondance approchée nette — score élevé **et** écart franc avec le candidat suivant.
7. Candidats proches sans écart franc : arbitrage par le modèle de langage.
8. Aucun candidat plausible : `DGI_NO_MATCH`, sans aucun appel au modèle.

Les résolutions déterministes de l'étape 6 et les absences de correspondance de l'étape 8 sont
mémorisées à part, avec une empreinte du fichier fiscal : l'exécution suivante ne refait pas le
rapprochement. Une base fiscale modifiée change l'empreinte et déclenche un recalcul complet.

Les seuils du rapprochement sont des réglages : score fort, écart minimal avec le second
candidat, score plancher pour déclencher un arbitrage, taille des lots.

### 3.8 Entité étrangère — Beneficiaire sur E07 et E08, NomDonneurOrdre sur E10

La base fiscale nationale n'est pas pertinente. Mémoire, puis valeur déjà cible du référentiel,
puis référentiel, puis raccourci par la règle NA, puis entreprise publique, puis outlier évident,
puis classification locale du libellé, puis résolution assistée **avec recherche web réelle** pour
identifier l'entité et son nom légal, sinon `OUTLIER`.

Seules les résolutions effectivement payées par une recherche web sont mémorisées. Le nombre de
recherches par valeur est plafonné par un réglage.

### 3.9 Produits

Mémoire, puis valeur déjà cible du référentiel, puis bruit connu, puis référentiel — construit
depuis le fichier métier —, puis résolution assistée sur liste fermée, sinon `OUTLIER`. Champ
propre à E08, absent de la chaîne par champ.

## 4. Règle NA

Une valeur `NA` n'est légitime que si la ligne est bien une déclaration sans activité. Le témoin
de non-activité est déclaré par champ :

```
champ == 'NA'  ET  témoin == 'NA'   -> 'NA'
champ == 'NA'  ET  témoin != 'NA'   -> OUTLIER
champ vide, nul, ou non identifié   -> OUTLIER
```

La règle est appliquée **en dernier**, après la cascade, et vectorisée. Elle n'est jamais
redéfinie dans un package : un test de cohérence le vérifie.

Le témoin varie selon l'endpoint : la référence de transaction sur E07 et E10, le numéro de
crédit documentaire sur E08 et E09, une colonne synthétique sur E11.

**E11** — le témoin ne peut pas être une seule colonne. Une non-activité légitime se vérifie sur
tout le gabarit à la fois : nom du correspondant, devise et numéro de compte tous à `NA` **et**
les quatre montants à zéro. Le témoin est donc une colonne précalculée une seule fois pour tous
les champs de l'endpoint. Un témoin partiel laissait deux champs décider indépendamment et se
contredire.

Une valeur nulle en base n'échappe pas à la règle : elle est ramenée à une chaîne vide avant
comparaison, et devient donc `OUTLIER`.

## 5. Valeur déjà conforme au référentiel

Un référentiel associe un libellé brut à une valeur normalisée. Un libellé qui **est déjà** la
valeur normalisée n'en est donc pas une clé : sans traitement particulier il partait en résolution
payante ou en rapprochement fiscal, pouvait revenir `OUTLIER`, puis être mémorisé comme tel.

L'étape compare la valeur, **insensible à la casse**, à l'ensemble des valeurs cibles du
référentiel. Elle intervient juste après la mémoire des valeurs validées — une correction métier
garde donc la priorité — et s'applique à tous les champs adossés à un référentiel de libellés :
`NomCorrespondant`, `NomDonneurOrdre`, `Beneficiaire`, `Produits`.

## 6. Boucle de correction

L'onglet `Instructions` du classeur livré porte l'**historique en lecture seule** des corrections
déjà appliquées, en trois colonnes — `Champ | Input | Label_Attendu`. Il est vide à la première
exécution. Ce n'est pas une liste de valeurs à corriger : celles-ci se lisent dans les feuilles de
classification, lignes `OUTLIER`, et dans le rapport d'outliers.

Pour soumettre des corrections, le métier fournit un classeur comportant un onglet `Instructions`
aux mêmes trois colonnes, une ligne par valeur corrigée. `apply_corrections.py` route chaque ligne
vers la mémoire des valeurs validées du champ nommé, et la correction prime dès l'exécution
suivante, avec la méthode `WARM`.

Traitement des lignes invalides : sans `Label_Attendu`, la ligne est ignorée avec un décompte ; un
champ inconnu est signalé par un avertissement ; un champ de contrôle numérique est écarté — il
n'a pas de mémoire de valeurs validées.

Chaque correction est enregistrée sous **trois formes de clé** : libellé nettoyé, valeur brute,
valeur brute en majuscules. Les champs ne cherchent pas tous la même forme dans leur mémoire —
libellé nettoyé pour `Devise`, `TypeSwift` et `NomDonneurOrdre`, valeur brute en majuscules pour
`NatureEconomique` et `Pays`. Sans cela, une correction dont le nettoyage modifie la valeur restait
sans effet et sans message.

## 7. Contrôles numériques et de date

Ces champs ne se normalisent pas : ils produisent un rapport d'anomalies au lieu d'une mapping
table. Une ligne en échec sur plusieurs règles produit **une anomalie par règle** : aucune n'est
masquée par une autre. Ces contrôles n'existent que dans la chaîne par API.

### 7.1 E11 — cohérence des relevés de comptes correspondants

| Règle | Contrôle | Sévérité |
|---|---|---|
| `ARITHMETIC` | solde de fin = solde de début + mouvements créditeurs − mouvements débiteurs, à la tolérance près | bloquante |
| `TEMPORAL_CONTINUITY` | solde de fin du jour J = solde de début du jour J+1, pour un même compte. Regroupement par banque, correspondant, numéro de compte et devise | bloquante ; écart de plus d'un jour entre deux relevés : avertissement |
| `NO_ACTIVITY_CONFORMITY` | conformité du gabarit sans activité : champs identifiants tous à `NA` et quatre montants à zéro. Une `NA` partielle est une anomalie | bloquante |
| `DATE_VALIDITY` | date de fin de journée valide et antérieure ou égale à la date de création de la ligne | bloquante |

Deux relevés à la même date pour un même compte sont une anomalie bloquante : le chaînage est
ambigu, aucun des deux ne peut être retenu. Les lignes sans activité sont exclues du chaînage —
leurs champs identifiants valant tous `NA`, elles ne désignent aucun compte.

La conformité du gabarit se vérifie à zéro exact, sans la tolérance métier : c'est un contrôle de
gabarit, pas une réconciliation.

### 7.2 E09 — échéances prévisionnelles

| Règle | Contrôle |
|---|---|
| `AMOUNT_POSITIVE` | montant d'échéance strictement positif. Valeur non numérique ou manquante : anomalie |
| `DATE_VALIDITY` | date d'échéance interprétable et **strictement postérieure** à la date de création, comparaison au jour calendaire |

### 7.3 E07 et E10 — transactions

| Règle | Contrôle |
|---|---|
| `AMOUNT_NON_NEGATIVE` | montant de transaction numérique et positif ou nul |
| `RATE_NON_NEGATIVE` | taux de change numérique et positif ou nul |
| `DATE_VALIDITY` | date de transaction interprétable et antérieure à la date de création, comparaison au jour. Un réglage autorise le même jour |
| `NO_ACTIVITY_CONFORMITY` | message sans activité : champs texte du gabarit à `NA`, pays à `NoAs`, montant et taux à zéro |

Les colonnes du gabarit sans activité sont ajoutées à la requête même si aucun champ ne les
normalise. Le gabarit diffère entre les deux endpoints : une colonne de devise source présente sur
E07 est absente de la table E10.

## 8. Lectures retenues

Points où l'énoncé d'origine était ambigu ou incohérent, et la règle effectivement implémentée.

| Point | Règle retenue |
|---|---|
| E11, validité de la date de relevé | L'énoncé comparait un solde à une date. La règle implémentée porte sur la date de fin de journée, antérieure ou égale à la date de création |
| E09, sens de la validité de date | La date d'échéance doit être **postérieure** à la date de création : une échéance prévisionnelle est à venir. C'est le sens inverse de la règle E11, et il est cohérent tel quel |
| E11, clé de chaînage temporel | Banque, correspondant, numéro de compte et devise — un témoin plus court rendait le chaînage ambigu |
| Base fiscale, entreprises en cessation | Aucun filtre sur la situation : une entreprise en cessation reste rapprochable comme une entreprise active |
| Champ `Pays` | Résolu par appel au modèle de langage, et les résolutions sont mémorisées, l'appel étant facturé à l'usage |
| Champ `Pays`, valeur `NA` | `NA` étant le code de la Namibie, la non-activité se déclare `NoAs` |
| E07, E09 et E10, profondeur d'historique | Le chargement initial est borné aux lignes créées depuis le 01/01/2024. E08 et E11, de faible volume, chargent tout l'historique |
| E07 et E10, champ `Produit` | Hors périmètre de normalisation : seulement contrôlé par le gabarit des messages sans activité |
| Mémoire des valeurs validées | Une mémoire par couple champ/API : une valeur tranchée pour un endpoint ne s'applique pas aux autres |

## 9. Points ouverts

- **E09, taux d'échec sur la validité de date** : sur un échantillon récent, environ 11,7 % des
  lignes portent une échéance au jour même ou antérieure à la création. À qualifier avec le
  métier — défaut de qualité réel, ou situation normale d'échéances arrivées à terme avant mise à
  jour du statut.
- **E11, rattachement des anomalies aux champs du rapport** : chaque règle est rattachée à un seul
  champ candidat. Le rattachement est documenté dans le module et reste à confirmer.
- **E11, tolérance de la règle arithmétique** : valeur de travail, non arrêtée par le métier.
- **Extraction ad hoc** : disponible pour E07, E10 et E11, non portée pour E08 et E09.
- **Chaîne par champ** : aucun test automatisé, et la mémoire des valeurs validées de `Devise` est
  commune à toutes les APIs — une valeur tranchée pour un endpoint s'applique à tous. La chaîne par
  API corrige ce point.
