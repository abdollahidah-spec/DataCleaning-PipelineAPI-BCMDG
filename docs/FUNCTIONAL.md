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

### Règle NA (commune aux deux champs)
```
Champ == 'NA'  ET  Ref == 'NA'   -> 'NA'
Champ == 'NA'  ET  Ref != 'NA'   -> OUTLIER
Champ vide / null / non identifié -> OUTLIER
```
Pour E11, `Ref` = **`NumCompte`** (numéro de compte) pour NomCorrespondant ET Devise — c'est
la colonne "témoin" qui, avec le champ lui-même, permet de distinguer une ligne "sans activité"
légitime (les deux à `NA`) d'une `NA` suspecte (le champ à `NA` mais pas `NumCompte`).

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
pour le même compte, regroupé par `grouping_key_temporal_continuity` — défaut `[RefBanque,
NumCompte]` (NumCompte confirmé comme référence de compte, remplace l'ancien placeholder
`[RefBanque, NomCorrespondant, Devise]`, moins précis). Toujours configurable en YAML, à
valider avec le métier avant la mise en prod définitive.
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

Voir le tableau dans [README.md](../README.md#outputs) — un seul classeur
(`E11_RDCC_classification.xlsx`) avec les feuilles de classification, `Anomalies_Numeriques`
et `Instructions`. Le rapport de qualité (nombre de lignes traitées, taux de conformité, taux
de normalisation, taux d'outliers, répartition par RefBanque et par champ, temps d'exécution)
n'est plus écrit comme feuille du classeur — il reste disponible dans les logs structurés et
dans le corps de l'email de notification (stats du run + stats globales cumulées).

**Colonnes de la feuille `Anomalies_Numeriques`** (demande métier) : `NumCompte`, `RefBanque`,
les 5 colonnes numériques/date (`SoldeDebutJournee`, `TotalMvtsDebiteursJournee`,
`TotalMvtsCrediteurs`, `SoldeFinJournee`, `DateFinJournee`), `Rule`, `Detail`. `Rule`/`Detail`
sont conservés au-delà de la demande stricte ("NumCompte + RefBanque + colonnes numériques,
c'est tout") — sans eux, une ligne en échec sur deux règles différentes produirait deux lignes
visuellement identiques dans le fichier, impossibles à distinguer. `NomCorrespondant`, `Devise`,
`Delta`, `Severity` restent calculés en interne (rapport de qualité) mais ne sont plus affichés
dans cette feuille — voir `e11_rdcc/fields/numeric_coherence.py::SHEET_COLUMNS` pour ajuster.

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
incrémental. Voir [README.md](../README.md#extraction-ad-hoc-requête-sql-personnalisée).
