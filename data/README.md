# Données

Générateur de données fictives : produits, ventes, stocks et catalogue fournisseurs.

*Fake-data generator: products, sales, stock and supplier catalog.*

## Commandes

```bash
export DATABASE_URL=postgresql://lso:lso@localhost:5432/lso
python -m data.migrate                                       # schémas sales, inventory, catalog
python -m data.generate --seed 42 --if-empty                  # ne touche jamais une base déjà remplie
python -m data.verify                                        # 10 contrôles + empreinte
```

| Fichier | Rôle |
|---|---|
| `migrate.py` | Applique `services/*/migrations/*.sql`, idempotent, détecte une migration modifiée |
| `catalog_spec.py` | Catégories, modèles de produits, fournisseurs fictifs, scénarios imposés |
| `generate.py` | Simulation quotidienne (ventes, réassort, ruptures) avec graine fixe |
| `verify.py` | Contrôles d'intégrité et empreinte de reproductibilité |

Détails : [docs/partie3/data-model.md](../docs/partie3/data-model.md).
