# Service stocks

Stock disponible, jours de couverture, surstocks et ruptures.

*Available stock, days of coverage, overstock and stockouts.*

## Lancer et tester

```bash
# depuis la racine du dépôt, base migrée et générée (voir docs/partie3/README.md)
DATABASE_URL=postgresql://lso:lso@localhost:5432/lso uvicorn services.inventory.main:app --port 8002
# documentation interactive : http://localhost:8002/docs
TEST_DATABASE_URL=postgresql://lso:lso@localhost:5433/lso_test pytest tests/inventory
```

| Fichier | Rôle |
|---|---|
| `main.py` | Routes FastAPI et schémas de réponse |
| `metrics.py` | Calculs métier purs (testables sans base) |
| `config.py` | Variables d'environnement |
| `db.py` | Connexion PostgreSQL ; base injoignable ⇒ HTTP 503 |
| `migrations/` | Schéma `inventory` versionné (appliqué par `python -m data.migrate`) |
| `Dockerfile`, `requirements.txt` | Image du service (contexte : racine du dépôt) |

Contrat et définitions : [docs/partie3/api.md](../../docs/partie3/api.md).
