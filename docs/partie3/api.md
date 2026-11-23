# API des services de la Partie 3

Base de référence : la proposition `docs/api.md` de la PR #1 (non encore validée).
**Les champs de cette proposition sont conservés à l'identique** ; tout le reste est ajouté.
OpenAPI complet : `/docs` et `/openapi.json` de chaque service.

Conventions communes :

- JSON ; identifiants `P001` (produit du magasin, `^P\d{3,5}$`), `S101` (produit fournisseur,
  `^S\d{3,5}$`), `F01` (fournisseur).
- Montants : nombres décimaux à 2 décimales dans la devise `currency` (défaut `MAD`, hypothèse D-1).
- Filtres `category` et `query` insensibles à la casse et aux accents (`câbles` = `CABLES`).
- `GET /health` → `200 {"status":"ok","service":…,"database":"up"}` ;
  `503 {"status":"degraded","database":"down"}` si la base est injoignable.
- Erreurs : `404` objet inconnu, `422` paramètre invalide, `409` conflit (décisions), `503` panne.
- Périodes : `N` mois glissants se terminant à la date d'analyse (`AS_OF_DATE` ou dernière date en base).

## Service ventes — port 8001

| Route | Paramètres | Réponse |
|---|---|---|
| `GET /sales/summary` | `period_months` 1–24 (1), `category`, `product_id`, `sort` (`product_id`, `revenue`, `units`, `trend`, `margin_rate`), `limit` | `{period_months, as_of, start, end, previous_start, previous_end, currency, items[]}` |
| `GET /sales/trend/{product_id}` | `months` 1–24 (6) | `{product_id, months, monthly_units[], monthly_revenue[], periods[], currency}` |
| `GET /sales/products/{product_id}` | `period_months` | période courante, précédente, évolutions, `trend_6m` |
| `GET /sales/monthly` | `months` (12), `category`, `product_id` | totaux mensuels pour les tableaux de bord |

Élément de `items` (contrat + ajouts ; réponse réelle du seed, graine 42) :

```json
{"product_id": "P003", "name": "Souris filaire basique", "category": "souris",
 "units_sold": 18, "revenue": 1260.0, "margin_rate": 0.12, "trend_6m": -0.5068,
 "gross_margin": 151.2, "previous_revenue": 3290.0, "revenue_change": -0.617}
```

Toutes les bornes (période, période précédente, fenêtres mensuelles, tendance) dérivent directement de la
date d'analyse `as_of` : au 30/03, la période précédente d'un mois est le 31/01–28/02 partout.
Définitions (D-4) : CA = Σ qté × prix ; marge brute = Σ qté × (prix − coût) ; `margin_rate` =
marge / CA (`null` si CA nul) ; `trend_6m` = (unités des 3 derniers mois − 3 précédents) / 3
précédents (`null` si dénominateur nul). Tous les produits sont listés, même sans vente.

## Service stocks — port 8002

| Route | Paramètres | Réponse |
|---|---|---|
| `GET /stocks` | `product_ids` (répétable), `category` | `{as_of, items[], unknown_product_ids[]}` |
| `GET /stocks/alerts` | `kind` = `overstock` \| `stockout` \| `critical` \| `all` | `{as_of, overstock[], stockout[], critical[]}` |
| `GET /stocks/{product_id}` | — | niveau + `open_stockout` |
| `GET /stocks/{product_id}/movements` | `kind`, `start`, `end`, `limit` | `{items[], total_in, total_out, net}` |
| `GET /stockouts` | `product_id`, `open` | historique des ruptures avec `duration_days` |

Élément de stock (réponse réelle du seed) :

```json
{"product_id": "P005", "name": "Écouteurs filaires jack", "category": "écouteurs",
 "quantity": 404, "coverage_days": 505,
 "on_hand": 418, "reserved": 14, "available": 404, "reorder_point": 20,
 "avg_daily_demand": 0.8, "coverage_status": "overstock"}
```

`quantity` (champ du contrat) = **disponible** = physique − réservé. Couverture = disponible × 30 / sorties
des 30 jours, calculée sur la demande exacte et arrondie une seule fois (`avg_daily_demand` est arrondi pour
l'affichage seulement). Si `AS_OF_DATE` précède le dernier instantané, `historical: true` : stock physique
reconstruit depuis les mouvements, réservé `null` (non historisé), ruptures vues telles qu'à cette date.
`/stocks/alerts` a un modèle documenté (`StockAlerts`) ; seules les listes demandées par `kind` sont présentes. Statuts de couverture
(D-4, par priorité) : `missing_data` (aucun niveau), `stockout` (disponible ≤ 0, couverture 0),
`no_demand` (aucune sortie sur 30 j : couverture `null`), `critical` (disponible ≤ seuil d'alerte),
`overstock` (> 180 j), `ok`. L'alerte `overstock` inclut les produits `no_demand`.

## Service catalogue — port 8003

| Route | Paramètres | Réponse |
|---|---|---|
| `GET /catalog/categories` | — | catégories avec nombres de produits et de références fournisseurs |
| `GET /catalog/products` | `category`, `query`, `status`, `page` ≥ 1, `size` 1–100 | `{items[], page, size, total}`, tri par identifiant |
| `GET /catalog/products/{product_id}` | — | fiche avec `attributes` (caractéristiques) |
| `GET /catalog/search` | `category`, `query`, `max_price`, `available_only`, `supplier_id`, `limit` | `{items[], total, currency}`, tri catégorie, prix, identifiant |
| `GET /catalog/supplier-products` | `ids` (répétable) | `{items[], unknown_ids[]}` : vérification de candidats |
| `GET /catalog/supplier-products/{id}` | — | référence fournisseur ou 404 |
| `GET /catalog/candidates/{product_id}` | `available_only` (true), `limit` | références de la même catégorie (accès aux données, sans classement) |

Produit fournisseur (contrat + ajouts ; réponse réelle du seed) :

```json
{"supplier_product_id": "S104", "name": "Écouteurs Bluetooth", "category": "écouteurs", "price": 240.0,
 "cost": 143.05, "estimated_margin_rate": 0.404, "supplier_id": "F01", "supplier_name": "Atlas Distribution",
 "brand": "Pixa", "attributes": {}, "availability": "in_stock", "stock_available": 406, "lead_time_days": 10}
```

## Décisions — service catalogue

**Écritures authentifiées** (`POST`) : en-tête `Authorization: Bearer <jeton>` ; les jetons sont
configurés par `CATALOG_SERVICE_TOKENS="client:jeton,…"` (≥ 16 caractères, comparés en temps
constant). L'identité enregistrée (`recorded_by_client`) est celle du **jeton**, jamais un champ
du corps ; `decided_by` est une **déclaration** de l'appelant. Sans jeton valide : `401`.
**Clé d'idempotence obligatoire** pour tout client authentifié : en-tête `Idempotency-Key` (ou champ
`idempotency_key`), vérifiée avant toute autre validation ⇒ `400` si absente.
Mode `DECISIONS_LEGACY_ORCHESTRATOR=true` (désactivé par défaut, **non sûr**, signalé au démarrage et
par `"legacy_mode": true` dans `/health`) : accepte UNIQUEMENT la forme exacte de l'appel actuel de
l'orchestrateur `{product_id, action, replacement_id?, reason}` sans jeton ; tout autre champ
(`decision`, `decided_by`, clé, champ inconnu) ⇒ `422` ; statut forcé `approved`, décideur
`LEGACY_DECIDED_BY`, `source = "legacy"`, client `legacy-unauthenticated` ; une requête legacy
identique dans les `LEGACY_DEDUP_SECONDS` (60 s) est un rejeu (`200`), sous verrou consultatif.
Les lectures (`GET`) sont ouvertes au réseau interne.

| Route | Corps / paramètres | Réponse |
|---|---|---|
| `POST /decisions` | `{product_id, action, replacement_id?, reason, decision, decided_by?}` ; `decision` **obligatoire** (`approved`, `refused`, `pending`) ; `decided_by` obligatoire si `approved`/`refused`, interdit si `pending` ; en-tête `Idempotency-Key` **obligatoire** (`^[A-Za-z0-9._:-]{1,100}$`) | `201` créée ; `200` rejeu identique ; `400` clé absente ; `401` ; `404` produit inconnu ; `409` clé réutilisée avec des champs différents ; `422` invalide, décision implicite ou référence fournisseur inexistante ; `500`/`503` rien n'est validé |
| `POST /decisions/{id}/approve` · `/refuse` · `/cancel` | `{decided_by, note?}` + jeton (jamais en mode legacy) | `200` ; `200` + `idempotent_replay` si même cible, même décideur et même client, après une vraie transition ; `409` sinon ; `401` ; `404` |
| `GET /decisions` | `status`, `product_id`, `limit` | `{items[], total}` du plus récent au plus ancien |
| `GET /decisions/{id}` | — | décision + `events[]` (journal d'audit : `actor` déclaré, `client` authentifié) |

Idempotence : la clé est propre à chaque client authentifié ; un rejeu est accepté seulement si
`product_id`, `action`, `replacement_id`, `reason`, `decision` et `decided_by` sont identiques
(empreinte SHA-256), sinon `409`. Requêtes simultanées avec la même clé : une seule décision.

Transactions : décision et événement d'audit sont écrits dans une même transaction, validée
**avant** l'envoi de la réponse de succès ; en cas d'échec (y compris au COMMIT), rien n'est enregistré.

Réponse de `POST /decisions` (contrat : `status: "recorded"` ; forme testée) :

```json
{"decision_id": 3, "product_id": "P005", "action": "replace", "replacement_id": "S104",
 "reason": "Ventes en forte baisse et stock élevé", "status": "recorded",
 "decision_status": "approved", "source": "orchestrator", "decided_by": "manal",
 "recorded_by_client": "orchestrator", "decided_at": "2026-11-21T19:58:12Z",
 "idempotency_key": "a2b5000f", "created_at": "2026-11-21T19:58:12Z", "idempotent_replay": false}
```

Cycle de vie : `pending → approved | refused | cancelled` ; les états finaux sont immuables.
`replacement_id` est obligatoire pour `replace` et interdit sinon. Aucun produit n'est modifié.
