# Configuration

Deux niveaux, dans les deux chaînes : les secrets et les chemins propres au poste dans `.env`, les
réglages du traitement dans les YAML. Un YAML ne redéfinit que ce qui change par rapport au socle
`shared/config_base.yaml` : la fusion est profonde.

L'organisation du code est décrite dans [ARCHITECTURE.md](ARCHITECTURE.md), les règles métier dans
[FUNCTIONAL.md](FUNCTIONAL.md).

## 1. `.env`

Copier `.env.example` vers `.env` et renseigner. Ce fichier n'est jamais versionné.

| Variable | Utilisation | Par champ | Par API |
|---|---|---|---|
| `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_DRIVER` | Connexion à la base de collecte, en **lecture seule stricte** : aucune écriture, aucun DDL | oui | oui |
| `ANTHROPIC_API_KEY` | Résolution assistée des champs qui la prévoient. Facturée à l'appel, et à l'usage pour la recherche web | oui | oui |
| `DGI_BASE_PATH` | Base fiscale du rapprochement des raisons sociales. Absente : erreur explicite au chargement | oui | oui |
| `PUBLIC_ENT_PATH` | Liste des entreprises publiques. Absente : liste vide, aucune entreprise publique reconnue, pas d'erreur | oui | oui |
| `OUTPUT_BASE` | Racine des livrables. Vide : sous le dossier du champ ou de l'endpoint. Renseignée : `{OUTPUT_BASE}/{API_ID}/`, un dossier par endpoint, jamais de collision en lancement parallèle | oui | oui |
| `STATE_DIR` | État incrémental et mémoires dérivées, un fichier JSON par endpoint. Vide : `state/` à la racine | — | oui |
| `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_SITE_URL`, `SHAREPOINT_FOLDER_PATH` | Dépôt distant, optionnel : absent, le dépôt est ignoré sans erreur | oui | oui |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_APP_PASSWORD`, `EMAIL_TO` | Notification de fin d'exécution, optionnelle : absente, la notification est ignorée sans erreur | — | oui |
| `EMAIL_MAX_ATTACHMENT_MB` | Taille maximale d'une pièce jointe avant omission (défaut 15) | — | oui |
| `LOG_LEVEL` | Niveau de journalisation (défaut `INFO`) | — | oui |

La chaîne par champ n'a ni état incrémental ni notification : elle n'utilise donc pas les
variables correspondantes.

## 2. Chaîne par API

### 2.1 Socle `shared/config_base.yaml`

| Clé | Défaut | Rôle |
|---|---|---|
| `pipeline_version` | `1.0.0` | version reportée dans les rapports et la notification |
| `input.type` | `sqlserver` | source : la base, ou un fichier local (`csv`, `excel`) |
| `input.sep`, `input.encoding`, `input.sheet` | `;`, `utf-8-sig`, `0` | lecture d'un fichier local. L'encodage et le séparateur déclarés sont essayés d'abord, puis des replis — un CSV exporté depuis Excel en français reste lisible |
| `load.chunk_size` | `50000` | lecture par paquets, pour une progression visible pendant un chargement long. `0` ou vide : lecture en un seul bloc |
| `load.initial_since` | vide | borne le chargement initial à partir de cette date |
| `load.cast_max_text` | `true` | conversion des colonnes texte volumineuses à la lecture, voir §4 |
| `load.retries`, `load.retry_wait_seconds` | `2`, `30` | reprise sur coupure réseau passagère |
| `output.format` | `csv` | format de l'extraction ad hoc |
| `llm.model` | `claude-sonnet-5` | modèle appelé pour la résolution assistée |
| `llm.batch_size` | `20` | valeurs par appel ; porté à `35` sur les champs à recherche web |
| `llm.max_retry`, `llm.retry_wait` | `3`, `2` | nouvelles tentatives sur un appel en échec, et attente entre deux |
| `llm.concurrency` | `4` | lots envoyés en parallèle |
| `llm.max_values_per_run` | vide | plafond de valeurs nouvelles par exécution et par champ |
| `reports.top_n_outliers_detail` | `100` | valeurs détaillées par champ dans le rapport PDF. `0` : aucune limite |
| `reports.max_anomaly_rows_excel` | `200000` | lignes d'anomalies écrites dans un onglet ; au-delà, l'onglet garde cet extrait et le détail intégral part dans un CSV à côté du classeur. `0` : aucune limite |
| `email.enabled`, `email.attach_workbook_max_mb` | `true`, `15` | notification, et taille maximale du classeur joint |

### 2.2 Structure d'un YAML d'endpoint

```yaml
api_id:   "E07_FS"
input:
  table_name:    "E7EtatBcmFluxSortants"   # table source
  dt_cr_column:  "dtCr"                    # colonne du delta
load:
  initial_since: 2024-01-01
fields:
  - name: "Devise"                         # sélectionne le module du package
    type: "categorical"                    # sélectionne la famille de traitement
    columns:
      field:           "Devise"            # colonne source
      field_out:       "Devise_Normalisée" # colonne produite
      ref_transaction: "ReferenceTransaction"  # témoin de la règle NA
      ref_banque:      "RefBanque"         # ventilation des outliers
    referentiel_path: "e07_fs/referentiel/devise_referentiel.json"
    outlier_tag: "OUTLIER"
output:
  local_dir:            "e07_fs/outputs/"
  classification_path:  "E07_FS_classification.xlsx"
email:
  display_name: "E07 – FS"
schedule:                                  # métadonnées, non lues par le code
  frequency: "weekly"
```

Clés exigées selon la famille déclarée :

| Famille | Clés obligatoires |
|---|---|
| `categorical` | `columns.field`, `columns.field_out`, `columns.ref_transaction`, `columns.ref_banque`, `referentiel_path` |
| `transaction_validation` | `columns.{montant_transaction, taux_de_change, date_transaction, dt_cr, ref_banque, reference_transaction, pays}` |
| `numeric_validation` | `columns.{montant_echeance, date_echeance, dt_cr, ref_banque, num_credoc}` |
| `numeric_coherence` | `columns.{solde_debut, mvts_debiteurs, mvts_crediteurs, solde_fin, date_fin, dt_cr, ref_banque, nom_correspondant, devise, num_compte}`, `tolerance.absolute`, `grouping_key_temporal_continuity` |

Une clé manquante, un type ou un nom de champ inconnu arrête l'exécution au démarrage.

### 2.3 Réglages propres à chaque endpoint

| Endpoint | Table source | Champs | Réglages ajoutés |
|---|---|---|---|
| [E07_FS](../e07_fs/README.md) | `E7EtatBcmFluxSortants` | 7 catégoriels, 1 bloc de contrôle des transactions | `flux: FS` pour `TypeSwift` ; `columns.nif_nni` et bloc `matching` pour `NomDonneurOrdre` ; `llm.web_search_max_uses` pour `Beneficiaire` ; `no_activity.template_na_columns` et `no_activity.pays_value` ; `date_inclusive` ; `load.initial_since` |
| [E08_OCD](../e08_ocd/README.md) | `E8EtatBcmOuvertureCreditDocumentaires` | 6 catégoriels | `columns.nif_nni` et bloc `matching` pour `NomDonneurOrdre` ; `llm.web_search_max_uses` pour `Beneficiaire` ; référentiel `Produits` converti depuis le fichier métier |
| [E09_PE](../e09_pe/README.md) | `E9EtatBcmPrevisionEcheances` | 1 catégoriel, 1 bloc de contrôle des échéances | `load.initial_since` |
| [E10_FE](../e10_fe/README.md) | `E10EtatBcmFluxEntrants` | 7 catégoriels, 1 bloc de contrôle des transactions | `flux: FE` ; `llm.web_search_max_uses` pour `NomDonneurOrdre` ; `columns.nif_nni` et bloc `matching` pour `Beneficiaire` ; gabarit sans activité sans colonne de devise source, absente de la table ; `load.initial_since` |
| [E11_RDCC](../e11_rdcc/README.md) | `E11EtatBcmReleveDesComptesCorrespondants` | 2 catégoriels, 1 bloc de cohérence numérique | `tolerance.absolute` ; `grouping_key_temporal_continuity` ; témoin de non-activité global, colonne synthétique précalculée |

Le bloc `matching` des champs à rapprochement fiscal porte quatre seuils : score fort,
écart minimal avec le second candidat, score plancher déclenchant un arbitrage, taille des lots.
`matching.cache_resolutions` mémorise les résolutions déterministes.

E08 et E11, de faible volume, chargent tout l'historique : ils n'ont pas de `load.initial_since`.

## 3. Chaîne par champ

### 3.1 Socle `shared/config_base.yaml`

| Clé | Défaut | Rôle |
|---|---|---|
| `pipeline_version` | `1.0.0` | version du traitement |
| `input.type` | `sqlserver` | source : la base, ou un fichier local |
| `input.sep`, `input.encoding`, `input.sheet` | `;`, `utf-8-sig`, `0` | lecture d'un fichier local |
| `output.format` | `csv` | format du fichier d'extraction |
| `llm.model` | `qwen2.5:14b` | modèle **local**, servi par Ollama |
| `llm.batch_size`, `llm.num_ctx` | `25`, `3500` | valeurs par appel, et taille de contexte du modèle local |
| `llm.max_retry`, `llm.retry_wait` | `3`, `2` | nouvelles tentatives sur un appel en échec |

Le socle de cette chaîne ne porte ni réglage de chargement, ni plafond de rapport, ni
notification : ces mécanismes n'existent pas dans la chaîne par champ.

### 3.2 Structure d'un YAML de couple champ/API

Un fichier par couple, dans `{champ}/config/{API}.yaml` :

```yaml
api_id:     "E07_FS"
input:
  type:       "sqlserver"
  table_name: "E7EtatBcmFluxSortants"
columns:
  field:           "Devise"                 # colonne source
  field_out:       "Devise_Normalisée"      # colonne produite
  ref_transaction: "ReferenceTransaction"   # témoin de la règle NA
  ref_banque:      "RefBanque"              # ventilation des outliers
outlier_tag:      "OUTLIER"
referentiel_path: "devise/referentiel/devise_referentiel.json"
output:
  dir: "devise/outputs/E07_FS/"
```

Les quatre clés de `columns` et `referentiel_path` sont la structure commune à tous les couples.
Les champs à rapprochement fiscal ajoutent la colonne d'identifiant fiscal et leurs seuils.

La mémoire des valeurs validées n'est pas déclarée en configuration : chaque champ la résout
depuis son dossier `referentiel/`. L'option `--warm-start` commande son usage.

### 3.3 Les 22 couples déclarés

| Champ | YAML présents |
|---|---|
| `devise` | E07_FS, E08_OCD, E09_PE, E10_FE, E11_RDCC |
| `pays` | E07_FS, E08_OCD, E10_FE |
| `nomdonneurordre` | E07_FS, E08_OCD, E10_FE |
| `beneficiaire` | E07_FS, E08_OCD, E10_FE |
| `nomcorrespondant` | E08_OCD, E11_RDCC |
| `nature_economique` | E07_FS, E10_FE |
| `typeswift` | E07_FS, E10_FE |
| `mode_reglement` | E07_FS, E10_FE |

Ajouter un couple consiste à déposer un YAML dans `{champ}/config/` : il est pris en compte par
`--all` sans modification de code.

### 3.4 Réglages propres à certains champs

| Champ | Clés ajoutées au YAML du couple |
|---|---|
| `nomdonneurordre`, `beneficiaire` | `api_family` : `DGI` pour l'entité locale, `CLAUDE_WEB` pour l'entité étrangère — sélectionne la cascade. `columns.nif_nni` : colonne d'identifiant fiscal. Bloc `matching` : `strong_threshold` 92, `strong_gap` 8, `arbitrage_min` 70, `batch_size` 300. Bloc `llm` : modèle distant et taille des lots d'arbitrage |
| `nature_economique` | `flux_type` : `FS` ou `FE`, sélectionne le référentiel. `model_embed` : **chemin absolu** du modèle de phrases installé sur le poste. `seuil_embed` 0,84 et `seuil_embed_court` 0,95 : seuils de l'étape sémantique. `tokens_par_ligne` 35 |
| `pays` | `use_llm` : active ou non l'appel au modèle local en dernière étape |
| `typeswift` | `flux_type` : `FS` ou `FE`, sélectionne la liste de codes de messages valides |

Le champ `nature_economique` ne déclare pas de `referentiel_path` : son référentiel est choisi par
`flux_type`, parmi les fichiers de son dossier `referentiel/`.

### 3.5 Seuils des étapes de résolution

Tous les seuils ne sont pas des réglages : certains sont des constantes de module, et leur
modification demande une intervention dans le code.

| Étape | Seuil | Où |
|---|---|---|
| 3. Rapprochement approché, champs de noms | `strong_threshold`, `strong_gap`, `arbitrage_min`, `batch_size` | YAML, dans les deux chaînes |
| 3. Rapprochement approché, `Pays` | score minimum 93 | constante du module, dans les deux chaînes |
| 4. Similarité sémantique | `seuil_embed`, `seuil_embed_court`, `model_embed` | YAML, chaîne par champ uniquement |
| 5. Client de modèle | `llm.batch_size`, `llm.concurrency`, `llm.max_values_per_run`, `llm.web_search_max_uses` | YAML. `concurrency` et `max_values_per_run` n'existent que dans la chaîne par API |

<a id="performance-dun-chargement-réglages-communs"></a>

## 4. Performance d'un chargement (réglages communs)

| Réglage | Effet |
|---|---|
| `load.cast_max_text` | Les colonnes texte de la base sont déclarées sans longueur maximale ; le pilote ODBC les lit alors cellule par cellule. Converties à la lecture vers une longueur bornée, 20 000 lignes passent de 11,6 s à 2,7 s. La valeur réelle la plus longue relevée sur les cinq tables mesure 149 caractères : aucune troncature |
| `load.chunk_size` | Donne une progression dans la journalisation pendant un chargement long, au lieu d'un silence |
| `load.retries`, `load.retry_wait_seconds` | Nouvelle tentative sur coupure réseau passagère, lecture reprise depuis le début. Une erreur SQL — colonne inconnue, droits insuffisants — échoue immédiatement |
| `load.initial_since` | Borne la profondeur d'historique du premier chargement |
| `llm.concurrency` | Le temps d'un appel est presque entièrement de l'attente réseau, surtout avec recherche web. Les enchaîner un par un allongeait fortement un premier chargement |
| `llm.max_values_per_run` | Borne la durée et le coût d'un premier chargement. Au-delà du plafond, les valeurs restent `OUTLIER`, ne sont pas mémorisées, et sont reproposées à l'exécution suivante |
| `matching.cache_resolutions` | Mémorise les résolutions déterministes du rapprochement fiscal, avec une empreinte de la base fiscale : l'exécution suivante ne le refait pas. Base modifiée, empreinte différente, tout est recalculé |

Ces réglages n'existent que dans la chaîne par API. La chaîne par champ lit la table en un seul
bloc, sans reprise sur coupure, et borne le volume par un filtre de date appliqué seulement aux
champs `Pays` et `NatureEconomique`.

Le coût d'un premier chargement, endpoint par endpoint, figure dans la section *Points de coût* du
README de chaque package.

## 5. Installation

Voir [README.md](../README.md#installation).
