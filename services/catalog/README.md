# Service catalogue

Recherche de produits dans le catalogue fournisseurs et enregistrement des décisions validées.

*Supplier catalog search and storage of approved decisions.*

## Lancer et tester

```bash
# depuis la racine du dépôt, base migrée et générée (voir docs/partie3/README.md)
DATABASE_URL=postgresql://lso:lso@localhost:5432/lso uvicorn services.catalog.main:app --port 8003
# documentation interactive : http://localhost:8003/docs
TEST_DATABASE_URL=postgresql://lso:lso@localhost:5433/lso_test pytest tests/catalog tests/decisions
```

| Fichier | Rôle |
|---|---|
| `main.py` | Produits, catalogue fournisseurs, candidats |
| `decisions.py` | Décisions humaines : création idempotente, transitions, audit |
| `config.py` | Variables d'environnement |
| `db.py` | Connexion PostgreSQL ; base injoignable ⇒ HTTP 503 |
| `migrations/` | Schéma `catalog` versionné (appliqué par `python -m data.migrate`) |
| `Dockerfile`, `requirements.txt` | Image du service (contexte : racine du dépôt) |

Contrat et définitions : [docs/partie3/api.md](../../docs/partie3/api.md).
