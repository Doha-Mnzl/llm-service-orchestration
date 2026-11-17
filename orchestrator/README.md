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
| `FAIL_SERVICES` | ex. `inventory,analysis` : simule des pannes | vide |
| `MAX_RETRIES`, `HTTP_TIMEOUT`, `MAX_TOOL_CALLS` | | `2`, `5`, `8` |

Exemple de panne simulée : `FAIL_SERVICES=inventory uvicorn orchestrator.main:app --port 8000`

## Tests

```bash
pytest -v
```
