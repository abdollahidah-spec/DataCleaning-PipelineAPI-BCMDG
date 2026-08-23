# Documentation fonctionnelle — E11_RDCC

## Champs catégoriels

### NomCorrespondant
Cascade : cache warm-start (résolutions Claude déjà validées) → référentiel BCM exact →
fallback API Claude (identification d'une vraie banque avec un vrai code SWIFT/BIC publié,
sinon `OUTLIER`). Voir `e11_rdcc/fields/nomcorrespondant.py`.

### Devise
Cascade purement déterministe (pas de LLM) : liste de bruit connu → code ISO 4217 exact →
code numérique → alias → nettoyage de repli (description entre parenthèses, caractères non
alphanumériques, préfixe 3 lettres). Voir `e11_rdcc/fields/devise.py`.

### Règle NA (commune aux deux champs) — GLOBALE, corrigée sur demande du Business Analyst
```
Champ == 'NA'  ET  Ref == 'NA'   -> 'NA'
Champ == 'NA'  ET  Ref != 'NA'   -> OUTLIER
Champ vide / null / non identifié -> OUTLIER
```
Pour E11, `Ref` = **`_E11_GlobalNoActivite`**, une colonne synthétique précalculée UNE SEULE FOIS
pour NomCorrespondant ET Devise ensemble (voir `e11_rdcc/global_na.py` et
`E11Pipeline.preprocess()`) — corrige un bug où NomCorrespondant et Devise décidaient chacun
indépendamment en ne regardant que `NumCompte`, sans vérifier l'autre champ ni les 4 montants.
`_E11_GlobalNoActivite` vaut `'NA'` seulement si TOUT le gabarit du ticket est respecté à la fois
(`NomCorrespondant`, `Devise`, `NumCompte` tous `'NA'` ET les 4 montants tous à 0), sinon `''`.

---

## Cohérence numérique (SoldesRDCC)

Les 5 champs `SoldeDebutJournee`, `TotalMvtsDebiteursJournee`, `TotalMvtsCrediteurs`,
`SoldeFinJournee`, `DateFinJournee` ne sont pas des catégories à normaliser — ce sont des
valeurs continues soumises à des règles de cohérence métier. Voir
`e11_rdcc/fields/numeric_coherence.py`.

### Règle 1 — ARITHMETIC
```
SoldeFinJournee == SoldeDebutJournee + TotalMvtsCrediteurs - TotalMvtsDebiteursJournee
```
(tolérance configurable, `tolerance.absolute`, défaut 0.01). Un champ non numérique/manquant
est lui-même une anomalie.

### Règle 2 — TEMPORAL_CONTINUITY
```
SoldeFinJournee(jour J) == SoldeDebutJournee(jour J+1)
```
pour le même compte, regroupé par `grouping_key_temporal_continuity` — CONFIRMÉ par le Business
Analyst (test métier) : `[RefBanque, NomCorrespondant, NumCompte, Devise]`. Toujours configurable
en YAML si besoin de le revalider ultérieurement.
Les lignes "sans activité" (voir règle 3) sont exclues du chaînage — leurs champs identifiants
sont tous `NA`, elles ne désignent aucun compte précis.

Deux lignes à la même date pour un même compte → anomalie `ERROR` (ambiguïté, impossible de
choisir laquelle chaîner). Un écart de plus d'un jour entre deux relevés consécutifs → `WARNING`
(informationnel, pas nécessairement une erreur).

### Règle 3 — NO_ACTIVITY_CONFORMITY
Modélise le gabarit "pas d'activité" donné par le ticket :
```json
{"banque":"string","nomCorrespondant":"NA","numCompte":"NA","devise":"NA",
 "soldeDebutJournee":0,"totalMvtsDebiteursJournee":0,"totalMvtsCrediteurs":0,
 "soldeFinJournee":0,"dateFinJournee":"2024-04-11"}
```
- Aucun champ identifiant (`NomCorrespondant`, `Devise`, `NumCompte`) à `NA` → ligne normale,
  pas de vérification.
- **Certains** à `NA` mais pas tous → anomalie ("NA partielle").
- **Tous** à `NA` → ligne "sans activité" légitime ; anomalie **seulement** si un des 4 champs
  numériques n'est pas exactement 0 (tolérance flottante 1e-9, pas la tolérance métier — c'est
  une vérification de gabarit, pas une réconciliation).

Une ligne peut échouer à la fois la règle 1 et la règle 3 (ex : taguée "sans activité" mais
avec un solde non nul) — les deux anomalies sont rapportées indépendamment, jamais l'une
masquée par l'autre.

### Règle 4 — DATE_VALIDITY
`DateFinJournee` doit être une date valide, et `DateFinJournee <= dtCr` (date de création/
ingestion de la ligne en base). Le ticket écrivait littéralement `soldeFinJournee < dtCr`
(comparaison nombre/date incohérente) — confirmé par le métier comme une coquille pour
`dateFinJournee <= dtCr`.

---

## Livrables

Voir le tableau dans [README.md](../README.md#outputs). Deux fichiers par run (mode non-`file`) :

1. **Classeur Excel** (`E11_RDCC_classification.xlsx`) — toujours envoyé, feuilles de
   classification par champ + `Anomalies_Numeriques` + `Instructions`. Chemin stable SharePoint,
   écrasé à chaque run.
2. **`Rapport_Qualite_Outliers_E11_RDCC_{yyyymmdd}.pdf`** — UN SEUL fichier PDF, deux sections
   (saut de page entre les deux, `E11Pipeline.build_reports_markdown()`) :
   - *Rapport de qualité* : statistiques générales + indicateurs de performance de CE run
     (`shared/report_templates.py::build_quality_report_markdown`, générique, gabarit texte
     validé par le Business Analyst).
   - *Rapport des outliers* : répartition par champ traité (7 lignes fixes : `nomCorrespondant`,
     `devise`, `soldeDebutJournee`, `totalMvtsDebiteursJournee`, `totalMvtsCrediteurs`,
     `soldeFinJournee`, `dateFinJournee`) et par RefBanque (`e11_rdcc/reports.py`, mapping propre
     à E11 passé à `shared/report_templates.py::build_outliers_report_markdown`). Le mapping des
     4 règles numériques vers ces 7 champs est un choix documenté dans le module (chaque règle
     n'est rattachée qu'à un seul champ candidat) — à valider avec le métier si besoin d'ajustement.

Les deux pièces jointes accompagnent l'email OK (voir [README.md](../README.md) pour le gabarit
exact du corps). Le rapport de qualité au format tableau n'est plus écrit comme feuille du
classeur — il reste disponible dans les logs structurés et dans le PDF ci-dessus.

**Colonnes de la feuille `Anomalies_Numeriques`** (demande métier) : `NumCompte`, `RefBanque`,
les 5 colonnes numériques/date (`SoldeDebutJournee`, `TotalMvtsDebiteursJournee`,
`TotalMvtsCrediteurs`, `SoldeFinJournee`, `DateFinJournee`), `dtCr` (ajouté sur demande du
Business Analyst pour faciliter le test du contrôle de continuité temporelle), `Rule`, `Detail`.
`Rule`/`Detail` sont conservés au-delà de la demande stricte — sans eux, une ligne en échec sur
deux règles différentes produirait deux lignes visuellement identiques dans le fichier,
impossibles à distinguer. `NomCorrespondant`, `Devise`, `Delta`, `Severity` restent calculés en
interne (rapport de qualité) mais ne sont plus affichés dans cette feuille — voir
`e11_rdcc/fields/numeric_coherence.py::SHEET_COLUMNS` pour ajuster.

## Boucle de correction (Instructions)

L'onglet `Instructions` du classeur est pré-rempli automatiquement (une ligne par valeur
outlier distincte, par champ) avec les colonnes `Champ | Input | Label_Attendu`. Une équipe
métier renseigne `Label_Attendu`, puis `apply_corrections.py` route chaque ligne vers le cache
warm-start du champ concerné (déduit de `Champ`) — la correction prime dès le run suivant. Les
champs numériques n'ont pas de cache warm-start (pas de concept de "correction" pour une
anomalie de cohérence) — les lignes `Champ` les concernant sont ignorées avec un avertissement.

## Extraction ad hoc

`e11_rdcc/ad_hoc_extraction.py` — outil indépendant du run automatisé, pour un analyste qui veut
sa propre extraction (requête SQL personnalisée, table, ou fichier local). Applique le nettoyage
normal à tout champ configuré dont la colonne source est présente dans le résultat ; les champs
dont une colonne manque sont ignorés silencieusement (pas d'erreur — une requête personnalisée
peut légitimement ne couvrir que certains champs). Écrit une extraction locale (colonnes de la
requête + leurs versions nettoyées insérées juste après), sans toucher SharePoint/email/état
incrémental. Voir [README.md](../README.md#extraction-ad-hoc-requête-sql-personnalisée). (Outil
disponible pour E11 uniquement à ce jour — pas encore porté pour E09.)

---

## E09_PE (BCMDG-223)

Même approche qu'E11, en plus simple — voir [e09_pe/](../e09_pe/) et le tableau dans
[README.md](../README.md#champs-traités--e09_pe).

### Devise
Cascade identique à celle d'E11 (référentiel ISO 4217, pas de LLM) — voir `e09_pe/fields/devise.py`.
Cache warm-start propre à E09 (`validated_classif_devise_e09_pe.json`), indépendant de celui d'E11.

### Règle NA (Devise)
Témoin simple `NumCredoc` (`Devise == 'NA' ET NumCredoc == 'NA' -> 'NA'`, sinon `OUTLIER`) — pas
de correctif "NA globale" comme pour E11, car E09 n'a qu'un seul champ catégoriel : pas de risque
que deux champs se contredisent sur un témoin partiel (le problème que corrigeait `global_na.py`
pour E11 n'existe pas ici).

### Echeances (MontantEcheance, DateEcheance) — 2 règles, ligne à ligne
Contrairement aux 4 règles d'E11 (dont une cohérence J/J+1 entre lignes), le ticket BCMDG-223 ne
demande que 2 contrôles indépendants, sans mémoire entre lignes — voir `e09_pe/fields/echeances.py` :

- **AMOUNT_POSITIVE** : `MontantEcheance > 0`. Valeur non numérique ou manquante = anomalie.
- **DATE_VALIDITY** : `DateEcheance` parsable ET strictement postérieure à `dtCr` (comparaison au
  jour calendaire — `DateEcheance` est toujours minuit dans les données réelles, `dtCr` porte une
  heure de création précise ; les deux étant naturellement alignés sur le jour, aucune troncature
  supplémentaire n'était nécessaire, vérifié contre un échantillon réel de 5000 lignes). C'est le
  sens INVERSE de la règle 4 d'E11 (`<=`) : ici une échéance prévisionnelle doit être future, pas
  antérieure ou égale à la date de création — pas une coquille du ticket, le sens métier est
  cohérent tel quel.

`NumCredoc` n'est pas normalisé — conservé comme identifiant dans `Anomalies_Echeances`, comme
`NumCompte` pour E11.

**Observation sur données réelles** (échantillon de 5000 lignes récentes, 2026-08-18) : ~11,7 %
des lignes échouent `DATE_VALIDITY` (échéance le jour même ou antérieure à la création) — à faire
valider par le métier lors du test : signal de qualité de données réel à investiguer, ou situation
normale (ex: échéances arrivées à terme avant mise à jour du statut) ?
