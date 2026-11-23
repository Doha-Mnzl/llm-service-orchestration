"""Service stocks (Partie 3) : stock physique/réservé/disponible, couverture, alertes, ruptures.

Lancer depuis la racine du dépôt :
    uvicorn services.inventory.main:app --port 8002
Documentation interactive : http://127.0.0.1:8002/docs
"""

import unicodedata
from datetime import date, timedelta
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.inventory import metrics
from services.inventory.config import settings
from services.inventory.db import EXCEPTION_HANDLERS, database_up, get_conn

app = FastAPI(title="Service stocks", version="1.0.0",
              description="Stock physique, réservé et disponible, jours de couverture, alertes et ruptures.")
for _exc, _handler in EXCEPTION_HANDLERS:
    app.add_exception_handler(_exc, _handler)

PRODUCT_ID = r"^P\d{3,5}$"
Conn = Annotated[psycopg.Connection, Depends(get_conn, scope="function")]
_FROM = "àâäáãéèêëîïíìôöóòõùûüúçñÀÂÄÁÃÉÈÊËÎÏÍÌÔÖÓÒÕÙÛÜÚÇÑ"
_TO = "aaaaaeeeeiiiiooooouuuucnaaaaaeeeeiiiiooooouuuucn"


def _norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


# ---------- schémas de réponse ----------

class StockItem(BaseModel):
    product_id: str
    name: str
    category: str
    quantity: int | None   # = disponible (champ du contrat docs/api.md)
    on_hand: int | None
    reserved: int | None
    available: int | None
    reorder_point: int
    avg_daily_demand: float
    coverage_days: int | None
    coverage_status: Literal["missing_data", "stockout", "no_demand", "critical", "overstock", "ok"]


class StockList(BaseModel):
    as_of: date
    historical: bool = Field(description="Vrai si as_of précède le dernier instantané : stock reconstruit depuis "
                                         "les mouvements, quantité réservée non historisée (null)")
    items: list[StockItem]
    unknown_product_ids: list[str]


class OpenStockout(BaseModel):
    started_on: date
    days_out: int


class StockDetail(StockItem):
    as_of: date
    historical: bool
    open_stockout: OpenStockout | None


class OverstockAlert(BaseModel):
    product_id: str
    name: str
    category: str
    quantity: int | None
    coverage_days: int | None
    coverage_status: Literal["overstock", "no_demand"]


class StockoutAlert(BaseModel):
    product_id: str
    name: str
    category: str
    quantity: int | None
    since: date | None
    days_out: int | None


class CriticalAlert(BaseModel):
    product_id: str
    name: str
    category: str
    quantity: int | None
    reorder_point: int
    coverage_days: int | None


class StockAlerts(BaseModel):
    as_of: date
    historical: bool
    overstock: list[OverstockAlert] | None = None
    stockout: list[StockoutAlert] | None = None
    critical: list[CriticalAlert] | None = None


class Movement(BaseModel):
    moved_on: date
    kind: str
    quantity: int


class Movements(BaseModel):
    product_id: str
    items: list[Movement]
    total_in: int
    total_out: int
    net: int


class Stockout(BaseModel):
    product_id: str
    name: str
    started_on: date
    ended_on: date | None
    duration_days: int
    open: bool


class Stockouts(BaseModel):
    as_of: date
    items: list[Stockout]


# ---------- accès aux données ----------

def _as_of(conn) -> date:
    if settings.as_of:
        return date.fromisoformat(settings.as_of)
    return conn.execute("SELECT max(updated_on) AS d FROM inventory.stock_levels").fetchone()["d"] or date.today()


def _snapshot(conn) -> date | None:
    return conn.execute("SELECT max(updated_on) AS d FROM inventory.stock_levels").fetchone()["d"]


def _historical(conn, as_of: date) -> bool:
    snap = _snapshot(conn)
    return snap is not None and as_of < snap


def _levels(conn, as_of: date, product_ids: list[str] | None = None, category: str | None = None) -> list[dict]:
    """Niveaux à la date as_of. Avant le dernier instantané, le stock physique est reconstruit depuis les
    mouvements (Σ jusqu'à as_of) et la quantité réservée, non historisée, vaut null (revue Codex)."""
    window_start = as_of - timedelta(days=settings.demand_window_days - 1)
    hist = _historical(conn, as_of)
    stock_cols = ("l.on_hand, l.reserved, l.available" if not hist else
                  """CASE WHEN l.product_id IS NULL THEN NULL ELSE coalesce((SELECT sum(h.quantity)
                         FROM inventory.stock_movements h WHERE h.product_id = r.product_id
                         AND h.moved_on <= %(as_of)s), 0) END AS on_hand,
                     NULL::int AS reserved,
                     CASE WHEN coalesce((SELECT sum(h.quantity) FROM inventory.stock_movements h
                         WHERE h.product_id = r.product_id AND h.moved_on <= %(as_of)s), 0) = 0
                         AND l.product_id IS NOT NULL THEN 0 END AS available""")
    # disponible = physique − réservé : réservé non historisé ⇒ disponible inconnu (null), sauf physique nul (0).
    rows = conn.execute(f"""
        SELECT r.product_id, r.name, r.category, r.reorder_point, {stock_cols},
               coalesce((SELECT -sum(m.quantity) FROM inventory.stock_movements m
                         WHERE m.product_id = r.product_id AND m.kind = 'sale'
                           AND m.moved_on BETWEEN %(ws)s AND %(as_of)s), 0) AS units_out
        FROM inventory.product_ref r
        LEFT JOIN inventory.stock_levels l USING (product_id)
        WHERE (%(ids)s::text[] IS NULL OR r.product_id = ANY(%(ids)s::text[]))
          AND (%(cat)s::text IS NULL OR lower(translate(r.category, '{_FROM}', '{_TO}')) = %(cat)s::text)
        ORDER BY r.product_id""",
        {"ws": window_start, "as_of": as_of, "ids": product_ids, "cat": _norm(category) if category else None})
    out = []
    for r in rows:
        units_out = int(r["units_out"])
        demand = metrics.average_daily_demand(units_out, settings.demand_window_days)  # affichage seulement
        days, status = metrics.coverage(r["available"], units_out, settings.demand_window_days,
                                        r["reorder_point"], settings.overstock_days)
        out.append({"product_id": r["product_id"], "name": r["name"], "category": r["category"],
                    "quantity": r["available"], "on_hand": r["on_hand"], "reserved": r["reserved"],
                    "available": r["available"], "reorder_point": r["reorder_point"],
                    "avg_daily_demand": demand, "coverage_days": days, "coverage_status": status})
    return out


def _require(conn, product_id: str) -> None:
    if not conn.execute("SELECT 1 FROM inventory.product_ref WHERE product_id = %s", (product_id,)).fetchone():
        raise HTTPException(404, f"Produit inconnu : {product_id}")


def _open_stockouts(conn, as_of: date) -> dict[str, date]:
    """Ruptures ouvertes À LA DATE as_of (commencées avant, pas encore terminées à cette date)."""
    return {r["product_id"]: r["started_on"] for r in conn.execute(
        "SELECT product_id, started_on FROM inventory.stockouts WHERE started_on <= %(d)s "
        "AND (ended_on IS NULL OR ended_on > %(d)s)", {"d": as_of})}


# ---------- routes ----------

@app.get("/health")
def health():
    if database_up():
        return {"status": "ok", "service": "inventory", "database": "up"}
    return JSONResponse(status_code=503, content={"status": "degraded", "service": "inventory", "database": "down"})


@app.get("/stocks", response_model=StockList)
def stock_levels(conn: Conn,
                 product_ids: Annotated[list[str] | None, Query(max_length=50)] = None,
                 category: Annotated[str | None, Query(max_length=50)] = None):
    """Niveaux de stock et couverture. Les identifiants inconnus sont listés dans `unknown_product_ids`."""
    as_of = _as_of(conn)
    items = _levels(conn, as_of, product_ids, category)
    known = {r["product_id"] for r in conn.execute(
        "SELECT product_id FROM inventory.product_ref WHERE product_id = ANY(%s)", (product_ids or [],))}
    unknown = [p for p in product_ids or [] if p not in known]
    return {"as_of": as_of, "historical": _historical(conn, as_of), "items": items, "unknown_product_ids": unknown}


@app.get("/stocks/alerts", response_model=StockAlerts, response_model_exclude_unset=True)
def stock_alerts(conn: Conn, kind: Literal["overstock", "stockout", "critical", "all"] = "all"):
    """Alertes : surstock (y compris demande nulle), rupture, stock critique."""
    as_of = _as_of(conn)
    items = _levels(conn, as_of)
    res: dict = {"as_of": as_of, "historical": _historical(conn, as_of)}
    if kind in ("overstock", "all"):
        res["overstock"] = [{k: i[k] for k in ("product_id", "name", "category", "quantity", "coverage_days",
                                                "coverage_status")}
                            for i in items if i["coverage_status"] in ("overstock", "no_demand")]
    if kind in ("stockout", "all"):
        opened = _open_stockouts(conn, as_of)
        res["stockout"] = [{"product_id": i["product_id"], "name": i["name"], "category": i["category"],
                            "quantity": i["quantity"], "since": opened.get(i["product_id"]),
                            "days_out": (as_of - opened[i["product_id"]]).days if i["product_id"] in opened else None}
                           for i in items if i["coverage_status"] == "stockout"]
    if kind in ("critical", "all"):
        res["critical"] = [{k: i[k] for k in ("product_id", "name", "category", "quantity", "reorder_point",
                                               "coverage_days")}
                           for i in items if i["coverage_status"] == "critical"]
    return res


@app.get("/stocks/{product_id}", response_model=StockDetail)
def stock_detail(conn: Conn, product_id: Annotated[str, Path(pattern=PRODUCT_ID)]):
    _require(conn, product_id)
    as_of = _as_of(conn)
    item = _levels(conn, as_of, [product_id])[0]
    started = _open_stockouts(conn, as_of).get(product_id)
    return {**item, "as_of": as_of, "historical": _historical(conn, as_of),
            "open_stockout": {"started_on": started, "days_out": (as_of - started).days} if started else None}


@app.get("/stocks/{product_id}/movements", response_model=Movements)
def stock_movements(conn: Conn, product_id: Annotated[str, Path(pattern=PRODUCT_ID)],
                    kind: Literal["initial", "receipt", "sale", "return", "adjustment"] | None = None,
                    start: date | None = None, end: date | None = None,
                    limit: Annotated[int, Query(ge=1, le=5000)] = 1000):
    """Mouvements de stock (signés). Sans filtre, `net` = stock physique."""
    _require(conn, product_id)
    rows = conn.execute("""
        SELECT moved_on, kind, quantity FROM inventory.stock_movements
        WHERE product_id = %(p)s AND (%(k)s::text IS NULL OR kind = %(k)s::text)
          AND (%(s)s::date IS NULL OR moved_on >= %(s)s::date) AND (%(e)s::date IS NULL OR moved_on <= %(e)s::date)
        ORDER BY moved_on DESC, movement_id DESC""", {"p": product_id, "k": kind, "s": start, "e": end}).fetchall()
    total_in = sum(r["quantity"] for r in rows if r["quantity"] > 0)
    total_out = -sum(r["quantity"] for r in rows if r["quantity"] < 0)
    return {"product_id": product_id, "items": rows[:limit], "total_in": total_in, "total_out": total_out,
            "net": total_in - total_out}


@app.get("/stockouts", response_model=Stockouts)
def stockouts(conn: Conn, product_id: Annotated[str | None, Query(pattern=PRODUCT_ID)] = None,
              open: bool | None = None):
    """Historique des ruptures (en cours et terminées)."""
    as_of = _as_of(conn)
    # Vue à la date as_of : ruptures commencées avant cette date ; une fin postérieure n'est pas encore connue.
    rows = conn.execute("""
        SELECT s.product_id, r.name, s.started_on,
               CASE WHEN s.ended_on <= %(d)s THEN s.ended_on END AS ended_on
        FROM inventory.stockouts s JOIN inventory.product_ref r USING (product_id)
        WHERE s.started_on <= %(d)s AND (%(p)s::text IS NULL OR s.product_id = %(p)s::text)
        ORDER BY s.started_on DESC, s.product_id""", {"p": product_id, "d": as_of}).fetchall()
    items = [{**r, "duration_days": ((r["ended_on"] or as_of) - r["started_on"]).days,
              "open": r["ended_on"] is None} for r in rows]
    if open is not None:
        items = [i for i in items if i["open"] == open]
    return {"as_of": as_of, "items": items}
