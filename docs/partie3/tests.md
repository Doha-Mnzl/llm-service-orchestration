# Tests de la Partie 3 (LSO-14)

## Commandes

```bash
# Base PostgreSQL dédiée aux tests (son nom doit contenir « test » ; elle est réinitialisée)
docker run -d --name lso-p3-postgres -e POSTGRES_USER=lso -e POSTGRES_PASSWORD=lso \
  -e POSTGRES_DB=lso_test -p 127.0.0.1:5433:5432 postgres:16-alpine

# Tous les tests unitaires, fonctionnels et d'interface
TEST_DATABASE_URL=postgresql://lso:lso@localhost:5433/lso_test pytest

# Intégration live : pile ISOLÉE (ports 29xxx, base lso_integration_test), puis tests, puis arrêt
python scripts/partie3_demo.py start --port-base 29000 --database lso_integration_test \
  --orchestrator-src ../orch --legacy-catalog
LIVE_SERVICES=1 \
LIVE_SALES_URL=http://127.0.0.1:29001 LIVE_INVENTORY_URL=http://127.0.0.1:29002 \
LIVE_CATALOG_URL=http://127.0.0.1:29003 LIVE_LEGACY_CATALOG_URL=http://127.0.0.1:29013 \
LIVE_ORCHESTRATOR_URL=http://127.0.0.1:29010 \
LIVE_STACK_DATABASE_URL=postgresql://lso:lso@localhost:5433/lso_integration_test \
LIVE_CATALOG_TOKEN="$(cat "$XDG_RUNTIME_DIR/lso-partie3-demo/29000/catalog_token")" \
ORCHESTRATOR_SRC=../orch pytest tests/integration
python scripts/partie3_demo.py stop --port-base 29000
```

La garde (`tests/integration/live.py`) refuse toute URL absente, tout port de la démonstration
existante (8000–8003, 8010, 8011, 8501, 8502) et toute base sans « test » ; des canaris vérifient que le
catalogue et l'exécuteur de l'orchestrateur lisent bien la base de la pile avant toute écriture.

Sans `TEST_DATABASE_URL`, les tests PostgreSQL sont **ignorés avec un message** (et non en échec) :
le `pytest` des autres membres reste vert. Les tests d'intégration sont ignorés sans `LIVE_SERVICES`.

## Validation du 23/11/2026 avant les commits

Les huit lots ont été assemblés successivement dans une copie propre. Le premier reprenait
l’index et les corrections du générateur et de son test. Les essais ont utilisé un environnement
Python neuf et un cluster PostgreSQL 16 temporaire sur socket Unix privé.

| Périmètre | Résultat |
|---|---|
| Data, dont deux remplissages concurrents avec `--if-empty` | 60 réussis. Un seul processus remplit les tables, l’autre renvoie `skipped`. |
| Configuration commune, puis Sales, Inventory et Catalog | Imports réussis, puis respectivement 21, 23 et 80 tests réussis. |
| Interface Streamlit | 45 réussis. |
| Lanceur et gardes des tests live | 30 réussis, dont le refus d’un port occupé, l’arrêt d’un enfant et le délai de démarrage dépassé. |
| Intégration live avec la révision externe `06e5b51` | 29 réussis, 1 échec attendu. Le catalogue refuse l’écriture sans jeton, puis l’orchestrateur renvoie HTTP 500 et perd la proposition. |
| Partie 3 complète avec PostgreSQL | 261 réussis, 16 ignorés (les tests live ont leur exécution dédiée). |
| Copie combinée avec les tests de l’orchestrateur à `06e5b51` | 270 réussis, 16 ignorés. |
| Partie 3 sans base | 91 réussis, 186 ignorés. |

L’audit du seed sur la pile live a renvoyé zéro anomalie sur dix contrôles, avec l’empreinte
`a00387d7594a5ad5`. Les suites FastAPI signalent un avertissement de dépréciation du client
de test Starlette utilisant `httpx`.

## Exécution du 22/11/2026 après revue de code

| Exécution | Résultat |
|---|---|
| Suite complète (`lso_test`) | **255 PASS, 0 FAIL, 16 SKIP** (intégration live sans `LIVE_SERVICES=1`) |
| Intégration live (pile isolée 29xxx) | **29 PASS, 1 XFAIL attendu** : défaut de la PR #1 (HTTP 500 et proposition perdue après un refus du catalogue), test strict prêt |
| Fusion simulée avec la PR #1 | **264 PASS** (dont les 9 tests de l'orchestrateur), 16 SKIP |
| Poste sans base | 86 PASS, 185 SKIP avec message |

Corrigé dans cette ronde (chaque cas reproduit par un test rouge) : proposition revenue en file après un
timeout jamais bloquée (état « absente » non final, rapprochement automatique) ; succès annoncé seulement
après relecture d'une décision « approved » dans le catalogue et mode réel confirmé ; 404 non final ;
mode détecté uniquement par un booléen explicite (inconnu ⇒ aucune donnée réelle affichée) ; bornes Sales
dérivées de la même date d'analyse ; couverture exacte arrondie au plus proche ; état Inventory reconstruit
à une date antérieure (disponible inconnu, ruptures vues à cette date) ; « Aucune vente » fondé sur les
unités ; migrations sérialisées par verrou consultatif ; audit lu dans un instantané cohérent ; journal des
jetons sans valeur ; lanceur (connexion par l'environnement, PID publiés après confirmation, noms validés) ;
comparateur de contrat strict (listes non vides, types, union des éléments) ; modèle des alertes.

## Exécution précédente (21/11/2026, après la revue finale)

| Exécution | Commande | Résultat |
|---|---|---|
| Suite complète | `TEST_DATABASE_URL=postgresql://lso:lso@localhost:5433/lso_test pytest` | **226 PASS, 0 FAIL, 15 SKIP** (intégration live : `LIVE_SERVICES=1` non défini) |
| Intégration live | pile isolée `scripts/partie3_demo.py start --port-base 29000 --database lso_integration_test --legacy-catalog` puis `LIVE_*=… pytest tests/integration` | **27 PASS** (12 tests de la garde + 15 live) |
| Fusion simulée avec la PR #1 | copie combinée, même commande | **235 PASS** (dont les 9 tests de l'orchestrateur), 15 SKIP |
| Poste sans base | `pytest` | 65 PASS, 176 SKIP avec message |

Nouveaux tests de cette ronde : clé d'idempotence obligatoire (400), mode legacy limité à la forme exacte
de l'appel (422 sinon, source `legacy`, déduplication concurrente), `/health` et démarrage signalant le
mode legacy, page Décisions gardant le résultat, mode simulé sans données réelles (et mode inconnu traité
comme non réel), garde des tests live (aucune URL par défaut, ports de démo et `lso_dev` refusés, canaris
catalogue et exécuteur), lanceur de démo sûr (`tests/scripts/test_demo_launcher.py` : dossier privé,
aucun lien suivi, environnement imposé, base vérifiée sur la chaîne finale, PID vérifiés).
Un test du lanceur s'est révélé intermittent (course fork/exec dans le test lui-même) : corrigé, puis
répété 20 fois sans échec.

## Résultats (exécution du 21/11/2026 après corrections H1–H6, Python 3.11.14, PostgreSQL 16, Streamlit 1.67)

Base isolée `lso_test` (garde-fou : les tests refusent toute base dont le nom ne contient pas le mot
« test »). Chaque correction H1–H6 a d'abord été prouvée par un test rouge.

| Domaine | Fichier | Tests | Résultat | Ce qui est vérifié |
|---|---|---|---|---|
| Schémas (LSO-5) | `tests/data/test_migrations.py` | 26 | PASS | 6 domaines, migrations 001 + 002 idempotentes, migration modifiée détectée, 19 contraintes rejetées (chaque insertion ne viole que la contrainte visée), audit en ajout seul |
| Données (LSO-6, LSO-21) | `tests/data/test_generator.py` | 17 | PASS | 0 anomalie, 200 candidats, scénarios observables en SQL, reproductibilité, commandes documentées |
| **H1** préservation | `tests/data/test_reset_guards.py` | 15 | PASS | `--reset` refusé s'il existe des décisions humaines (CLI et fonctions) ; séquence de démarrage rejouée 2× sans perte (empreinte identique) ; `--if-empty` ; **course reproduite** : décision validée pendant une réinitialisation jamais effacée (verrou exclusif) ; garde du nom de base de test |
| Ventes (LSO-8, 16, 17) | `tests/sales/test_sales_api.py` | 19 | PASS | calculs exacts sur jeu calculé à la main, filtres, 404/422/503 |
| Stocks (LSO-9, 18, 19) | `tests/inventory/test_inventory_api.py` | 16 | PASS | couverture et cas limites, inconnus signalés, mouvements, ruptures, 503 |
| Catalogue (LSO-10, 20, 21) | `tests/catalog/test_catalog_api.py` | 12 | PASS | recherche, pagination, candidats existants |
| Décisions (LSO-11, 22, 23) | `tests/decisions/test_decisions_api.py` | 14 | PASS | décision explicite auditée (acteur déclaré + client authentifié), validations, refus, cycle de vie, produit jamais modifié |
| **H2 + H4** | `tests/decisions/test_decisions_security.py` | 28 | PASS | 401 sans jeton / jeton faux / schéma faux / avant tout accès base ; pas d'approbation implicite (422) ; identité issue du jeton, `source`/`recorded_by_client` du corps ignorés ; aucun jeton ⇒ refus ; jetons courts, partagés ou réservés rejetés ; mode legacy limité à `POST /decisions` ; rejeu identique 200 ; **6 variations** d'un champ significatif ⇒ 409 ; clés propres à chaque client ; clés invalides ou ambiguës 422 ; 8 requêtes simultanées ⇒ 1 décision et 1 événement ; rejeu de transition (même cible + même décideur, après une vraie transition) |
| **H3** transactions | `tests/decisions/test_transactions.py` | 7 | PASS | échec au COMMIT (déclencheur différé) ⇒ 500 et rien d'écrit ; échec de l'audit ⇒ décision annulée ; échec de transition ⇒ proposition toujours `pending` ; succès relu par une autre connexion ; cause racine sur les 3 services (rouge prouvé sur l'ancien `get_conn` : 201 malgré l'échec du COMMIT) |
| Interface + **H6** | `tests/frontend/test_ui.py` | 25 | PASS | parcours précédents + panne / délai / 500 ⇒ « résultat inconnu » puis vérification réelle avant nouvelle tentative ; action disparue ⇒ jamais de faux succès ; orchestrateur toujours en panne ⇒ état incertain conservé ; échec signalé par l'orchestrateur ⇒ nouvelle tentative ; refus non présenté comme archivé ; délai de validation couvrant les reprises de l'orchestrateur ; statuts inconnus sans plantage |
| Intégration Partie 2 + **H5** | `tests/integration/test_orchestrator_compat.py` | 15 | PASS | outils de lecture avec l'exécuteur de la PR #1 ; catalogue sûr ⇒ `record_decision` actuel refusé (401) ; catalogue legacy ⇒ accepté et marqué `legacy-unauthenticated` ; contrat cible : rejeu réseau avec clé ⇒ 1 décision, champ modifié ⇒ 409 |
| **Total** | | **194** | **PASS** | 179 sur `lso_test` + 15 d'intégration sur une pile isolée (ports 18001–18013) |

Vérifications complémentaires exécutées :

| Vérification | Résultat |
|---|---|
| `pytest` sans base (poste d'un collègue) | 36 PASS, 158 ignorés avec message |
| `TEST_DATABASE_URL` pointant sur `lso_dev` | refusé (`UsageError`) avant toute écriture ; `lso_dev` intacte |
| Fusion simulée Partie 3 + PR #1 | 188 PASS (dont les 9 tests de l'orchestrateur), 15 ignorés |
| Mise à niveau 001 → 002 sur une copie de la base de démonstration | 002 appliquée une fois ; décisions conservées (rattachées à `seed`) ; empreinte identique avant/après ; `--if-empty` ne modifie rien |
| QA navigateur (Playwright Chromium, sans tête) du parcours H6 | PASS lors du contrôle du 21/11/2026 |
| Revue de sécurité (sous-agent) + filtrage | aucune vulnérabilité de confiance ≥ 7 |
| Revue de code (`code-review`) | 10 constats : 1 déjà corrigé, 9 corrigés avec tests de régression |

## Non testé ou bloqué

| Élément | Raison |
|---|---|
| Scénario « retirer / remplacer » sur les vrais services | Worker Analysis et RabbitMQ (Partie 1) absents : l'orchestrateur s'arrête proprement (testé) |
| Docker Compose complet | `docker-compose.yml` (Partie 1) vide ; proposition fournie, non appliquée |
| Vrai LLM (Groq/Ollama) | Hors périmètre de la Partie 3 ; l'interface ne dépend que de l'API de l'orchestrateur |
| Tests de charge et de panne globaux | Partie 1 |
