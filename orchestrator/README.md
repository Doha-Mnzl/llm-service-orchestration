# Orchestrateur

Microservice central (FastAPI) : reçoit les demandes, dialogue avec le LLM, contrôle les actions et appelle les services.

*Central microservice (FastAPI): receives requests, talks to the LLM, controls actions and calls the services.*

## Fichiers

| Fichier | Rôle |
|---|---|
| `main.py` | Routes de l'API (`/health`, `/chat`, `/tools`, `/actions/...`, `/services/health`) |
| `agent.py` | Boucle LLM → contrôle → exécution, validation humaine, gestion des pannes |
| `tools.py` | Liste blanche des outils et validation des arguments (pydantic) |
| `llm.py` | Faux LLM, Groq et Ollama, interchangeables par `LLM_PROVIDER` |
| `executor.py` | Appels aux services : timeout, 2 reprises |
| `mock_services.py` | Faux services pour avancer sans les parties 1 et 3 |
| `config.py` | Réglages par variables d'environnement |

## Lancer (depuis la racine du dépôt)

```bash
source venv/Scripts/activate        # Git Bash sous Windows
pip install -r orchestrator/requirements.txt
uvicorn orchestrator.main:app --reload --port 8000
```
Ouvrir http://127.0.0.1:8000/docs et tester `POST /chat` avec
`{"message": "Quels produits dois-je retirer ce mois-ci, et par quoi les remplacer ?"}`.

## Réglages utiles

| Variable | Valeurs | Défaut |
|---|---|---|
| `LLM_PROVIDER` | `fake`, `groq`, `ollama` | `fake` |
| `GROQ_API_KEY` | clé Groq (ne jamais la commiter : fichier `.env`) | — |
| `USE_MOCK_SERVICES` | `true` / `false` | `true` |
| `CATALOG_TOKEN` | jeton de service de l'orchestrateur ; même valeur que `orchestrator:<jeton>` dans `CATALOG_SERVICE_TOKENS` du catalogue (fichier `.env`, jamais commité) | vide |
| `FAIL_SERVICES` | ex. `inventory,analysis` : simule des pannes | vide |
| `MAX_RETRIES`, `HTTP_TIMEOUT`, `MAX_TOOL_CALLS` | | `2`, `5`, `15` |

Exemple de panne simulée : `FAIL_SERVICES=inventory uvicorn orchestrator.main:app --port 8000`

## Validation humaine et contrat du catalogue

`POST /actions/{action_id}/decision` avec `{"approved": true|false, "decided_by": "<nom du responsable>"}`.

- Acceptée **ou refusée**, la décision est envoyée au catalogue (`POST /decisions`), pour garder un
  historique complet. En-têtes : `Authorization: Bearer $CATALOG_TOKEN` et `Idempotency-Key: <action_id>`
  (la même clé à chaque reprise : pas de doublon). Corps : arguments proposés par le LLM +
  `decision` (`approved`/`refused`) + `decided_by`. Ces deux champs viennent de l'humain : le LLM ne
  peut pas les fournir (`RecordDecisionArgs` les refuse).
- Réponses : `executed` (acceptée et enregistrée), `refused` (refus enregistré), `failed` (catalogue en
  panne ou refus HTTP : `catalog_status` = 401, 404, 409, 422…). En cas d'échec, la proposition **reste en
  attente** : on peut réessayer, rien n'est perdu.

## Tests

```bash
pytest -v
```
