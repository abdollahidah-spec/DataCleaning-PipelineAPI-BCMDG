# Configuration

Deux niveaux : les secrets et les chemins propres au poste, dans `.env` ; les réglages du
traitement, dans les YAML. Les réglages propres à un endpoint sont décrits dans le README de son
package.

## `.env`

Copier `.env.example` vers `.env` et renseigner. Ce fichier n'est jamais versionné.

| Variable | Utilisation |
|---|---|
| `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_DRIVER` | Connexion à la base de collecte, en **lecture seule stricte** sur toutes les tables sources : aucune écriture, aucun DDL |
| `ANTHROPIC_API_KEY` | Résolution assistée des champs qui la prévoient. Facturée à l'appel, et à l'usage pour les champs qui recourent à la recherche web |
| `DGI_BASE_PATH` | Base fiscale, utilisée par le rapprochement des raisons sociales. Absente : erreur explicite au chargement |
| `PUBLIC_ENT_PATH` | Liste des entreprises publiques. Absente : liste vide, aucune entreprise publique reconnue, pas d'erreur |
| `OUTPUT_BASE` | Racine des livrables. Vide : `{api}/outputs/`. Renseignée : `{OUTPUT_BASE}/{API_ID}/`, nom de l'endpoint en majuscules — un dossier par endpoint, jamais de collision en lancement parallèle |
| `STATE_DIR` | État incrémental et mémoires dérivées, un fichier JSON par endpoint. Vide : `state/` à la racine du dépôt |
| `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, `SHAREPOINT_SITE_URL`, `SHAREPOINT_FOLDER_PATH` | Dépôt distant, optionnel : absent, le dépôt est ignoré sans erreur |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_APP_PASSWORD`, `EMAIL_TO` | Notification de fin d'exécution, optionnelle : absente, la notification est ignorée sans erreur |
| `EMAIL_MAX_ATTACHMENT_MB` | Taille maximale d'une pièce jointe avant omission (défaut 15) |
| `LOG_LEVEL` | Niveau de journalisation (défaut `INFO`) |

## `shared/config_base.yaml` — socle commun

Tous les endpoints héritent de ce fichier ; un `config/{API}.yaml` ne redéfinit que ce qui change
(fusion profonde, `shared/config.py::load_config`).

| Clé | Défaut | Rôle |
|---|---|---|
| `pipeline_version` | `1.0.0` | version reportée dans les rapports et la notification |
| `input.type` | `sqlserver` | source : la base, ou un fichier local (`csv`, `excel`) |
| `input.sep`, `input.encoding`, `input.sheet` | `;`, `utf-8-sig`, `0` | lecture d'un fichier local. L'encodage et le séparateur déclarés sont essayés d'abord, puis des replis (`cp1252`, `latin-1` ; virgule, tabulation) — un CSV exporté depuis Excel en français reste lisible |
| `load.chunk_size` | `50000` | lecture par paquets, pour une progression visible pendant un chargement long. `0` ou vide : lecture en un seul bloc |
| `load.initial_since` | vide | borne le chargement initial à partir de cette date. Renseignée au 01/01/2024 sur E07 et E10 |
| `load.cast_max_text` | `true` | conversion des colonnes texte volumineuses à la lecture, voir ci-dessous |
| `load.retries`, `load.retry_wait_seconds` | `2`, `30` | reprise sur coupure réseau passagère |
| `output.format` | `csv` | format de l'extraction ad hoc |
| `llm.model` | `claude-sonnet-5` | modèle appelé pour la résolution assistée |
| `llm.batch_size` | `20` | valeurs par appel ; porté à `35` sur les champs à recherche web |
| `llm.max_retry`, `llm.retry_wait` | `3`, `2` | nouvelles tentatives sur un appel en échec, et attente entre deux |
| `llm.concurrency` | `4` | lots envoyés en parallèle |
| `llm.max_values_per_run` | vide | plafond de valeurs nouvelles par exécution et par champ |
| `reports.top_n_outliers_detail` | `100` | valeurs détaillées par champ dans le rapport PDF. `0` : aucune limite |
| `reports.max_anomaly_rows_excel` | `200000` | lignes d'anomalies écrites dans un onglet Excel ; au-delà, l'onglet garde cet extrait et le détail intégral part dans un CSV à côté du classeur. `0` : aucune limite |
| `email.enabled`, `email.attach_workbook_max_mb` | `true`, `15` | notification, et taille maximale du classeur joint |

## Surcharges par endpoint

| Endpoint | Table source | Ce que le YAML ajoute |
|---|---|---|
| [E07_FS](../e07_fs/README.md) | `E7EtatBcmFluxSortants` | 7 champs catégoriels et un bloc de contrôle des transactions ; rapprochement fiscal sur `NomDonneurOrdre` ; gabarit des messages sans activité ; borne d'historique |
| [E08_OCD](../e08_ocd/README.md) | `E8EtatBcmOuvertureCreditDocumentaires` | 6 champs catégoriels ; rapprochement fiscal sur `NomDonneurOrdre` ; recherche web sur `Beneficiaire` ; référentiel `Produits` construit depuis le fichier métier |
| [E09_PE](../e09_pe/README.md) | `E9EtatBcmPrevisionEcheances` | 1 champ catégoriel et un bloc de contrôle des échéances |
| [E10_FE](../e10_fe/README.md) | `E10EtatBcmFluxEntrants` | même structure qu'E07, rôles inversés : recherche web sur `NomDonneurOrdre`, rapprochement fiscal sur `Beneficiaire` |
| [E11_RDCC](../e11_rdcc/README.md) | `E11EtatBcmReleveDesComptesCorrespondants` | 2 champs catégoriels et un bloc de cohérence numérique ; témoin de non-activité global |

Tout champ `categorical` exige `columns.field`, `columns.field_out`, `columns.ref_transaction`,
`columns.ref_banque` et `referentiel_path` ; les blocs de contrôle exigent leurs propres colonnes.
Une clé manquante ou un nom de champ inconnu arrête l'exécution au démarrage.

## Performance d'un chargement (réglages communs)

| Réglage | Effet |
|---|---|
| `load.cast_max_text` | Les colonnes texte de la base sont déclarées sans longueur maximale ; le pilote ODBC les lit alors cellule par cellule. Converties à la lecture vers une longueur bornée, 20 000 lignes passent de 11,6 s à 2,7 s. La valeur réelle la plus longue relevée sur les cinq tables mesure 149 caractères : aucune troncature |
| `load.chunk_size` | Donne une progression dans la journalisation pendant un chargement long, au lieu d'un silence |
| `load.retries`, `load.retry_wait_seconds` | Nouvelle tentative sur coupure réseau passagère, lecture reprise depuis le début. Une erreur SQL — colonne inconnue, droits insuffisants — échoue immédiatement |
| `llm.concurrency` | Le temps d'un appel est presque entièrement de l'attente réseau, surtout avec recherche web. Les enchaîner un par un allongeait fortement un premier chargement |
| `llm.max_values_per_run` | Borne la durée et le coût d'un premier chargement. Au-delà du plafond, les valeurs restent `OUTLIER`, ne sont pas mémorisées, et sont reproposées à l'exécution suivante |
| `matching.cache_resolutions` | Mémorise les résolutions déterministes du rapprochement fiscal, avec une empreinte de la base fiscale : l'exécution suivante ne le refait pas. Base modifiée, empreinte différente, tout est recalculé |

Le coût d'un premier chargement, endpoint par endpoint, figure dans la section *Points de coût*
du README de chaque package.

## Installation

Voir [README.md](../README.md#installation).
