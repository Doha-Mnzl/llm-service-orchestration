


# Partie 3 — Données, services et interface

Responsable : Manal Farouqi · Epic Jira LSO-3 · Projet 6 « Orchestration intelligente de services par un LLM »

Cette partie fournit les **données**, les trois **microservices métier** (ventes, stocks, catalogue
et décisions) et l'**interface Streamlit**. Elle est consommée par l'orchestrateur (Partie 2) et
par le worker Analysis (Partie 1).

| Document | Contenu |
|---|---|
| [api.md](api.md) | Référence des API (routes, paramètres, réponses, erreurs) |
| [data-model.md](data-model.md) | Schémas PostgreSQL, contraintes, propriété des données |
| [decisions-equipe.md](decisions-equipe.md) | Décisions communes D-1…D-6 : hypothèses retenues, points à valider |
| [tests.md](tests.md) | Commandes de test et résultats PASS/FAIL |
| [compose-proposition.yml](compose-proposition.yml) | Proposition de services Compose pour la Partie 1 (non appliquée) |
| [guide-demo.md](guide-demo.md) | Comprendre la Partie 3 et la tester soi-même (pile isolée) |

La branche `feature/data-services-interface` cible `develop` pour la revue de la Partie 3.
La [PR #1](https://github.com/Doha-Mnzl/llm-service-orchestration/pull/1) de l’orchestrateur a été fermée sans fusion. Les essais de compatibilité utilisent sa révision externe `06e5b51`.

## Architecture

```mermaid
flowchart LR
    U([Responsable du magasin]) --> UI

    subgraph P3[Partie 3]
        UI[Interface Streamlit<br/>:8501]
        S[Service ventes<br/>:8001]
        I[Service stocks<br/>:8002]
        C[Service catalogue<br/>+ décisions<br/>:8003]
        G[[Générateur<br/>data.generate]]
    end

    subgraph P2[Partie 2]
        O[Orchestrateur FastAPI<br/>:8000]
        L[LLM<br/>fake / Groq / Ollama]
    end

    subgraph P1[Partie 1]
        Q[(RabbitMQ)]
        A[Worker Analysis ×2]
        DB[(PostgreSQL<br/>schémas sales · inventory · catalog)]
    end

    UI -- "chat, Valider / Refuser,<br/>état du système" --> O
    UI -. "lecture seule GET<br/>tableaux de bord (D-5)" .-> S & I & C
    O <--> L
    O -- "REST + contrôle des outils" --> S & I & C
    O -- "RPC (à brancher)" --> Q --> A
    A -. "lecture REST (D-2)" .-> S & I & C
    S -- "schéma sales" --> DB
    I -- "schéma inventory" --> DB
    C -- "schéma catalog" --> DB
    G -- "seed reproductible" --> DB
```

Règles appliquées dans le code :

- **Un schéma par service**, aucun accès croisé : les autres composants passent par REST.
- **Toute écriture passe par l'orchestrateur** : l'interface n'appelle jamais `POST /decisions` et ne
  connaît pas le fournisseur LLM (vérifié par `tests/frontend/test_ui.py`).
- **Aucune modification commerciale automatique** : une décision validée est enregistrée et auditée ;
  aucun produit n'est supprimé ni modifié.
- **Aucun résultat inventé** : un service indisponible produit une erreur visible ; l'interface
  n'affiche que les valeurs renvoyées par les API.
- **Pannes** : base injoignable ⇒ HTTP 503 (l'orchestrateur réessaie puis arrête ou répond
  partiellement) ; objet inconnu ⇒ 404 ; paramètre invalide ⇒ 422.

## Interactions avec les autres parties

| Avec | Ce que la Partie 3 fournit | État réel (21/11/2026) |
|---|---|---|
| Orchestrateur (Partie 2, PR #1) | Routes de `docs/api.md` avec les mêmes noms de champs ; champs ajoutés seulement | **Intégré et testé** : 5 outils HTTP + `record_decision` fonctionnent sur les vrais services avec l'exécuteur de Doha |
| Analysis (Partie 1) | Tendances, marges, stocks, candidats fournisseurs (`/catalog/candidates/{id}`) | **Non intégré** : le worker et RabbitMQ n'existent pas encore ; le scénario « retirer / remplacer » s'arrête proprement |
| Docker Compose (Partie 1) | Dockerfiles par service, proposition Compose | **Non intégré** : `docker-compose.yml` est vide ; images construites et testées localement |

## Lancer la Partie 3 en local

Prérequis : Python 3.11, Docker.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-partie3-dev.txt

# 1. PostgreSQL (en attendant le Compose de la Partie 1)
docker run -d --name lso-postgres -e POSTGRES_USER=lso -e POSTGRES_PASSWORD=lso \
  -e POSTGRES_DB=lso -p 127.0.0.1:5432:5432 postgres:16-alpine
export DATABASE_URL=postgresql://lso:lso@localhost:5432/lso

# 2. Schémas et données (graine fixe, date de référence 2026-10-31)
python -m data.migrate
python -m data.generate --seed 42 --if-empty   # ne touche jamais une base déjà remplie
python -m data.verify                    # 0 anomalie attendue

# 3. Services (un terminal chacun, depuis la racine)
uvicorn services.sales.main:app --port 8001
uvicorn services.inventory.main:app --port 8002
CATALOG_SERVICE_TOKENS="orchestrator:$(openssl rand -hex 24)" uvicorn services.catalog.main:app --port 8003

# 4. Interface (orchestrateur attendu sur ORCHESTRATOR_URL, défaut http://localhost:8000)
cd frontend && streamlit run app.py
```

Documentation interactive : http://localhost:8001/docs, :8002/docs, :8003/docs.

### Variables d'environnement

| Variable | Composant | Défaut | Rôle |
|---|---|---|---|
| `DATABASE_URL` (ou `SALES_/INVENTORY_/CATALOG_DATABASE_URL`) | services, données | `postgresql://lso:lso@localhost:5432/lso` | Connexion PostgreSQL |
| `CURRENCY` | ventes, catalogue | `MAD` | Devise renvoyée (D-1) |
| `AS_OF_DATE` | ventes, stocks | dernière date en base | Date d'analyse (AAAA-MM-JJ) |
| `DEMAND_WINDOW_DAYS`, `OVERSTOCK_DAYS` | stocks | `30`, `180` | Règles de couverture (D-4) |
| `CATALOG_SERVICE_TOKENS` | catalogue | vide (toute écriture refusée) | Jetons des clients autorisés à écrire des décisions : `client:jeton,…` |
| `DECISIONS_LEGACY_ORCHESTRATOR` | catalogue | `false` | `true` : accepte l'orchestrateur actuel sans jeton (démo, **non sûr**) |
| `LEGACY_DECIDED_BY` | catalogue | `inconnu (orchestrateur non authentifié)` | Décideur enregistré en mode legacy (vide ⇒ défaut) |
| `LEGACY_DEDUP_SECONDS` | catalogue | `60` | Fenêtre de déduplication des reprises legacy (sans clé possible) |
| `ORCHESTRATOR_URL` | interface | `http://localhost:8000` | Seul point d'action de l'interface |
| `SALES_URL`, `INVENTORY_URL`, `CATALOG_URL` | interface | `:8001`, `:8002`, `:8003` | Lecture des tableaux de bord |
| `DASHBOARD_DIRECT_READ` | interface | `true` | `false` : aucun appel direct aux services (D-5) |

### Démonstration de bout en bout avec l'orchestrateur de la PR #1

L'orchestrateur actuel n'envoie pas encore de jeton : pour enregistrer une décision validée avec lui,
lancer le catalogue en mode de compatibilité **non sûr** (`DECISIONS_LEGACY_ORCHESTRATOR=true`).

```bash
git worktree add --detach ../orch 06e5b51      # copie de lecture, sans rien modifier
cd ../orch && USE_MOCK_SERVICES=false LLM_PROVIDER=fake \
  uvicorn orchestrator.main:app --port 8000
```

Questions qui fonctionnent sur les vrais services : « Quels produits sont en rupture ? »,
« Quelles sont les ventes du mois ? », « Cherche dans le catalogue fournisseur ». La question
« Quels produits retirer ? » s'arrête avec un message clair tant qu'Analysis n'est pas branché.

## Limites connues

1. Scénario « retirer / remplacer » non démontrable sur les vrais services avant le worker Analysis.
2. Avec l'orchestrateur actuel, un **refus** n'est pas transmis au catalogue, l'**auteur** n'est pas
   connu et aucun jeton n'est envoyé : en mode sûr, ses validations sont refusées (401). Le contrat
   cible est décrit dans [decisions-equipe.md](decisions-equipe.md) (H5).
3. Les propositions en attente vivent dans la mémoire de l'orchestrateur : elles sont perdues à son
   redémarrage. À la révision `06e5b51`, un refus HTTP 401 du catalogue entraîne aussi une réponse HTTP 500 de l’orchestrateur et la perte de la proposition.
4. Idempotence : garantie quand une clé `Idempotency-Key` est fournie (tous les champs comparés) ;
   l'orchestrateur actuel n'en envoie pas, et son exécuteur rejoue un POST après un délai dépassé.
5. Montants en DH par hypothèse (D-1), configurables ; schéma indépendant de la devise.
6. Authentification de **service** seulement (jetons) : l'auteur humain (`decided_by`) reste déclaratif ;
   les lectures `GET` ne sont pas authentifiées (réseau interne supposé).
7. `data.migrate --reset` et `data.generate --reset` refusent d'effacer des décisions humaines sans
   `--allow-decision-loss` ; les tests exigent une base dont le nom contient « test ».

## Points de soutenance

- Données reproductibles : même graine ⇒ même empreinte (`python -m data.verify`), scénarios
  vérifiés par des requêtes SQL indépendantes.
- Cohérence par construction : stock simulé jour par jour, Σ mouvements = stock physique,
  ventes = sorties de stock (vérifié).
- Calculs testés contre des valeurs calculées à la main (`tests/reference_data.py`).
- Contrat compatible avec l'orchestrateur prouvé par ses propres outils, son exécuteur et ses mocks
  (`tests/integration/`).
- Garde-fous de l'interface testés : confirmation avant décision, aucune écriture directe, pannes
  visibles, mode simulé signalé.
