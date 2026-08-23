# Configuration

## `.env`

Copier `.env.example` vers `.env` et renseigner :

| Variable | Utilisation |
|---|---|
| `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_DRIVER` | Connexion SQL Server — **lecture seule** sur toutes les tables sources (E11, E09, ...) — aucune écriture/DDL autorisée sur cette base |
| `ANTHROPIC_API_KEY` | Fallback Claude — NomCorrespondant (E11, E08), Produits/NomDonneurOrdre/Beneficiaire/Pays (E08). Beneficiaire utilise en plus l'outil serveur `web_search` (facturé à l'usage) |
| `DGI_BASE_PATH`, `PUBLIC_ENT_PATH` | Fichiers de référence externes (E08 : `NomDonneurOrdre`, `Beneficiaire`) — base fiscale DGI et liste des entreprises publiques. Toujours un chemin explicite en `.env`, jamais en dur dans le code (voir `e08_ocd/fields/_entity_matching.py`). `DGI_BASE_PATH` absent → erreur claire au chargement ; `PUBLIC_ENT_PATH` absent → liste vide, dégradé propre (pas d'erreur, juste aucune entreprise publique reconnue) |
| `OUTPUT_BASE` | **Stockage local/réseau — pas besoin de SharePoint.** Vide = livrables dans `{api}/outputs/` (ex: `e11_rdcc/outputs/`, `e09_pe/outputs/` — fonctionne déjà). Renseigné (ex: `D:\BCM_Outputs` ou `\\serveur\partage`) = tous les livrables écrits sous `{OUTPUT_BASE}/{api}/outputs/...` à la place |
| `STATE_DIR` | **État incrémental — fichier JSON local, pas une table SQL.** Vide = `state/` à la racine du repo, un fichier par API (`state/E11_RDCC_run_state.json`, `state/E09_PE_run_state.json`...). Voir `shared/state_store.py` |
| `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_SITE_URL`, `SHAREPOINT_FOLDER_PATH` | Upload SharePoint (optionnel, pour plus tard — si absent, l'upload est simplement ignoré, pas d'erreur) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_APP_PASSWORD`, `EMAIL_TO` | Notifications email OK/KO (optionnel, pour plus tard — si absent, notification simplement ignorée). Voir [README.md](../README.md#notifications-email) pour générer le mot de passe d'application Gmail |
| `EMAIL_MAX_ATTACHMENT_MB` | Taille max de la pièce jointe email avant omission (défaut 15) |
| `LOG_LEVEL` | Niveau de log (défaut `INFO`) |

## `e11_rdcc/config/E11_RDCC.yaml`

Fusionné en profondeur avec `shared/config_base.yaml` (le YAML spécifique surcharge uniquement
les clés qu'il redéfinit — voir `shared/config.py::load_config`).

### Clés principales

- `input.table_name` / `input.dt_cr_column` — table source et colonne utilisée pour le delta.
- `load.mode` — `auto` (défaut, déduit de l'état en base) / `initial` / `incremental`.
- `fields[]` — liste des champs traités, un bloc par champ :
  - `type: categorical` — nécessite `columns.field`, `columns.field_out`, `columns.ref_transaction`,
    `columns.ref_banque`, `referentiel_path`. `NomCorrespondant` a en plus un bloc `llm:`.
    Pour E11, `ref_transaction: "_E11_GlobalNoActivite"` (voir règle NA ci-dessous).
  - `type: numeric_coherence` — nécessite `columns.{solde_debut,mvts_debiteurs,mvts_crediteurs,
    solde_fin,date_fin,dt_cr,ref_banque,nom_correspondant,devise,num_compte}`, `tolerance.absolute`,
    `grouping_key_temporal_continuity` (CONFIRMÉ par le Business Analyst :
    `[RefBanque, NomCorrespondant, NumCompte, Devise]`).

**Règle NA** (`columns.ref_transaction`, champs catégoriels) : `field == 'NA' ET ref_transaction
== 'NA'` → `'NA'` (ligne "sans activité" légitime) ; `field == 'NA' ET ref_transaction != 'NA'`
→ `OUTLIER` (NA suspecte). Correctif Business Analyst : ce témoin ne peut pas être une seule
colonne (ex: NumCompte seul) — la non-activité légitime doit être vérifiée GLOBALEMENT sur tout
le gabarit du ticket en une fois (NomCorrespondant, Devise, NumCompte tous 'NA' ET les 4 montants
tous à 0). `ref_transaction` pointe donc vers `_E11_GlobalNoActivite`, une colonne synthétique
précalculée une seule fois pour tous les champs (voir `e11_rdcc/global_na.py` et
`E11Pipeline.preprocess()`), et non vers NumCompte directement.
- `output.local_dir` / `output.classification_path` — chemin de sortie local (sous `OUTPUT_BASE`
  si défini) et chemin SharePoint stable (écrasé à chaque run) du classeur unique de sortie.
- `email.enabled` / `email.attach_workbook_max_mb`.
- `instructions.prefill` — pré-remplissage automatique de l'onglet Instructions (défaut `true`).
- `schedule` — métadonnées uniquement (documentation de l'intention Task Scheduler), non lues
  par le code.

### Points explicitement différés par le métier (`TODO-VALIDATE` dans le YAML)

- Tolérance métier (`tolerance.absolute`).

Les noms de colonnes SQL et la clé de regroupement pour la continuité temporelle sont CONFIRMÉS
par le Business Analyst (test métier) : `[RefBanque, NomCorrespondant, NumCompte, Devise]` — ce
ne sont plus des placeholders.

Ces valeurs sont des placeholders raisonnables, pas des hypothèses figées dans le code — un
changement se fait uniquement dans le YAML.

## `e09_pe/config/E09_PE.yaml`

Même structure que E11 (fusion avec `shared/config_base.yaml`), en plus simple :

- `input.table_name: "E9EtatBcmPrevisionEcheances"`.
- `fields[]` :
  - `Devise` (`type: categorical`) — référentiel ISO 4217 identique à E11, cache warm-start
    propre à E09. `ref_transaction: "NumCredoc"` (témoin NA simple — un seul champ catégoriel sur
    E09, donc pas besoin du correctif "NA globale" d'E11, voir `e09_pe/fields/devise.py`).
  - `Echeances` (`type: numeric_validation`) — nécessite `columns.{montant_echeance,date_echeance,
    dt_cr,ref_banque,num_credoc}`. 2 règles indépendantes (pas de cohérence inter-lignes, pas de
    `grouping_key`, pas de `tolerance`) : `AMOUNT_POSITIVE` (montant > 0) et `DATE_VALIDITY`
    (date d'échéance strictement postérieure à `dtCr`) — voir `e09_pe/fields/echeances.py`.
- `email.display_name: "E09 – PE"` — libellé utilisé dans le sujet/corps de l'email.

**Colonnes SQL confirmées** (vérifié directement contre la table de production le 2026-08-18,
`SELECT TOP 1 * FROM E9EtatBcmPrevisionEcheances`) : `NumCredoc`, `RefBanque`, `Devise`,
`MontantEcheance`, `DateEcheance`, `dtCr` — les noms par convention PascalCase utilisés dans le
YAML correspondent exactement aux colonnes réelles, aucun ajustement nécessaire.

## `e08_ocd/config/E08_OCD.yaml`

`input.table_name: "E8EtatBcmOuvertureCreditDocumentaires"`. 6 champs, tous `type: categorical` :

- `Devise`, `NomCorrespondant`, `Produits` — mêmes patterns qu'E11/E09 (référentiel + fallback
  Claude). `Produits` construit son référentiel depuis `req/DataCleaning_E08 OCD_Produits.xlsx`.
- `NomDonneurOrdre` — nécessite en plus `columns.nif_nni` (optionnel : si absente des données,
  l'étape de matching NIF exact est simplement sautée) et un bloc `matching` :
  `strong_threshold`/`strong_gap`/`arbitrage_min`/`batch_size` (seuils rapidfuzz contre la base
  DGI — valeurs reprises telles quelles de l'ancien repo, déjà validées en prod sur E07/E08).
- `Beneficiaire` — bloc `llm.web_search_max_uses` (défaut 3) : nombre max de recherches web
  facturées par valeur à résoudre.
- `Pays` — pas de référentiel Excel BCM dédié (le référentiel est construit en code depuis
  pycountry/babel/geonamescache) ; `referentiel_path` pointe vers `pays_addr_keywords.json` (le
  seul fichier référentiel réellement utilisé par la cascade) pour satisfaire la validation de
  config générique, qui exige `referentiel_path` pour tout champ `categorical`.

Tous les champs catégoriels partagent le même témoin NA (`ref_transaction: "NumCredoc"`),
chacun indépendamment — voir `e08_ocd/config/E08_OCD.yaml` pour le raisonnement complet (pas de
correctif "NA globale" comme pour E11 : aucun gabarit "sans activité" multi-champs documenté
pour E08).

**Décision confirmée avec l'utilisateur** : le champ Pays bascule d'Ollama/qwen2.5:14b (modèle
local, ancien repo) vers Claude — cohérent avec le reste du repo (aucune infra locale à héberger).
Contrairement à l'ancien repo, les résolutions Claude de Pays sont maintenant mises en cache
(`validated_classif_pays_e08_ocd.json`) — Claude étant facturé à l'appel, contrairement à Ollama.

**Filtre SITUATION de la base DGI** : décision confirmée de reproduire le comportement de
l'ancien repo tel quel — aucun filtre sur `SITUATION` (ACTIF/EN CESSATION), une entreprise en
cessation d'activité reste matchable comme une entreprise active.

## Installation

Voir [README.md](../README.md#installation).
