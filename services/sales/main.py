"""Service ventes (Partie 3) : ventes, chiffre d'affaires, marge et évolution.

Lancer depuis la racine du dépôt :
    uvicorn services.sales.main:app --port 8001
Documentation interactive : http://127.0.0.1:8001/docs
"""

from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse

from services.sales import metrics, repository
from services.sales.config import settings
from services.sales.db import EXCEPTION_HANDLERS, database_up, get_conn
from services.sales.schemas import (Health, MonthlyTotals, ProductSales, SalesSummary, SalesTrend)

app = FastAPI(title="Service ventes", version="1.0.0",
              description="Ventes, chiffre d'affaires, marge brute et évolutions par produit et période.")
for _exc, _handler in EXCEPTION_HANDLERS:
    app.add_exception_handler(_exc, _handler)

PRODUCT_ID = r"^P\d{3,5}$"
Conn = Annotated[psycopg.Connection, Depends(get_conn, scope="function")]
Category = Annotated[str | None, Query(max_length=50, description="Insensible à la casse et aux accents")]

SORT_KEYS = {"product_id": lambda i: i["product_id"], "revenue": lambda i: -i["revenue"],
             "units": lambda i: -i["units_sold"], "trend": lambda i: (i["trend_6m"] is None, i["trend_6m"] or 0),
             "margin_rate": lambda i: (i["margin_rate"] is None, i["margin_rate"] or 0)}


@app.get("/health", response_model=Health)
def health():
    if database_up():
        return {"status": "ok", "service": "sales", "database": "up"}
    return JSONResponse(status_code=503, content={"status": "degraded", "service": "sales", "database": "down"})


def _require_product(conn, product_id: str | None) -> None:
    if product_id and not repository.product_exists(conn, product_id):
        raise HTTPException(404, f"Produit inconnu : {product_id}")


def _stats(w: dict, bounds) -> dict:
    return {"start": bounds[0], "end": bounds[1], "units": w["units"], "revenue": w["revenue"],
            "gross_margin": w["margin"], "margin_rate": metrics.ratio(w["margin"], w["revenue"])}


@app.get("/sales/summary", response_model=SalesSummary)
def sales_summary(conn: Conn,
                  period_months: Annotated[int, Query(ge=1, le=24)] = 1,
                  category: Category = None,
                  product_id: Annotated[str | None, Query(pattern=PRODUCT_ID)] = None,
                  sort: Literal["product_id", "revenue", "units", "trend", "margin_rate"] = "product_id",
                  limit: Annotated[int | None, Query(ge=1, le=500)] = None):
    """Ventes, CA, marge et tendances par produit sur les `period_months` derniers mois."""
    _require_product(conn, product_id)
    as_of = repository.resolve_as_of(conn, settings.as_of)
    current = metrics.period_bounds(as_of, period_months)
    previous = metrics.previous_period_bounds(as_of, period_months)
    trend_windows = metrics.monthly_windows(as_of, 6)
    rows = repository.aggregate(conn, [current, previous, *trend_windows], category, product_id)
    items = []
    for r in rows:
        cur, prev = r["windows"][0], r["windows"][1]
        items.append({"product_id": r["product_id"], "name": r["name"], "category": r["category"],
                      "units_sold": cur["units"], "revenue": cur["revenue"], "gross_margin": cur["margin"],
                      "margin_rate": metrics.ratio(cur["margin"], cur["revenue"]),
                      "trend_6m": metrics.trend([w["units"] for w in r["windows"][2:]]),
                      "previous_revenue": prev["revenue"],
                      "revenue_change": metrics.change(cur["revenue"], prev["revenue"])})
    items.sort(key=SORT_KEYS[sort])
    return {"period_months": period_months, "as_of": as_of, "start": current[0], "end": current[1],
            "previous_start": previous[0], "previous_end": previous[1],
            "currency": settings.currency, "items": items[:limit] if limit else items}


@app.get("/sales/trend/{product_id}", response_model=SalesTrend)
def sales_trend(conn: Conn, product_id: Annotated[str, Path(pattern=PRODUCT_ID)],
                months: Annotated[int, Query(ge=1, le=24)] = 6):
    """Évolution mensuelle des ventes d'un produit (mois glissants, du plus ancien au plus récent)."""
    _require_product(conn, product_id)
    windows = metrics.monthly_windows(repository.resolve_as_of(conn, settings.as_of), months)
    row = repository.aggregate(conn, windows, product_id=product_id)[0]
    return {"product_id": product_id, "months": months,
            "monthly_units": [w["units"] for w in row["windows"]],
            "monthly_revenue": [w["revenue"] for w in row["windows"]],
            "periods": [{"start": s, "end": e} for s, e in windows], "currency": settings.currency}


@app.get("/sales/products/{product_id}", response_model=ProductSales)
def product_sales(conn: Conn, product_id: Annotated[str, Path(pattern=PRODUCT_ID)],
                  period_months: Annotated[int, Query(ge=1, le=24)] = 1):
    """Détail d'un produit : période courante, période précédente de même durée et évolutions."""
    _require_product(conn, product_id)
    as_of = repository.resolve_as_of(conn, settings.as_of)
    current = metrics.period_bounds(as_of, period_months)
    previous = metrics.previous_period_bounds(as_of, period_months)
    row = repository.aggregate(conn, [current, previous, *metrics.monthly_windows(as_of, 6)],
                               product_id=product_id)[0]
    cur, prev = row["windows"][0], row["windows"][1]
    return {"product_id": product_id, "name": row["name"], "category": row["category"],
            "period_months": period_months, "current": _stats(cur, current), "previous": _stats(prev, previous),
            "changes": {"units": metrics.change(cur["units"], prev["units"]),
                        "revenue": metrics.change(cur["revenue"], prev["revenue"]),
                        "gross_margin": metrics.change(cur["margin"], prev["margin"])},
            "trend_6m": metrics.trend([w["units"] for w in row["windows"][2:]]), "currency": settings.currency}


@app.get("/sales/monthly", response_model=MonthlyTotals)
def monthly_totals(conn: Conn, months: Annotated[int, Query(ge=1, le=24)] = 12, category: Category = None,
                   product_id: Annotated[str | None, Query(pattern=PRODUCT_ID)] = None):
    """Totaux mensuels (unités, CA, marge) pour les tableaux de bord."""
    _require_product(conn, product_id)
    windows = metrics.monthly_windows(repository.resolve_as_of(conn, settings.as_of), months)
    rows = repository.aggregate(conn, windows, category, product_id)
    items = []
    for i, (start, end) in enumerate(windows):
        items.append({"month": end.strftime("%Y-%m"), "start": start, "end": end,
                      "units": sum(r["windows"][i]["units"] for r in rows),
                      "revenue": round(sum(r["windows"][i]["revenue"] for r in rows), 2),
                      "gross_margin": round(sum(r["windows"][i]["margin"] for r in rows), 2)})
    return {"currency": settings.currency, "category": category, "product_id": product_id, "items": items}
