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

## 2. Les étapes de résolution

Les deux chaînes appliquent la **même escalade**, de l'étape la plus rapide et gratuite à la plus
lente et coûteuse. La première étape qui tranche l'emporte, et la **méthode** qui a résolu la
valeur est conservée dans les livrables : elle distingue ce qui était déjà connu de ce qui a été
tranché pendant l'exécution.

Toute la cascade travaille sur les **valeurs distinctes** du champ, jamais ligne par ligne, puis le
résultat est rediffusé sur les lignes. Une valeur rencontrée mille fois est donc résolue une fois.

### Étape 0 — Mémoire des valeurs validées

Consultée avant tout le reste, elle court-circuite la cascade entière : résolutions déjà obtenues,
et corrections métier déjà appliquées. Méthode `WARM`.

Différence importante entre les deux chaînes : dans la chaîne par API la mémoire est **toujours**
consultée ; dans la chaîne par champ elle ne l'est que si l'option `--warm-start` est passée. Sans
cette option, toutes les valeurs repartent dans la cascade, y compris les résolutions déjà payées.

### Étape 1 — Nettoyage basique

Aucune décision métier, seulement la mise en forme de la valeur brute : normalisation Unicode,
casse, accents, ponctuation et caractères de contrôle, espaces multiples, bruit de saisie connu,
tokens parasites. Une fonction par champ — `clean_devise`, `clean_pays`, `clean_typeswift`,
`clean_mode_reglement`, `clean_produits`, `clean_nomcorrespondant`, `nettoyer` pour
`NatureEconomique`, `clean_label` pour les champs de noms.

Deux sorties en découlent : la valeur nettoyée, conservée dans les livrables, et pour les noms une
forme **hyper-normalisée** (`hyper_normaliser`) qui ne sert qu'à la correspondance exacte de
l'étape suivante.

Cette étape écarte déjà des valeurs : bruit connu, en-tête de colonne, montant, date, adresse,
modalité vague — classés `NOISE` ou `OUTLIER` sans aller plus loin.

### Étape 2 — Mapping direct

Correspondance **exacte** avec une référence, donc sans aucune incertitude. Plusieurs formes :

| Méthode | Correspondance exacte avec |
|---|---|
| `MAP` | une entrée du référentiel |
| `MAP_CIBLE` | une valeur **cible** du référentiel — la valeur est déjà normalisée, comparaison insensible à la casse |
| `ALIAS` | une variante connue d'une entrée (`Devise`, `ModeReglement`) |
| `NUM` | un code numérique de devise |
| `PREFIX` | un numéro seul complété en code de message valide (`TypeSwift`) |
| `ADDR` | un mot-clé d'adresse désignant un pays |
| `NIF_EXACT` | un identifiant fiscal valide présent tel quel dans la base fiscale |
| `PUBLIC_ENT` | une entreprise publique de la liste de référence |

S'y ajoute, pour les champs de noms, une **classification locale** du libellé par règles : personne
physique (`PARTICULIER`), établissement personnel (`ETS_PERSONNEL`), établissement non
identifiable (`ETS_OUTLIER`). Et pour `NatureEconomique`, une classification par **règles de
mots-clés** sur le libellé nettoyé (`REGLE` dans la chaîne par champ).

### Étape 3 — Rapprochement approché (rapidfuzz)

Première étape à accepter une incertitude, donc la première à exiger des seuils. Elle n'appelle
aucun modèle et reste gratuite. Deux usages :

| Champ | Mécanisme | Seuils |
|---|---|---|
| `Pays` | `rapidfuzz.process.extractOne` contre les noms de pays du référentiel | score minimum 93, constante du module |
| Champs de noms | `rapidfuzz.process.cdist` : meilleurs candidats de la base fiscale pour chaque libellé, par lots pour borner la mémoire, tous les cœurs mobilisés | score au moins 92 **et** écart au moins 8 avec le second candidat : correspondance nette (`DGI_FUZZY_STRONG`). Score sous 70 : aucun candidat plausible (`DGI_NO_MATCH`), sans appel au modèle. Entre les deux : arbitrage à l'étape 5 |

L'exigence d'un **écart** avec le second candidat est le point déterminant : un score élevé ne
suffit pas, la mesure produisant des scores hauts par accident sur des libellés courts. Deux
raisons sociales proches sans écart franc ne sont donc jamais tranchées automatiquement.

Les seuils des champs de noms sont des réglages du YAML ; celui de `Pays` est une constante du
module.

Cette étape est la plus longue d'un premier chargement — de l'ordre de 25 minutes pour quelques
milliers de libellés nouveaux contre une base de quelque 52 000 raisons sociales. Ses résultats
déterministes sont donc mémorisés à part, avec une empreinte du fichier fiscal.

### Étape 4 — Similarité sémantique (transformers)

**Chaîne par champ uniquement**, et pour le seul champ `NatureEconomique`. Les libellés du
référentiel et les valeurs à résoudre sont encodés en vecteurs par un modèle de phrases
(`sentence-transformers`, `mpnet-base-v2`, chargé depuis un chemin local), vecteurs normalisés,
puis comparés par similarité cosinus. Le meilleur label est retenu au-delà d'un seuil : **0,84**
en général, **0,95** pour les libellés courts, où une proximité de vecteurs est moins fiable.
Méthode `EMBED`.

Cette étape n'a pas été reprise dans la chaîne par API : `NatureEconomique` y passe directement du
mapping direct au client de modèle, sur la liste fermée des labels. Cela évite d'héberger un modèle
et ses dépendances d'apprentissage automatique.

### Étape 5 — Client de modèle, dernière étape

Appelée **en dernier**, uniquement sur les valeurs que rien n'a tranché : c'est la seule étape
payante, et la seule qui dépende d'un service externe. Quatre usages distincts :

| Usage | Champs | Ce qui est demandé au modèle |
|---|---|---|
| Liste fermée | `NatureEconomique`, `Produits` | choisir un label existant du référentiel, ou répondre `OUTLIER` — jamais un libellé inventé |
| Identification bancaire | `NomCorrespondant` | identifier une banque réelle disposant d'un code SWIFT/BIC publié |
| Arbitrage | noms, entité locale | trancher entre les candidats fiscaux proches laissés par l'étape 3 (`DGI_CLAUDE_ARBITRAGE`) |
| Recherche web | noms, entité étrangère | identifier l'entité et son nom légal par recherche web réelle, nombre de recherches plafonné par valeur |

Trois garde-fous encadrent cette étape :

- **lots parallèles**, le temps d'un appel étant presque entièrement de l'attente réseau ;
- **plafond de valeurs nouvelles** par exécution et par champ : au-delà, les valeurs restent
  `OUTLIER`, ne sont pas mémorisées, et sont reproposées à l'exécution suivante ;
- **mémorisation** de toute résolution obtenue, pour ne pas la repayer. Un **échec technique** ne
  l'est jamais : la valeur est reproposée à l'exécution suivante.

Le client diffère selon la chaîne : la chaîne par API appelle Claude pour tous les champs
concernés ; la chaîne par champ appelle Claude pour les champs de noms, et un modèle **local** Qwen
servi par Ollama pour `Pays` et `NatureEconomique` (méthode `LLM`).

### Étape finale — Règle NA, puis `OUTLIER`

La règle NA est appliquée après la cascade (voir §4). Ce qu'aucune étape n'a tranché devient
`OUTLIER` et part en validation métier ; une fois validé, le résultat rejoint la mémoire de
l'étape 0 et n'est plus retraité.

### Quelles étapes pour quel champ

| Champ | 1 nettoyage | 2 mapping direct | 3 rapidfuzz | 4 transformers | 5 modèle |
|---|---|---|---|---|---|
| Devise | oui | oui | — | — | — |
| TypeSwift | oui | oui | — | — | — |
| ModeReglement | oui | oui | — | — | — |
| Pays | oui | oui | oui | — | oui |
| NatureEconomique | oui | oui | — | chaîne par champ seulement | oui |
| NomCorrespondant | oui | oui | — | — | oui |
| Nom, entité locale | oui | oui | oui | — | oui, arbitrage |
| Nom, entité étrangère | oui | oui | — | — | oui, recherche web |
| Produits | oui | oui | — | — | oui |

Trois champs sont **entièrement déterministes** — `Devise`, `TypeSwift`, `ModeReglement` : ils
n'appellent jamais de modèle, et leur résultat est strictement reproductible.

### Glossaire des méthodes

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
| `REGLE` | classification par règles de mots-clés |
| `DGI_EXACT_NORM` | correspondance exacte après normalisation poussée contre la base fiscale |
| `DGI_FUZZY_STRONG` | correspondance approchée nette : score élevé et écart franc avec le candidat suivant |
| `DGI_CLAUDE_ARBITRAGE` | candidats fiscaux proches, tranchés par le modèle |
| `DGI_NO_MATCH` | aucun candidat plausible, aucun appel au modèle |
| `EMBED` | similarité sémantique par embeddings — chaîne par champ |
| `CLAUDE` | résolution par le client de modèle distant |
| `LLM` | résolution par le modèle local — chaîne par champ |
| `NA` | ligne sans activité légitime |
| `OUTLIER` | non résolu, à soumettre à validation métier |

## 3. Cascade, champ par champ

Chaque tableau donne la cascade **dans l'ordre d'exécution**, avec l'étape de l'escalade (§2) dont
relève chaque test. La première ligne qui tranche l'emporte.

### 3.1 Devise — sortie : code ISO 4217 alpha-3

| Ordre | Méthode | Étape | Ce qui est testé |
|---|---|---|---|
| 1 | `WARM` | 0 | mémoire des valeurs validées |
| 2 | — | 1 | nettoyage : espaces, suffixe `.0`, majuscules |
| 3 | `NOISE` | 1 | bruit connu du référentiel |
| 4 | `MAP` | 2 | code alpha-3 valide |
| 5 | `NUM` | 2 | trois chiffres correspondant à un code numérique ISO |
| 6 | `ALIAS` | 2 | variante connue d'un code |
| 7 | `STRIP` | 1 | trois replis successifs : description entre parenthèses retirée, caractères non alphanumériques retirés, préfixe de trois lettres — chacun confronté aux codes valides puis aux alias |
| 8 | `OUTLIER` | — | non identifié |

Aucun appel à un modèle : résultat strictement reproductible. Seul champ présent sur les cinq
endpoints.

### 3.2 TypeSwift — sortie : code de message SWIFT

| Ordre | Méthode | Étape | Ce qui est testé |
|---|---|---|---|
| 1 | `WARM` | 0 | mémoire, testée sur plusieurs formes de la valeur brute |
| 2 | `NOISE` | 1 | bruit connu, ou valeur ressemblant à un montant |
| 3 | `MAP` | 2 | code valide **du sens de flux** — la liste dépend du flux, sortant ou entrant |
| 4 | `PREFIX` | 2 | numéro seul à trois chiffres, complété en code de message si le résultat est valide |
| 5 | `OUTLIER` | — | non identifié |

### 3.3 ModeReglement — sortie : modalité validée

| Ordre | Méthode | Étape | Ce qui est testé |
|---|---|---|---|
| 1 | `WARM` | 0 | mémoire |
| 2 | `NOISE` | 1 | bruit connu |
| 3 | `MAP` | 2 | modalité valide du référentiel |
| 4 | `ALIAS` | 2 | variante connue |
| 5 | `OUTLIER` | — | non identifié |

### 3.4 Pays — sortie : code ISO-2, ou `NoAs`

| Ordre | Méthode | Étape | Ce qui est testé |
|---|---|---|---|
| 1 | `WARM` | 0 | mémoire |
| 2 | — | 1 | nettoyage et normalisation Unicode |
| 3 | `MAP` | 2 | référentiel — **construit en code** depuis les bibliothèques de codes pays, plus des alias manuels, et non depuis un fichier métier |
| 4 | `ADDR` | 2 | mot-clé d'adresse désignant un pays |
| 5 | `FUZZY` | 3 | rapprochement approché sur les noms de pays, score minimum 93 |
| 6 | `CLAUDE` ou `LLM` | 5 | résolution par le client de modèle |
| 7 | `NoAs` / `OUTLIER` | — | règle dédiée, voir §4 |

Ce champ a sa **propre** règle de non-activité, et c'est le seul : voir §4.

### 3.5 NatureEconomique — sorties : label normalisé **et** sa catégorie

| Ordre | Méthode | Étape | Ce qui est testé |
|---|---|---|---|
| 1 | — | — | valeur vide : non résolue, laissée à la règle NA |
| 2 | `WARM` | 0 | mémoire, testée sur plusieurs formes de la valeur brute |
| 3 | `NOISE` | 1 | en-tête de colonne, outlier évident — adresse, date, nom propre, nombre — ou modalité vague |
| 4 | `MAP` | 2 | label exact du référentiel, ou mapping direct |
| 5 | `REGLE` | 2 | classification par règles de mots-clés — chaîne par champ |
| 6 | `EMBED` | 4 | similarité sémantique, seuil 0,84 ou 0,95 sur libellé court — chaîne par champ |
| 7 | `CLAUDE` ou `LLM` | 5 | choix dans la **liste fermée** des labels : un label existant, ou `OUTLIER` — jamais un libellé inventé |
| 8 | `OUTLIER` | — | non identifié |

La catégorie du label est obtenue par consultation du référentiel, une fois le label retenu.

### 3.6 NomCorrespondant — sortie : nom légal de la banque

| Ordre | Méthode | Étape | Ce qui est testé |
|---|---|---|---|
| 1 | `WARM` | 0 | mémoire |
| 2 | `MAP_CIBLE` | 2 | la valeur est déjà un nom cible du référentiel |
| 3 | `MAP` | 2 | entrée du référentiel des banques validées |
| 4 | `CLAUDE` | 5 | identification d'une banque réelle disposant d'un code SWIFT/BIC publié |
| 5 | `OUTLIER` | — | non identifié |

### 3.7 Entité locale — sortie : raison sociale officielle en majuscules

`NomDonneurOrdre` sur E07 et E08, `Beneficiaire` sur E10. Cascade la plus longue, et la seule à
mobiliser la base fiscale. Dans la chaîne par champ, elle est sélectionnée par
`api_family: "DGI"`.

| Ordre | Méthode | Étape | Ce qui est testé |
|---|---|---|---|
| 1 | `WARM` | 0 | mémoire |
| 2 | `MAP_CIBLE` | 2 | la valeur est déjà une raison sociale cible du référentiel |
| 3 | `MAP` | 2 | entrée du référentiel des raisons sociales validées |
| 4 | — | — | raccourci par la règle NA |
| 5 | `PUBLIC_ENT` | 2 | entreprise publique de la liste de référence |
| 6 | `NOISE` | 1 | outlier évident |
| 7 | `NIF_EXACT` | 2 | identifiant fiscal valide présent tel quel dans la base fiscale |
| 8 | `PARTICULIER`, `ETS_PERSONNEL`, `ETS_OUTLIER` | 2 | classification locale : personne physique, établissement personnel, établissement non identifiable |
| 9 | `DGI_EXACT_NORM` | 2 | correspondance exacte après hyper-normalisation contre la base fiscale |
| 10 | `DGI_FUZZY_STRONG` | 3 | correspondance approchée **nette** : score au moins 92 et écart au moins 8 avec le second candidat |
| 11 | `DGI_CLAUDE_ARBITRAGE` | 5 | candidats proches sans écart franc : arbitrage par le modèle |
| 12 | `DGI_NO_MATCH` | 3 | meilleur score sous 70 : aucun candidat plausible, aucun appel au modèle |
| 13 | `OUTLIER` | — | non résolu |

Les étapes 9, 10 et 12 sont déterministes et gratuites : leurs résultats sont mémorisés à part,
avec une empreinte du fichier fiscal.

### 3.8 Entité étrangère — sortie : nom légal en majuscules

`Beneficiaire` sur E07 et E08, `NomDonneurOrdre` sur E10. La base fiscale nationale n'est pas
pertinente. Dans la chaîne par champ, cette cascade est sélectionnée par
`api_family: "CLAUDE_WEB"`.

| Ordre | Méthode | Étape | Ce qui est testé |
|---|---|---|---|
| 1 | `WARM` | 0 | mémoire |
| 2 | `MAP_CIBLE` | 2 | la valeur est déjà un nom cible du référentiel |
| 3 | `MAP` | 2 | entrée du référentiel |
| 4 | — | — | raccourci par la règle NA |
| 5 | `PUBLIC_ENT` | 2 | entreprise publique de la liste de référence |
| 6 | `NOISE` | 1 | outlier évident |
| 7 | `PARTICULIER`, `ETS_PERSONNEL`, `ETS_OUTLIER` | 2 | classification locale du libellé |
| 8 | `CLAUDE` | 5 | identification de l'entité et de son nom légal par **recherche web réelle**, nombre de recherches plafonné par valeur |
| 9 | `OUTLIER` | — | non résolu |

Seules les résolutions effectivement payées par une recherche web sont mémorisées.

### 3.9 Produits — sortie : libellé du référentiel métier

| Ordre | Méthode | Étape | Ce qui est testé |
|---|---|---|---|
| 1 | `WARM` | 0 | mémoire |
| 2 | `MAP_CIBLE` | 2 | la valeur est déjà un libellé cible du référentiel |
| 3 | `NOISE` | 1 | bruit connu |
| 4 | `MAP` | 2 | entrée du référentiel, construit depuis le fichier métier |
| 5 | `CLAUDE` | 5 | choix dans la liste fermée des libellés |
| 6 | `OUTLIER` | — | non identifié |

Champ propre à E08, absent de la chaîne par champ.

## 4. Règle NA

Une valeur `NA` n'est légitime que si la ligne est bien une déclaration sans activité. Le témoin
de non-activité est déclaré par champ :

```
champ == 'NA'  ET  témoin == 'NA'   -> 'NA'
champ == 'NA'  ET  témoin != 'NA'   -> OUTLIER
champ vide, nul, ou non identifié   -> OUTLIER
```

La règle est appliquée **en dernier**, après la cascade, et vectorisée. Une valeur nulle en base
n'y échappe pas : elle est ramenée à une chaîne vide avant comparaison, et devient donc `OUTLIER`.

Le témoin varie selon l'endpoint : la référence de transaction sur E07 et E10, le numéro de crédit
documentaire sur E08 et E09, une colonne synthétique sur E11.

Cette règle est la **seule logique métier partagée** entre tous les packages : elle vit dans un
module unique, et un test de cohérence vérifie qu'aucun package n'en redéfinit sa propre version.
Un défaut de traitement des valeurs nulles n'a ainsi été corrigé qu'une fois pour les cinq
endpoints.

### E11 — témoin global

Le témoin ne peut pas être une seule colonne. Une non-activité légitime se vérifie sur tout le
gabarit à la fois : nom du correspondant, devise et numéro de compte tous à `NA` **et** les quatre
montants à zéro. Le témoin est donc une colonne précalculée une seule fois pour tous les champs de
l'endpoint. Un témoin partiel laissait deux champs décider indépendamment et se contredire.

### Pays — la seule exception, et elle est inversée

`NA` est le code ISO-2 de la **Namibie**. Pour ce champ, `NA` est donc une valeur de pays
parfaitement légitime, ambiguë avec le témoin générique des autres champs. `Pays` a par conséquent
sa propre règle, délibérément distincte de la règle partagée :

```
Pays vide ou nul  ET  témoin vide ou nul   ->  NoAs   (bruit pur, les deux vides)
Pays == 'NA'      ET  témoin non vide      ->  'NA'   (Namibie, valeur légitime)
Pays == 'NA'      ET  témoin vide          ->  NoAs
valeur non reconnue comme un pays          ->  OUTLIER, quel que soit le témoin
```

La deuxième ligne est l'inverse de la règle générale : ailleurs, un `NA` avec un témoin non vide
est une anomalie ; ici, c'est la Namibie. C'est aussi pour cela que la non-activité se déclare
`NoAs` sur ce champ, et que les gabarits de messages sans activité d'E07 et E10 attendent `NoAs`
dans la colonne `Pays`.

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
