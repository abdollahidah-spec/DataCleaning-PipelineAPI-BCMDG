# Règles métier

Règles communes à tous les endpoints, et lectures retenues là où l'énoncé d'origine était
ambigu. Le détail des champs, des cascades et des contrôles propres à un endpoint figure dans le
README de son package : [e07_fs](../e07_fs/README.md), [e08_ocd](../e08_ocd/README.md),
[e09_pe](../e09_pe/README.md), [e10_fe](../e10_fe/README.md), [e11_rdcc](../e11_rdcc/README.md).

## Règle NA

Une valeur `NA` n'est légitime que si la ligne est bien une déclaration sans activité. Le témoin
de non-activité est déclaré par champ (`columns.ref_transaction`) :

```
champ == 'NA'  ET  témoin == 'NA'   -> 'NA'
champ == 'NA'  ET  témoin != 'NA'   -> OUTLIER
champ vide, nul, ou non identifié   -> OUTLIER
```

La règle est appliquée en dernier, après la cascade, et vectorisée (`shared/na_rule.py`). Elle
n'est jamais redéfinie dans un package : un test de cohérence le vérifie.

Deux particularités :

- **E11** — le témoin ne peut pas être une seule colonne. Une non-activité légitime se vérifie
  sur tout le gabarit à la fois : `NomCorrespondant`, `Devise` et `NumCompte` tous à `NA` **et**
  les quatre montants à zéro. Le témoin est donc une colonne synthétique précalculée une fois
  pour tous les champs (`e11_rdcc/global_na.py`). Un témoin partiel laissait deux champs décider
  indépendamment et se contredire.
- **Pays** — `NA` est le code ISO de la Namibie. Une déclaration sans activité porte donc la
  valeur `NoAs` pour ce champ, et non `NA`.

## Valeur déjà conforme au référentiel (`MAP_CIBLE`)

Un référentiel associe un libellé brut à une valeur normalisée. Un libellé qui **est déjà** la
valeur normalisée n'en est donc pas une clé : sans traitement particulier il partait en
résolution payante ou en rapprochement fiscal, pouvait revenir `OUTLIER`, puis être mémorisé
comme tel.

L'étape compare la valeur, insensible à la casse, à l'ensemble des valeurs cibles du référentiel
(`shared/referentiel_cibles.py`). Elle intervient juste après la mémoire des valeurs validées —
une correction métier garde donc la priorité — et s'applique à tous les champs adossés à un
référentiel de libellés : `NomCorrespondant`, `NomDonneurOrdre`, `Beneficiaire`, `Produits`.

## Boucle de correction

L'onglet `Instructions` du classeur livré porte l'**historique en lecture seule** des corrections
déjà appliquées, en trois colonnes — `Champ | Input | Label_Attendu`. Il est vide à la première
exécution. Ce n'est pas une liste de valeurs à corriger : celles-ci se lisent dans les feuilles de
classification, lignes `OUTLIER`, et dans le rapport d'outliers.

Pour soumettre des corrections, le métier fournit un classeur comportant un onglet `Instructions`
aux mêmes trois colonnes, une ligne par valeur corrigée. `{api}/apply_corrections.py` route chaque
ligne vers la mémoire des valeurs validées du champ nommé, et la correction prime dès l'exécution
suivante, avec la méthode `WARM`. Une ligne sans `Label_Attendu` est ignorée avec un décompte, un
champ inconnu signalé par un avertissement, un champ de contrôle numérique écarté — il n'a pas de
mémoire de valeurs validées.

Chaque correction est enregistrée sous trois formes de clé : libellé nettoyé, valeur brute, valeur
brute en majuscules. Les champs ne cherchent pas tous la même forme dans leur mémoire — libellé
nettoyé pour `Devise`, `TypeSwift` et `NomDonneurOrdre`, valeur brute en majuscules pour
`NatureEconomique` et `Pays`. Sans cela, une correction dont le nettoyage modifie la valeur
restait sans effet et sans message.

## Contrôles numériques et de date

Trois endpoints portent des champs qui ne se normalisent pas et produisent un rapport d'anomalies
au lieu d'une mapping table. Une ligne en échec sur plusieurs règles produit une anomalie par
règle : aucune n'est masquée par une autre.

| Endpoint | Champs contrôlés | Règles | Détail |
|---|---|---|---|
| E11 | soldes et dates de relevé | 4, dont un chaînage J / J+1 entre lignes | [e11_rdcc/README.md](../e11_rdcc/README.md) |
| E09 | montant et date d'échéance | 2, ligne à ligne | [e09_pe/README.md](../e09_pe/README.md) |
| E07, E10 | montant, taux de change, date de transaction, conformité des messages sans activité | 4, ligne à ligne | [e07_fs/README.md](../e07_fs/README.md) |

## Lectures retenues

Points où l'énoncé d'origine était ambigu ou incohérent, et la règle effectivement implémentée.

| Point | Règle retenue |
|---|---|
| E11, validité de la date de relevé | L'énoncé comparait un solde à une date (`soldeFinJournee < dtCr`). La règle implémentée est `DateFinJournee <= dtCr`, comparaison au jour calendaire |
| E09, sens de la validité de date | `DateEcheance` doit être **postérieure** à `dtCr` : une échéance prévisionnelle est à venir. C'est le sens inverse de la règle E11, et il est cohérent tel quel |
| E11, chaînage temporel | Regroupement `[RefBanque, NomCorrespondant, NumCompte, Devise]`. Deux relevés à la même date pour un même compte : anomalie bloquante, le chaînage étant ambigu. Écart de plus d'un jour : avertissement |
| Base fiscale, entreprises en cessation | Aucun filtre sur la situation : une entreprise en cessation reste rapprochable comme une entreprise active |
| Champ `Pays` | Résolu par appel au modèle de langage, et les résolutions sont mémorisées — l'appel étant facturé à l'usage |
| E07 et E10, profondeur d'historique | Le chargement initial est borné aux lignes créées depuis le 01/01/2024 |
| E07 et E10, champ `Produit` | Hors périmètre de normalisation : seulement contrôlé par le gabarit des messages sans activité |

## Points ouverts

- **E09, taux d'échec sur la validité de date** : sur un échantillon récent, environ 11,7 % des
  lignes portent une échéance au jour même ou antérieure à la création. À qualifier avec le
  métier — défaut de qualité réel, ou situation normale d'échéances arrivées à terme avant mise à
  jour du statut.
- **E11, rattachement des anomalies aux champs du rapport** : chaque règle numérique est rattachée
  à un seul champ candidat. Le rattachement est documenté dans
  `e11_rdcc/fields/numeric_coherence.py` et reste à confirmer.
- **E11, tolérance de la règle arithmétique** (`tolerance.absolute`) : valeur de travail, non
  arrêtée par le métier.
- **Extraction ad hoc** : disponible pour E07, E10 et E11, non portée pour E08 et E09.
