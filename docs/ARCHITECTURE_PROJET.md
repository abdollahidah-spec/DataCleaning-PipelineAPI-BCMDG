# Nettoyage et normalisation des données réglementaires BCM — architecture

Description de l'existant : nature du projet, architecture, fonctionnement et contenu de la
chaîne de traitement. Document de référence, sans recommandation ni prescription.

---

## 1. Objet et périmètre

La chaîne nettoie, normalise et contrôle les données des états réglementaires remontés par les
banques au datawarehouse de la BCM. Chaque valeur brute saisie par un opérateur bancaire est
rapprochée d'une valeur de référence normalisée, ou signalée comme non rattachable (`OUTLIER`)
pour validation métier. Les champs numériques et de date ne sont pas normalisés : ils sont soumis
à des règles de contrôle qui produisent un rapport d'anomalies.

| Endpoint | Table source | Champs normalisés | Contrôles numériques / date |
|---|---|---|---|
| E11 — RDCC | `E11EtatBcmReleveDesComptesCorrespondants` | NomCorrespondant, Devise | 4 règles de cohérence des soldes, dont chaînage J / J+1 |
| E09 — PE | `E9EtatBcmPrevisionEcheances` | Devise | montant strictement positif, échéance postérieure à la création |
| E08 — OCD | `E8EtatBcmOuvertureCreditDocumentaires` | Devise, NomCorrespondant, Produits, NomDonneurOrdre, Beneficiaire, Pays | — |
| E07 — FS | `E7EtatBcmFluxSortants` | TypeSwift, ModeReglement, Devise, NomDonneurOrdre, Beneficiaire, NatureEconomique, Pays | montant et taux positifs ou nuls, date antérieure à la création, gabarit « sans activité » |
| E10 — FE | `E10EtatBcmFluxEntrants` | idem E07 | idem E07 |

Volumétrie relevée en base : E09 ≈ 5,1 M lignes, E10 ≈ 3,0 M, E07 ≈ 1,0 M, E11 ≈ 350 k,
E08 ≈ 13 k. Les tables sont lues **en lecture seule stricte** : aucune écriture, aucun DDL.

Le sens du flux détermine le traitement d'un même nom de champ : sur les flux sortants (E07), le
donneur d'ordre est une entité mauritanienne et le bénéficiaire une entité étrangère ; sur les
flux entrants (E10), les rôles sont inversés. Les deux endpoints portent donc les mêmes champs
avec des moteurs de résolution échangés.

---

## 2. Nature du projet : deux volets

| | **Volet par champ** (`DataCleaning-PipelineField-BCMDG`) | **Volet par API** (`DataCleaning-PipelineAPI-BCMDG`) |
|---|---|---|
| Unité de traitement | un champ, sur toutes les APIs qui le contiennent | une API, tous ses champs en un run |
| Exécutions pour couvrir le périmètre | 8 champs × 22 couples champ/API | 5 runs |
| Livrables | un classeur par champ et par API | un classeur et un rapport par API |
| Rôle | socle métier : référentiels, règles de cascade et caches de classification y ont été constitués et éprouvés | chaîne de production : orchestration, planification, livrables consolidés, boucle de correction, notifications |
| Statut | figé ; reste la source de vérité métier et l'outil des analyses transverses par champ | actif, en recette |

Le volet par API répond à trois limites du volet par champ : un même jeu de données relu autant de
fois qu'il y a de champs, aucune vision consolidée de la qualité d'un endpoint, et des livrables
partiels à recoller manuellement. Les référentiels et les caches validés du volet par champ ont
été reportés tels quels dans les packages d'endpoint du volet par API.

---

## 3. Architecture logique

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

**Principe directeur** : un package d'endpoint ne réutilise que `shared/`, et rien d'un autre
package. Ajouter un endpoint ne modifie aucun endpoint existant ; une évolution métier sur un
endpoint ne peut pas en casser un autre.

**Contrepartie assumée** : les champs communs à plusieurs endpoints sont des copies, et non un
module partagé. Un correctif appliqué à une copie et oublié ailleurs ne se verrait pas — un jeu de
tests dédié verrouille donc les garanties transverses : présence des étapes obligatoires de la
cascade, parallélisation des appels LLM, identité du code entre copies, non-redéfinition de la
règle NA hors de `shared/`.

**Contrat entre couches** : chaque champ expose `FieldProcessor.process(df, api_id)` et retourne
un `FieldResult` uniforme — données annotées, table de classification, anomalies, colonnes
techniques à exclure, statistiques, noms d'onglets. L'orchestrateur ne connaît ni les champs ni
les endpoints : il manipule des `FieldResult`. Trois familles de champs existent, et un endpoint
en combine librement :

| Famille | Résultat | Champs concernés |
|---|---|---|
| Catégoriel | une valeur normalisée et une méthode de résolution, par ligne | Devise, Pays, NomCorrespondant, NomDonneurOrdre, Beneficiaire, NatureEconomique, TypeSwift, ModeReglement, Produits |
| Validation ligne à ligne | un rapport d'anomalies, pas de valeur normalisée | Échéances (E09), Transactions (E07, E10) |
| Cohérence inter-lignes | un rapport d'anomalies, avec chaînage temporel | Soldes RDCC (E11) |

---

## 4. Fonctionnement d'un run

1. **Résolution du mode** : `initial` (historique, éventuellement borné par une date de début) ou
   `incremental` (lignes dont l'horodatage de création dépasse le dernier horodatage traité, lu
   dans le fichier d'état de l'endpoint).
2. **Confrontation au schéma réel** de la table : une colonne optionnelle absente est retirée de
   la requête ; une colonne principale absente arrête le run immédiatement, avec un message
   nommant le champ et la colonne.
3. **Chargement** : projection explicite des seules colonnes utiles, lecture par paquets avec
   progression dans les journaux, colonnes texte `NVARCHAR(MAX)` lues converties en
   `NVARCHAR(4000)`, nouvelle tentative automatique sur coupure réseau passagère.
4. **Prétraitement** propre à l'endpoint quand il existe : E11 calcule une colonne témoin
   « sans activité » commune à ses deux champs catégoriels, une seule fois par run.
5. **Traitement des champs catégoriels en parallèle**, un fil par champ, chacun ne recevant que la
   projection de colonnes dont il a besoin ; puis traitement des champs de validation.
6. **Cascade de résolution**, dans cet ordre invariant pour tous les champs catégoriels :

   ```
   cache des corrections validées         (une correction métier prime sur tout le reste)
   -> valeur déjà conforme au référentiel
   -> référentiel métier
   -> règles déterministes du champ       (alias, bruit connu, codes, mots-clés)
   -> rapprochement ou LLM                (base fiscale DGI, liste fermée, recherche web)
   -> OUTLIER
   ```

   La règle NA/OUTLIER est appliquée en fin de cascade, de façon vectorisée. La résolution porte
   sur les **valeurs distinctes**, jamais ligne par ligne, puis est rediffusée sur l'ensemble des
   lignes.
7. **Qualité** : taux de conformité et de normalisation du run, compteurs cumulés sur l'historique
   traité, répartition des outliers par champ et par banque.
8. **Livrables** : classeur de classification à chemin stable (écrasé à chaque run, pour que la
   restitution Power BI pointe toujours le même fichier), rapport PDF, email de fin de run.
9. **État** : horodatage traité, statut, compteurs cumulés, pour le run suivant.

Un échec à n'importe quelle étape laisse l'état inchangé, notifie par email et retourne un code de
sortie non nul : le run suivant reprend le même delta. Une exécution sur fichier local (`--input`)
est entièrement hors ligne : ni email, ni dépôt distant, ni écriture d'état.

---

## 5. Méthodes de résolution et traçabilité

Chaque valeur normalisée est accompagnée d'une méthode, conservée dans les livrables. Elle
distingue ce qui était **déjà connu avant le run** de ce qui a été **tranché pendant** le run, et
alimente les indicateurs « nouvelles valeurs » des notifications.

| Déjà connu avant le run | Tranché pendant le run |
|---|---|
| `WARM` (cache validé), `MAP`, `MAP_CIBLE`, `ALIAS`, `NUM`, `STRIP`, `PREFIX`, `NOISE`, `NIF_EXACT`, `PUBLIC_ENT`, `DGI_EXACT_NORM`, `DGI_FUZZY_STRONG` | `CLAUDE`, `DGI_CLAUDE_ARBITRAGE`, `DGI_NO_MATCH`, `PARTICULIER`, `ETS_PERSONNEL`, `ETS_OUTLIER`, `OUTLIER` |

Les déductions par mots-clés (`PARTICULIER`, `ETS_*`) sont classées « nouvelles » : elles portent
sur une valeur jamais validée par un humain ni trouvée dans une source de référence.

---

## 6. Données et persistance

| Élément | Nature | Emplacement | Versionné |
|---|---|---|---|
| Tables source | lecture seule stricte | SQL Server, base du datawarehouse | — |
| Référentiels métier | validés par le métier | `{api}/referentiel/*.json` | oui |
| Caches de classification | résolutions validées et corrections manuelles ; priment sur toute la cascade | `{api}/referentiel/validated_classif_*.json` | oui |
| Historique des corrections | traçabilité des validations métier | `{api}/referentiel/corrections_history_*.json` | oui |
| État incrémental | dernier horodatage traité, statut, compteurs cumulés | `{STATE_DIR}/{API}_run_state.json` | non |
| Mémoire des rapprochements fiscaux | données dérivées, reproductibles, invalidées si la base fiscale change | `{STATE_DIR}/dgi_resolution_*.json` | non |
| Livrables | classeur de classification, rapport PDF | `{OUTPUT_BASE}/{API}/` | non |
| Fichiers de référence externes | base fiscale DGI, liste des entreprises publiques | `req/`, chemins en configuration | non |
| Secrets | base, LLM, SMTP | `.env` | non |

Les caches de classification sont la mémoire économique de la chaîne : une valeur résolue une fois
par un appel payant n'est jamais repayée. Un échec **technique** d'appel LLM n'est jamais mis en
cache : la valeur reste `OUTLIER` pour ce run et est réessayée au suivant.

---

## 7. Boucle de correction métier

Les valeurs non rattachées apparaissent dans les onglets de classification du classeur et dans le
rapport PDF. Le métier renseigne un classeur comportant un onglet `Instructions`
(`Champ | Input | Label_Attendu`), qui est ensuite appliqué : chaque ligne est routée vers le cache
du champ nommé, sous plusieurs formes de clé, pour que la correction soit retrouvée quelle que soit
la convention de nettoyage du champ. La correction prime dès le run suivant — la valeur ressort
alors avec la méthode `WARM` — et reste tracée dans l'historique des corrections, affiché en
lecture seule dans le classeur suivant.

---

## 8. Dépendances externes et modes de défaillance

| Dépendance | Usage | Comportement en cas de défaillance |
|---|---|---|
| SQL Server, en lecture seule | source unique des données | arrêt du run, email d'échec, état inchangé, delta repris au run suivant |
| Base fiscale DGI, entreprises publiques (fichiers) | rapprochement des entités mauritaniennes | chemin de la base fiscale absent : erreur explicite au démarrage ; liste des entreprises publiques absente : dégradation propre, aucune entreprise publique reconnue |
| API LLM | résolution des valeurs nouvelles sur liste fermée, arbitrage entre candidats, recherche web | échec technique : valeur laissée en `OUTLIER`, jamais mise en cache, réessayée au run suivant |
| SMTP | notification de fin de run | échec d'envoi : le run reste réussi, l'échec est journalisé |
| Dépôt de fichiers distant (optionnel) | diffusion des livrables | non bloquant ; le stockage local ou réseau est le mécanisme principal |

Aucune dépendance externe n'est sur le chemin critique de l'intégrité : un échec LLM, SMTP ou de
diffusion ne corrompt ni les caches, ni l'état, ni les livrables déjà produits.

---

## 9. Performances mesurées

Relevés sur l'environnement de recette en septembre 2026 : poste Windows, pilote ODBC historique
« SQL Server », accès par VPN.

| Poste | Mesure |
|---|---|
| Lecture de 2,98 M de lignes (E10) | 5 à 6 min, colonnes texte converties à la lecture (gain ×4 à ×9 par rapport à la lecture brute) |
| Run complet E10, livrables compris | 11 min 36 s |
| Traitement des 7 champs d'E10, en parallèle | ~4 min |
| Rapprochement fiscal, 8 000 libellés nouveaux | ~25 min au premier passage, puis évité par la mémoire des résolutions |
| Recherche web, 1 771 valeurs nouvelles (E07) | 355 appels, 45 à 90 min avec 4 lots en parallèle, puis mis en cache définitivement |
| Runs incrémentaux | quelques minutes : caches chauds, peu de valeurs nouvelles |

Seul le premier chargement d'un endpoint est coûteux, et il est bornable par configuration (date de
début d'historique, plafond de valeurs nouvelles envoyées au LLM par run). Le régime permanent est
un traitement de delta.

---

## 10. Exécution actuelle

Un poste Windows exécute une tâche planifiée qui enchaîne les 5 endpoints en mode incrémental.
Chaque endpoint gère son propre succès, son échec et son état : l'échec de l'un n'empêche pas le
lancement des suivants. Les journaux sont écrits par run et par endpoint dans `logs/`. Un
chargement initial manuel par endpoint est nécessaire avant la première mise sous planification,
pour amorcer l'état incrémental.

---

## 11. Limites connues de l'existant

- Exécution sur un poste utilisateur, en session interactive, avec accès par VPN et pilote ODBC
  historique : la disponibilité et le débit de lecture dépendent de ce poste.
- Secrets portés par un fichier `.env` local ; fichiers de référence externes (base fiscale,
  entreprises publiques) déposés manuellement, sans contrôle d'empreinte entre postes.
- Enchaînement des endpoints par script séquentiel : pas de reprise par étape, pas d'historique
  d'exécution consolidé, pas d'alerte en dehors de l'email de fin de run.
- Livrables au format Excel : plafond de lignes atteint sur les gros volumes d'anomalies, d'où une
  bascule automatique vers un CSV complet au-delà d'un seuil configuré.
- Coût des appels LLM non plafonné par défaut : le plafond par run existe, mais n'est pas activé.
- Observabilité limitée aux journaux de fichiers et à l'email : aucune métrique exportée.

---

Les règles métier détaillées figurent dans [FUNCTIONAL.md](FUNCTIONAL.md), les choix de conception
dans [ARCHITECTURE.md](ARCHITECTURE.md), les réglages dans [CONFIGURATION.md](CONFIGURATION.md).
