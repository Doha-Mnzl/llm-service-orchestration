# Service ventes

Ventes, chiffre d'affaires, marge et évolution par produit.

*Sales, revenue, margin and trends per product.*

## Lancer et tester

```bash
# depuis la racine du dépôt, base migrée et générée (voir docs/partie3/README.md)
DATABASE_URL=postgresql://lso:lso@localhost:5432/lso uvicorn services.sales.main:app --port 8001
# documentation interactive : http://localhost:8001/docs
TEST_DATABASE_URL=postgresql://lso:lso@localhost:5433/lso_test pytest tests/sales
```

| Fichier | Rôle |
|---|---|
| `main.py` | Routes FastAPI |
| `schemas.py`, `repository.py` | Schémas de réponse, requêtes SQL |
| `metrics.py` | Calculs métier purs (testables sans base) |
| `config.py` | Variables d'environnement |
| `db.py` | Connexion PostgreSQL ; base injoignable ⇒ HTTP 503 |
| `migrations/` | Schéma `sales` versionné (appliqué par `python -m data.migrate`) |
| `Dockerfile`, `requirements.txt` | Image du service (contexte : racine du dépôt) |

Contrat et définitions : [docs/partie3/api.md](../../docs/partie3/api.md).
