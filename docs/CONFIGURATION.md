# Configuration

## `.env`

Copier `.env.example` vers `.env` et renseigner :

| Variable | Utilisation |
|---|---|
| `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_DRIVER` | Connexion SQL Server — **lecture seule**, source E11 uniquement (aucune écriture/DDL autorisée sur cette base) |
| `ANTHROPIC_API_KEY` | Fallback Claude pour NomCorrespondant |
| `OUTPUT_BASE` | **Stockage local/réseau — pas besoin de SharePoint.** Vide = livrables dans `e11_rdcc/outputs/` (fonctionne déjà). Renseigné (ex: `D:\BCM_Outputs` ou `\\serveur\partage`) = tous les livrables écrits sous `{OUTPUT_BASE}/e11_rdcc/outputs/...` à la place |
| `STATE_DIR` | **État incrémental — fichier JSON local, pas une table SQL.** Vide = `e11_rdcc/state/` (fonctionne déjà). Voir `shared/state_store.py` |
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
    Pour E11, `ref_transaction: "NumCompte"` (voir règle NA ci-dessous).
  - `type: numeric_coherence` — nécessite `columns.{solde_debut,mvts_debiteurs,mvts_crediteurs,
    solde_fin,date_fin,dt_cr,ref_banque,nom_correspondant,devise,num_compte}`, `tolerance.absolute`,
    `grouping_key_temporal_continuity` (défaut `[RefBanque, NumCompte]`).

**Règle NA** (`columns.ref_transaction`, champs catégoriels) : `field == 'NA' ET ref_transaction
== 'NA'` → `'NA'` (ligne "sans activité" légitime) ; `field == 'NA' ET ref_transaction != 'NA'`
→ `OUTLIER` (NA suspecte). Pour E11, `ref_transaction = NumCompte` (numéro de compte) — c'est
lui qui, avec RefBanque et Devise, identifie une ligne "sans activité" dans l'exemple du ticket.
- `output.local_dir` / `output.classification_path` — chemin de sortie local (sous `OUTPUT_BASE`
  si défini) et chemin SharePoint stable (écrasé à chaque run) du classeur unique de sortie.
- `email.enabled` / `email.attach_workbook_max_mb`.
- `instructions.prefill` — pré-remplissage automatique de l'onglet Instructions (défaut `true`).
- `schedule` — métadonnées uniquement (documentation de l'intention Task Scheduler), non lues
  par le code.

### Points explicitement différés par le métier (`TODO-VALIDATE` dans le YAML)

- Noms exacts des colonnes SQL des 5 champs numériques/date.
- Orthographe exacte de `NumCompte` en base (son existence et son rôle de référence de
  transaction/compte sont confirmés — seul le nom littéral de la colonne SQL reste à valider).
- Tolérance métier (`tolerance.absolute`).

La clé de regroupement pour la continuité temporelle est maintenant `[RefBanque, NumCompte]`
(déduite de la confirmation ci-dessus) — reste à valider avec le métier avant la mise en prod
définitive, mais n'est plus un pur placeholder.

Ces valeurs sont des placeholders raisonnables, pas des hypothèses figées dans le code — un
changement se fait uniquement dans le YAML.

## Installation

Voir [README.md](../README.md#installation).
