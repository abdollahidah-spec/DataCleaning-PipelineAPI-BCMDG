# Nettoyage et normalisation des données BCM — architecture et prérequis d'industrialisation

Document de cadrage technique. Objet : donner à une équipe d'exploitation et d'intégration une
vue complète et actionnable de la chaîne de nettoyage des données réglementaires BCM, et la liste
des prérequis pour la porter sur une infrastructure de production.

Périmètre : 5 endpoints du datawarehouse (E07, E08, E09, E10, E11), 8 familles de champs,
~5 millions de lignes sur l'endpoint le plus volumineux.

---

## 1. Les deux volets

La chaîne a été construite en deux temps, et les deux volets coexistent avec des rôles distincts.

| | **Volet par champ** (`DataCleaning-PipelineField-BCMDG`) | **Volet par API** (`DataCleaning-PipelineAPI-BCMDG`) |
|---|---|---|
| Unité de traitement | un champ, sur toutes les APIs qui le contiennent | une API, tous ses champs en un run |
| Exécutions pour couvrir le périmètre | 8 champs × 22 couples champ/API | 5 runs |
| Livrables | un classeur par champ et par API | un classeur et un rapport par API |
| Rôle actuel | **socle métier** : c'est là que les référentiels, les règles de cascade et les caches de classification validés ont été constitués et éprouvés | **cible de production** : orchestration, planification, livrables, boucle de correction, notifications |
| Statut | figé, conservé comme source de vérité métier et pour les analyses transverses | actif, en recette |

Le basculement vers le volet par API répond à trois limites du volet par champ : un même jeu de
données relu autant de fois qu'il y a de champs, aucune vision consolidée de la qualité d'un
endpoint, et une multiplication des livrables partiels à recoller manuellement.

L'industrialisation porte sur le **volet par API**. Le volet par champ reste la référence pour
l'origine des référentiels et des caches, qui y sont maintenus puis repris côté API.

---

## 2. Architecture logique

Deux couches, un contrat unique entre elles.

```
shared/                        Infrastructure générique, sans logique métier de champ
  base_api_pipeline.py         Orchestrateur : chargement, traitement, qualité, livrables, état
  field_processor.py           Contrat FieldProcessor -> FieldResult
  db_connector.py              Accès SQL Server en lecture seule, projection, paquets, reprise
  query_columns.py             Projection des colonnes et confrontation au schéma réel
  na_rule.py                   Règle NA/OUTLIER commune, vectorisée
  claude_client.py             Appels LLM (listes fermées, arbitrage, recherche web)
  claude_batches.py            Lots d'appels LLM parallèles, plafond par run
  dgi_cache.py                 Mémoire des rapprochements fiscaux déterministes
  referentiel_cibles.py        Résolution d'une valeur déjà conforme au référentiel
  writer.py, pdf_report.py, report_templates.py, email_notifier.py, state_store.py, ...

e07_fs/  e08_ocd/  e09_pe/  e10_fe/  e11_rdcc/        Un package autonome par endpoint
  config/{API}.yaml            Table, champs, colonnes, seuils, livrables, planification
  fields/*.py                  Logique métier de chaque champ de cet endpoint
  referentiel/*.json           Référentiels et caches de classification validés
  pipeline.py, run_pipeline.py, apply_corrections.py, reports.py, ad_hoc_extraction.py
```

**Principe directeur** : un package d'endpoint ne réutilise que `shared/`. Aucune dépendance
entre packages d'endpoints. Ajouter un endpoint ne modifie aucun endpoint existant ; une
évolution métier sur un endpoint ne peut pas en casser un autre.

**Contrepartie assumée** : les champs communs à plusieurs endpoints sont des copies. Un correctif
appliqué à une copie et oublié ailleurs passerait inaperçu — un jeu de tests dédié
(`tests/test_coherence_packages.py`) verrouille les garanties transverses : présence des étapes
obligatoires, parallélisation des appels LLM, identité du code des copies, non-redéfinition de la
règle NA.

**Contrat entre couches** : chaque champ expose un `FieldProcessor.process(df, api_id)` et
retourne un `FieldResult` uniforme (données annotées, table de classification, anomalies,
colonnes techniques à exclure, statistiques, noms d'onglets). L'orchestrateur ne connaît ni les
champs ni les endpoints : il manipule des `FieldResult`. Trois familles de champs existent, et un
endpoint en combine librement :

| Famille | Résultat | Exemples |
|---|---|---|
| Catégoriel | une valeur normalisée + une méthode de résolution, par ligne | Devise, Pays, NomCorrespondant, NomDonneurOrdre, Beneficiaire, NatureEconomique, TypeSwift, ModeReglement, Produits |
| Validation ligne à ligne | un rapport d'anomalies, pas de valeur normalisée | Échéances (E09), Transactions (E07, E10) |
| Cohérence inter-lignes | un rapport d'anomalies, avec chaînage temporel | Soldes RDCC (E11) |

---

## 3. Chaîne d'exécution d'un run

1. **Résolution du mode** : `initial` (historique, borné par `load.initial_since`) ou
   `incremental` (lignes dont `dtCr` dépasse le dernier horodatage traité, lu dans l'état).
2. **Confrontation au schéma réel** de la table : une colonne optionnelle absente est retirée de
   la requête, une colonne principale absente arrête le run immédiatement avec un message
   exploitable.
3. **Chargement** : projection explicite des colonnes utiles, lecture par paquets avec
   progression, colonnes texte `NVARCHAR(MAX)` lues converties, reprise automatique sur coupure
   réseau passagère.
4. **Prétraitement** éventuel propre à l'endpoint (E11 : calcul de la colonne témoin globale
   « sans activité »).
5. **Traitement des champs catégoriels en parallèle** (un fil par champ, chacun sur la seule
   projection de colonnes dont il a besoin), puis des champs de validation.
6. **Cascade par champ**, dans cet ordre invariant : cache des corrections validées → valeur déjà
   conforme au référentiel → référentiel → règles déterministes → rapprochement ou LLM → `OUTLIER`.
   La règle NA/OUTLIER est appliquée en fin de cascade, de façon vectorisée.
7. **Qualité** : taux de conformité et de normalisation du run, compteurs cumulés sur l'historique,
   répartition des outliers par champ et par banque.
8. **Livrables** : classeur de classification (chemin stable, écrasé à chaque run, lisible par
   Power BI), rapport PDF, email.
9. **État** : horodatage traité, statut, compteurs cumulés, pour le run suivant.

Un échec à n'importe quelle étape laisse l'état inchangé, notifie par email et retourne un code de
sortie non nul. Une exécution sur fichier local (`--input`) est totalement hors ligne : ni email,
ni dépôt distant, ni état.

---

## 4. Données, persistance et effets de bord

| Élément | Nature | Emplacement | Versionné |
|---|---|---|---|
| Tables source | lecture seule stricte, aucune écriture ni DDL | SQL Server `DATAWAREHOUSE_SA_PROD` | — |
| Référentiels métier | validés par le métier | `{api}/referentiel/*.json` | oui |
| Caches de classification | résolutions validées et corrections manuelles ; priment sur toute la cascade | `{api}/referentiel/validated_classif_*.json` | oui |
| Historique des corrections | traçabilité des validations métier | `{api}/referentiel/corrections_history_*.json` | oui |
| État incrémental | dernier horodatage traité, statut, compteurs | `{STATE_DIR}/{API}_run_state.json` | non |
| Mémoire des rapprochements fiscaux | données dérivées, reproductibles, invalidées si la base fiscale change | `{STATE_DIR}/dgi_resolution_*.json` | non |
| Livrables | classeur, PDF | `{OUTPUT_BASE}/{API}/` | non |
| Fichiers de référence externes | base fiscale DGI, entreprises publiques | `req/`, chemins en configuration | non |
| Secrets | base, LLM, SMTP | `.env` | non |

**Boucle de correction** : les valeurs non résolues apparaissent dans les onglets de
classification ; le métier fournit un classeur comportant un onglet `Instructions`
(`Champ | Input | Label_Attendu`) ; l'application route chaque ligne vers le cache du champ
concerné, en enregistrant plusieurs formes de la clé pour que la correction soit retrouvée quelle
que soit la convention de nettoyage du champ. La correction prime dès le run suivant et reste
tracée dans l'historique.

---

## 5. Dépendances externes

| Dépendance | Usage | Mode de défaillance |
|---|---|---|
| SQL Server (lecture seule) | source unique des données | arrêt du run, email d'échec, état inchangé |
| Base fiscale DGI, entreprises publiques (fichiers) | rapprochement des entités locales | chemin absent : erreur explicite au démarrage pour la DGI, dégradation propre pour les entreprises publiques |
| API LLM | résolution des valeurs nouvelles sur listes fermées, arbitrage, recherche web | échec technique : valeur laissée en `OUTLIER`, **jamais mise en cache**, réessayée au run suivant |
| SMTP | notification de fin de run | échec d'envoi : run considéré réussi, échec journalisé |
| Dépôt de fichiers (SharePoint, optionnel) | diffusion des livrables | non bloquant ; le stockage local ou réseau reste le mécanisme principal |

Aucune dépendance externe n'est sur le chemin critique de l'intégrité : un échec LLM, SMTP ou de
diffusion ne corrompt ni les caches ni l'état.

---

## 6. Qualité logicielle

- **446 tests** automatisés, sans aucun appel réseau : base, LLM, SMTP et dépôt distant sont
  simulés ; les référentiels et l'état sont redirigés vers des dossiers temporaires.
- Couverture : cascades de chaque champ, règles de validation, règle NA (dont équivalence stricte
  avec l'implémentation ligne à ligne d'origine), delta vide, rapports, boucle de correction,
  projection de colonnes, chargement de fichiers, parallélisme des appels LLM, mémoire des
  rapprochements, cohérence entre packages.
- Invariants vérifiés par les tests, car déjà sources d'incidents : un échec LLM technique n'est
  jamais mis en cache ; un delta vide produit quand même des livrables ; une exécution sur fichier
  n'écrit jamais dans le dossier de production ; une valeur `NULL` en base est traitée comme
  `OUTLIER` et non ignorée.

---

## 7. Performances mesurées

Mesures relevées sur l'environnement de recette (poste Windows, pilote ODBC historique
« SQL Server », VPN) en septembre 2026.

| Poste | Mesure | Levier |
|---|---|---|
| Lecture de 2,98 M lignes (E10) | ~5 à 6 min | projection, lecture par paquets, conversion des colonnes `NVARCHAR(MAX)` (gain ×4 à ×9) |
| Run complet E10, livrables compris | 11 min 36 s | parallélisme des champs |
| Rapprochement fiscal, 8 000 libellés nouveaux | ~25 min, puis évité | mémoire des résolutions avec empreinte de la base fiscale |
| Recherche web, 1 771 valeurs nouvelles (E07) | ~45 à 90 min | 4 lots en parallèle, plafond par run, mise en cache définitive |
| Runs incrémentaux | quelques minutes | caches chauds, peu de valeurs nouvelles |

Seul le premier chargement est coûteux, et il est bornable (`load.initial_since`,
`llm.max_values_per_run`). Le régime permanent est un traitement de delta.

---

## 8. Exploitation actuelle et limites

**Exploitation actuelle** : un poste Windows, une tâche planifiée déclenchant un script qui
enchaîne les 5 endpoints en mode incrémental ; chacun gère son succès, son échec et son état,
l'échec de l'un n'empêchant pas les suivants. Journaux par run et par endpoint dans `logs/`.

| Limite | Impact | Traitement attendu à l'industrialisation |
|---|---|---|
| Exécution sur un poste utilisateur, session interactive | indisponibilité, dépendance au VPN et à une session ouverte | serveur dédié ou conteneur, exécution sous compte de service |
| Pilote ODBC historique | lecture nettement plus lente que nécessaire | installer un pilote ODBC récent (gain mesuré ×4 sur la lecture seule) |
| Secrets dans un fichier `.env` local | pas de rotation, pas de traçabilité | coffre de secrets, injection à l'exécution |
| Fichiers de référence externes posés à la main | dérive silencieuse entre postes | dépôt versionné ou partage contrôlé, avec empreinte vérifiée |
| Ordonnancement linéaire par script | pas de reprise fine, pas de dépendances, pas d'historique d'exécution | ordonnanceur (Airflow ou équivalent) : une tâche par endpoint, reprise sur échec, alerting |
| Livrables Excel | plafond de lignes, lecture lente des gros onglets | déjà contourné par bascule CSV au-delà d'un seuil ; cible : table ou fichier colonnaire pour la BI |
| Absence d'intégration continue | la suite de tests dépend d'une exécution manuelle | pipeline CI sur chaque push, exécution des 446 tests |
| Coût LLM non plafonné par défaut | dérive possible sur un premier chargement | plafond par run activé par configuration, suivi de consommation |
| Observabilité limitée aux journaux et emails | diagnostic a posteriori | métriques exportées (durée, volumes, taux de conformité, appels LLM) et tableau de bord |

---

## 9. Prérequis d'industrialisation

**Infrastructure**
- Serveur ou conteneur dédié, Python et pilote ODBC récent, accès réseau permanent à la base.
- Compte de service avec droits **strictement en lecture** sur le datawarehouse.
- Stockage des livrables partagé et sauvegardé ; `{OUTPUT_BASE}` et `{STATE_DIR}` sur des volumes
  persistants, distincts du code.

**Ordonnancement**
- Une tâche par endpoint, exécutable indépendamment, idempotente : le mode incrémental repart du
  dernier horodatage traité, un échec ne consomme pas de delta.
- Un chargement initial par endpoint à prévoir **avant** la mise sous ordonnanceur.
- Paramètres d'exécution exposés : mode, borne d'historique, plafond LLM, niveau de journalisation.

**Sécurité et conformité**
- Secrets hors du dépôt, rotation documentée.
- Lecture seule garantie côté base, journalisation des accès.
- Données de production jamais nécessaires pour les tests : la suite tourne entièrement simulée.

**Observabilité**
- Journaux centralisés, un identifiant de run propagé.
- Métriques par run : durée par étape, lignes traitées, taux de conformité, outliers par champ,
  nombre d'appels LLM, nombre de valeurs nouvelles mises en cache.
- Alerte sur échec, sur run vide inattendu et sur dérive du taux de conformité.

**Cycle de vie du code**
- Intégration continue : tests à chaque push, contrôle de cohérence entre packages inclus.
- Gestion des référentiels et des caches comme du code : revue, versionnement, traçabilité des
  corrections métier.

---

## 10. Trajectoire proposée

| Étape | Contenu | Résultat attendu |
|---|---|---|
| 1. Stabilisation | pilote ODBC récent, compte de service, volumes persistants, chargement initial des 5 endpoints, CI | exécution reproductible hors poste utilisateur |
| 2. Ordonnancement | migration du script vers un ordonnanceur, une tâche par endpoint, alerting, métriques | exploitation outillée, reprise sur échec |
| 3. Diffusion | livrables BI en table ou format colonnaire, historisation des indicateurs de qualité | suivi de la qualité dans le temps, sans dépendance au format Excel |

---

## 11. Référence rapide des endpoints

| Endpoint | Table | Champs normalisés | Contrôles | Particularité |
|---|---|---|---|---|
| E11 — RDCC | `E11EtatBcmReleveDesComptesCorrespondants` | NomCorrespondant, Devise | 4 règles de cohérence des soldes, dont chaînage J / J+1 | témoin « sans activité » global, calculé une fois par run |
| E09 — PE | `E9EtatBcmPrevisionEcheances` | Devise | montant strictement positif, échéance postérieure à la création | table la plus volumineuse, historique borné à 2024 |
| E08 — OCD | `E8EtatBcmOuvertureCreditDocumentaires` | Devise, NomCorrespondant, Produits, NomDonneurOrdre, Beneficiaire, Pays | — | réunit toutes les familles de résolution |
| E07 — FS | `E7EtatBcmFluxSortants` | TypeSwift, ModeReglement, Devise, NomDonneurOrdre, Beneficiaire, NatureEconomique, Pays | montant et taux positifs ou nuls, date antérieure à la création, gabarit « sans activité » | donneur d'ordre local, bénéficiaire étranger |
| E10 — FE | `E10EtatBcmFluxEntrants` | idem E07 | idem E07 | entités inversées : donneur d'ordre étranger, bénéficiaire local ; pas de colonne `SourceDevise` |

Le détail de chaque endpoint — cascades, règles, réglages, livrables, coûts — figure dans le
README de son package : [e07_fs](../e07_fs/README.md), [e08_ocd](../e08_ocd/README.md),
[e09_pe](../e09_pe/README.md), [e10_fe](../e10_fe/README.md), [e11_rdcc](../e11_rdcc/README.md).
Les choix de conception sont détaillés dans [ARCHITECTURE.md](ARCHITECTURE.md), les règles métier
dans [FUNCTIONAL.md](FUNCTIONAL.md), la configuration dans [CONFIGURATION.md](CONFIGURATION.md).
