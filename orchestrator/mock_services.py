"""Faux services (ventes, stocks, catalogue, analyse) pour développer sans attendre
les parties 1 et 3. Activés par USE_MOCK_SERVICES=true.

Les réponses suivent le format proposé dans docs/api.md : quand les vrais services
seront prêts, il suffit de passer USE_MOCK_SERVICES=false.
"""

PRODUCTS = {
    "P001": {"name": "Câble USB-C 1 m", "category": "câbles"},
    "P002": {"name": "Chargeur 20 W", "category": "chargeurs"},
    "P003": {"name": "Souris filaire basique", "category": "souris"},
    "P004": {"name": "Clavier AZERTY USB", "category": "claviers"},
    "P005": {"name": "Écouteurs filaires jack", "category": "écouteurs"},
    "P006": {"name": "Câble micro-USB 2 m", "category": "câbles"},
}

SUPPLIER = [
    {"supplier_product_id": "S101", "name": "Câble USB-C tressé 2 m", "category": "câbles", "price": 6.9},
    {"supplier_product_id": "S102", "name": "Chargeur GaN 35 W", "category": "chargeurs", "price": 19.5},
    {"supplier_product_id": "S103", "name": "Souris sans fil silencieuse", "category": "souris", "price": 12.0},
    {"supplier_product_id": "S104", "name": "Écouteurs Bluetooth", "category": "écouteurs", "price": 24.0},
]

SALES = {  # unités vendues sur le dernier mois, CA, marge, évolution sur 6 mois
    "P001": (420, 2940.0, 0.38, +0.12),
    "P002": (310, 4650.0, 0.31, +0.05),
    "P003": (18, 126.0, 0.12, -0.64),
    "P004": (95, 2375.0, 0.27, -0.08),
    "P005": (12, 96.0, 0.10, -0.71),
    "P006": (25, 125.0, 0.15, -0.55),
}

STOCKS = {"P001": (500, 36), "P002": (120, 12), "P003": (340, 566),
          "P004": (60, 19), "P005": (280, 700), "P006": (0, 0)}


def _product(pid: str) -> dict:
    return {"product_id": pid, **PRODUCTS.get(pid, {"name": "Inconnu", "category": "?"})}


def get_sales_summary(period_months: int = 1, category: str | None = None) -> dict:
    items = []
    for pid, (units, revenue, margin, trend) in SALES.items():
        p = _product(pid)
        if category and p["category"] != category:
            continue
        items.append({**p, "units_sold": units * period_months, "revenue": revenue * period_months,
                      "margin_rate": margin, "trend_6m": trend})
    return {"period_months": period_months, "items": items}


def get_sales_trend(product_id: str, months: int = 6) -> dict:
    units = SALES.get(product_id, (0, 0, 0, 0))[0]
    return {"product_id": product_id,
            "monthly_units": [round(units * (1 + 0.1 * (months - i))) for i in range(months)]}


def get_stock_levels(product_ids: list[str] | None = None, category: str | None = None) -> dict:
    ids = product_ids or list(STOCKS)
    items = []
    for pid in ids:
        if pid not in STOCKS:
            continue
        p = _product(pid)
        if category and p["category"] != category:
            continue
        qty, days = STOCKS[pid]
        items.append({**p, "quantity": qty, "coverage_days": days})
    return {"items": items}


def get_stock_alerts(kind: str = "all") -> dict:
    over = [{**_product(p), "quantity": q, "coverage_days": d} for p, (q, d) in STOCKS.items() if d > 180]
    out = [{**_product(p), "quantity": q} for p, (q, d) in STOCKS.items() if q == 0]
    res = {}
    if kind in ("overstock", "all"):
        res["overstock"] = over
    if kind in ("stockout", "all"):
        res["stockout"] = out
    return res


def find_underperforming_products(period_months: int = 1, top_n: int = 5) -> dict:
    ranked = sorted(SALES.items(), key=lambda kv: kv[1][3])  # pire évolution d'abord
    items = [{**_product(pid), "trend_6m": t, "margin_rate": m, "score": round(-t * (1 - m), 2)}
             for pid, (_, _, m, t) in ranked if t < 0][:top_n]
    return {"period_months": period_months, "items": items}


def find_replacements(product_id: str, max_results: int = 3) -> dict:
    cat = PRODUCTS.get(product_id, {}).get("category")
    items = [s for s in SUPPLIER if s["category"] == cat][:max_results]
    return {"product_id": product_id, "candidates": items}


def search_supplier_catalog(category: str, query: str | None = None, max_price: float | None = None) -> dict:
    items = [s for s in SUPPLIER if s["category"] == category
             and (max_price is None or s["price"] <= max_price)
             and (query is None or query.lower() in s["name"].lower())]
    return {"items": items}


_decisions: list[dict] = []


def record_decision(product_id: str, action: str, reason: str, replacement_id: str | None = None) -> dict:
    d = {"decision_id": len(_decisions) + 1, "product_id": product_id, "action": action,
         "replacement_id": replacement_id, "reason": reason, "status": "recorded"}
    _decisions.append(d)
    return d


HANDLERS = {
    "get_sales_summary": get_sales_summary,
    "get_sales_trend": get_sales_trend,
    "get_stock_levels": get_stock_levels,
    "get_stock_alerts": get_stock_alerts,
    "find_underperforming_products": find_underperforming_products,
    "find_replacements": find_replacements,
    "search_supplier_catalog": search_supplier_catalog,
    "record_decision": record_decision,
}
