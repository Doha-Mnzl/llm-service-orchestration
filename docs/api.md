# Contrat d'API des services — PROPOSITION à valider

> Brouillon proposé par Doha (Partie 2). **Manal (Partie 3)** et **Zakaria (Partie 1)** :
> relisez, corrigez dans la pull request, puis on fige le contrat.
> L'orchestrateur utilise déjà ces formats dans ses faux services (`orchestrator/mock_services.py`).

Conventions communes :
- JSON en entrée et en sortie, montants en euros, identifiants `P001` (produit du site) et `S101` (produit fournisseur).
- Chaque service expose `GET /health` → `{"status": "ok"}` (utilisé par l'interface et les tests de panne).
- Erreurs : `404` si l'objet n'existe pas, `422` si les paramètres sont invalides, `5xx` = panne.

Ports proposés : ventes `8001`, stocks `8002`, catalogue `8003`, orchestrateur `8000`, interface `8501`.

---

## Service ventes (Manal) — port 8001

### `GET /sales/summary?period_months=1&category=câbles`
| Paramètre | Type | Obligatoire | Remarque |
|---|---|---|---|
| period_months | int 1–24 | non (1) | |
| category | str | non | |

```json
{"period_months": 1,
 "items": [{"product_id": "P003", "name": "Souris filaire basique", "category": "souris",
            "units_sold": 18, "revenue": 126.0, "margin_rate": 0.12, "trend_6m": -0.64}]}
```

### `GET /sales/trend/{product_id}?months=6`
```json
{"product_id": "P003", "monthly_units": [40, 35, 30, 26, 21, 18]}
```

## Service stocks (Manal) — port 8002

### `GET /stocks?product_ids=P003&product_ids=P005&category=souris`
```json
{"items": [{"product_id": "P003", "name": "Souris filaire basique", "category": "souris",
            "quantity": 340, "coverage_days": 566}]}
```

### `GET /stocks/alerts?kind=overstock|stockout|all`
```json
{"overstock": [{"product_id": "P005", "name": "...", "quantity": 280, "coverage_days": 700}],
 "stockout":  [{"product_id": "P006", "name": "...", "quantity": 0}]}
```

## Service catalogue (Manal) — port 8003

### `GET /catalog/search?category=câbles&query=usb&max_price=10`
```json
{"items": [{"supplier_product_id": "S101", "name": "Câble USB-C tressé 2 m",
            "category": "câbles", "price": 6.9}]}
```

### `POST /decisions` (appelé par l'orchestrateur, seulement après la décision humaine)

Contrat sécurisé du catalogue (détails : `docs/partie3/api.md`, Partie 3).

```http
POST /decisions
Authorization: Bearer <CATALOG_TOKEN>
Idempotency-Key: <action_id>

{"product_id": "P005", "action": "replace", "replacement_id": "S104",
 "reason": "Ventes en forte baisse et stock élevé",
 "decision": "approved", "decided_by": "doha"}
```
`decision` vaut `approved` ou `refused` : les refus sont aussi enregistrés.
Réponses : `201` créée ; `200` rejeu identique (même clé) ; `401` jeton absent ou invalide ;
`404` produit inconnu ; `409` même clé avec d'autres champs ; `422` données invalides.
Sur toute erreur, l'orchestrateur garde la proposition en attente.

---

## Service analyse (Zakaria) — worker RabbitMQ, 2 instances

L'orchestrateur publie une tâche et attend la réponse (modèle RPC : `reply_to` + `correlation_id`).

| File | Message envoyé | Réponse |
|---|---|---|
| `analysis.underperforming` | `{"period_months": 1, "top_n": 5}` | `{"period_months": 1, "items": [{"product_id", "name", "category", "trend_6m", "margin_rate", "score"}]}` |
| `analysis.replacements` | `{"product_id": "P005", "max_results": 3}` | `{"product_id": "P005", "candidates": [{"supplier_product_id", "name", "category", "price"}]}` |

Points à décider ensemble :
- le worker lit-il directement PostgreSQL (schémas ventes/stocks/catalogue) ou appelle-t-il les services REST ?
- files durables + `ack` manuel après traitement, pour que l'autre worker reprenne la tâche si un worker tombe ;
- timeout côté orchestrateur : 5 s, puis 2 reprises.

## Correspondance outils LLM → services

| Outil (orchestrator/tools.py) | Service | Route / file | Si panne |
|---|---|---|---|
| get_sales_summary | ventes | GET /sales/summary | arrêt |
| get_sales_trend | ventes | GET /sales/trend/{id} | arrêt |
| get_stock_levels | stocks | GET /stocks | réponse partielle |
| get_stock_alerts | stocks | GET /stocks/alerts | réponse partielle |
| find_underperforming_products | analyse | analysis.underperforming | arrêt |
| find_replacements | analyse | analysis.replacements | arrêt |
| search_supplier_catalog | catalogue | GET /catalog/search | réponse partielle |
| record_decision | catalogue | POST /decisions | validation humaine obligatoire |
