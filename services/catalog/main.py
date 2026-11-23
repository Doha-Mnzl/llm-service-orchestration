"""Service catalogue (Partie 3) : produits du magasin, catalogue fournisseurs, décisions humaines.

Lancer depuis la racine du dépôt :
    uvicorn services.catalog.main:app --port 8003
Documentation interactive : http://127.0.0.1:8003/docs
"""

import logging
import unicodedata
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.catalog import decisions
from services.catalog.config import settings
from services.catalog.db import EXCEPTION_HANDLERS, database_up, get_conn

log = logging.getLogger("services.catalog")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.legacy_orchestrator:
        log.warning("DECISIONS_LEGACY_ORCHESTRATOR=true : écritures de décisions NON authentifiées acceptées "
                    "(mode de compatibilité NON SÛR, réservé à la démonstration)")
    if not settings.service_tokens:
        log.warning("CATALOG_SERVICE_TOKENS vide : aucune écriture authentifiée possible")
    yield


app = FastAPI(title="Service catalogue", version="1.0.0", lifespan=lifespan,
              description="Produits du magasin, catalogue fournisseurs (200 candidats) et décisions validées.")
for _exc, _handler in EXCEPTION_HANDLERS:
    app.add_exception_handler(_exc, _handler)
app.include_router(decisions.router)

PRODUCT_ID, SUPPLIER_PRODUCT_ID = r"^P\d{3,5}$", r"^S\d{3,5}$"
Conn = Annotated[psycopg.Connection, Depends(get_conn, scope="function")]
_FROM = "àâäáãéèêëîïíìôöóòõùûüúçñÀÂÄÁÃÉÈÊËÎÏÍÌÔÖÓÒÕÙÛÜÚÇÑ"
_TO = "aaaaaeeeeiiiiooooouuuucnaaaaaeeeeiiiiooooouuuucn"


def _norm(text: str | None) -> str | None:
    if text is None:
        return None
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def _sql_norm(col: str) -> str:
    return f"lower(translate({col}, '{_FROM}', '{_TO}'))"


# ---------- schémas ----------

class Category(BaseModel):
    name: str
    label: str
    product_count: int
    supplier_product_count: int


class Product(BaseModel):
    product_id: str
    name: str
    category: str
    brand: str
    attributes: dict[str, Any]
    unit_price: float
    unit_cost: float
    status: str
    created_on: date


class ProductPage(BaseModel):
    items: list[Product]
    page: int
    size: int
    total: int


class SupplierProduct(BaseModel):
    supplier_product_id: str
    name: str
    category: str
    price: float                 # prix de vente estimé (champ du contrat docs/api.md)
    cost: float                  # prix d'achat fournisseur
    estimated_margin_rate: float | None
    supplier_id: str
    supplier_name: str
    brand: str
    attributes: dict[str, Any]
    availability: Literal["in_stock", "limited", "out_of_stock"]
    stock_available: int
    lead_time_days: int


class SupplierSearch(BaseModel):
    items: list[SupplierProduct]
    total: int
    currency: str


class SupplierLookup(BaseModel):
    items: list[SupplierProduct]
    unknown_ids: list[str]


class Candidates(BaseModel):
    product_id: str
    category: str
    candidates: list[SupplierProduct]


# ---------- requêtes ----------

SUPPLIER_SELECT = """
    SELECT sp.supplier_product_id, sp.name, sp.category, sp.price::float AS price, sp.cost::float AS cost,
           CASE WHEN sp.price > 0 THEN round((sp.price - sp.cost) / sp.price, 4)::float END AS estimated_margin_rate,
           sp.supplier_id, s.name AS supplier_name, sp.brand, sp.attributes, sp.availability,
           sp.stock_available, sp.lead_time_days
    FROM catalog.supplier_products sp JOIN catalog.suppliers s USING (supplier_id)"""


def _product_category(conn, product_id: str) -> str:
    row = conn.execute("SELECT category FROM catalog.products WHERE product_id = %s", (product_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Produit inconnu : {product_id}")
    return row["category"]


# ---------- routes ----------

@app.get("/health")
def health():
    mode = {"legacy_mode": settings.legacy_orchestrator}
    if database_up():
        return {"status": "ok", "service": "catalog", "database": "up", **mode}
    return JSONResponse(status_code=503, content={"status": "degraded", "service": "catalog", "database": "down",
                                                  **mode})


@app.get("/catalog/categories", response_model=list[Category])
def categories(conn: Conn):
    return conn.execute("""
        SELECT c.name, c.label,
               (SELECT count(*) FROM catalog.products p WHERE p.category = c.name) AS product_count,
               (SELECT count(*) FROM catalog.supplier_products sp WHERE sp.category = c.name) AS supplier_product_count
        FROM catalog.categories c ORDER BY c.name""").fetchall()


@app.get("/catalog/products", response_model=ProductPage)
def products(conn: Conn, category: Annotated[str | None, Query(max_length=50)] = None,
             query: Annotated[str | None, Query(max_length=100)] = None,
             status: Literal["active", "discontinued"] | None = None,
             page: Annotated[int, Query(ge=1)] = 1, size: Annotated[int, Query(ge=1, le=100)] = 20):
    """Produits du magasin, filtrés (catégorie, mots-clés, statut) et triés par identifiant."""
    where = f"""WHERE (%(cat)s::text IS NULL OR {_sql_norm('category')} = %(cat)s::text)
                  AND (%(q)s::text IS NULL OR {_sql_norm("name || ' ' || brand")} LIKE '%%' || %(q)s::text || '%%')
                  AND (%(st)s::text IS NULL OR status = %(st)s::text)"""
    params = {"cat": _norm(category), "q": _norm(query), "st": status, "lim": size, "off": (page - 1) * size}
    total = conn.execute(f"SELECT count(*) AS n FROM catalog.products {where}", params).fetchone()["n"]
    rows = conn.execute(f"""SELECT product_id, name, category, brand, attributes, unit_price::float AS unit_price,
                                   unit_cost::float AS unit_cost, status, created_on
                            FROM catalog.products {where} ORDER BY product_id LIMIT %(lim)s OFFSET %(off)s""",
                        params).fetchall()
    return {"items": rows, "page": page, "size": size, "total": total}


@app.get("/catalog/products/{product_id}", response_model=Product)
def product(conn: Conn, product_id: Annotated[str, Path(pattern=PRODUCT_ID)]):
    row = conn.execute("""SELECT product_id, name, category, brand, attributes, unit_price::float AS unit_price,
                                 unit_cost::float AS unit_cost, status, created_on
                          FROM catalog.products WHERE product_id = %s""", (product_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Produit inconnu : {product_id}")
    return row


@app.get("/catalog/search", response_model=SupplierSearch)
def supplier_search(conn: Conn, category: Annotated[str | None, Query(max_length=50)] = None,
                    query: Annotated[str | None, Query(max_length=100)] = None,
                    max_price: Annotated[float | None, Query(gt=0)] = None,
                    available_only: bool = False,
                    supplier_id: Annotated[str | None, Query(pattern=r"^F\d{2,4}$")] = None,
                    limit: Annotated[int, Query(ge=1, le=200)] = 50):
    """Recherche dans le catalogue fournisseurs (tri : catégorie, prix, identifiant)."""
    params = {"cat": _norm(category), "q": _norm(query), "mp": max_price, "av": available_only,
              "sup": supplier_id, "lim": limit}
    where = f"""WHERE (%(cat)s::text IS NULL OR {_sql_norm('sp.category')} = %(cat)s::text)
                  AND (%(q)s::text IS NULL OR {_sql_norm("sp.name || ' ' || sp.brand")} LIKE '%%' || %(q)s::text || '%%')
                  AND (%(mp)s::numeric IS NULL OR sp.price <= %(mp)s::numeric)
                  AND (NOT %(av)s::boolean OR sp.availability <> 'out_of_stock')
                  AND (%(sup)s::text IS NULL OR sp.supplier_id = %(sup)s::text)"""
    total = conn.execute(f"SELECT count(*) AS n FROM catalog.supplier_products sp {where}", params).fetchone()["n"]
    rows = conn.execute(f"{SUPPLIER_SELECT} {where} ORDER BY sp.category, sp.price, sp.supplier_product_id "
                        "LIMIT %(lim)s", params).fetchall()
    return {"items": rows, "total": total, "currency": settings.currency}


@app.get("/catalog/supplier-products", response_model=SupplierLookup)
def supplier_lookup(conn: Conn, ids: Annotated[list[str], Query(min_length=1, max_length=100)]):
    """Vérifie une liste de références fournisseurs : celles qui existent et celles qui sont inconnues."""
    rows = conn.execute(f"{SUPPLIER_SELECT} WHERE sp.supplier_product_id = ANY(%s) ORDER BY 1", (ids,)).fetchall()
    found = {r["supplier_product_id"] for r in rows}
    return {"items": rows, "unknown_ids": [i for i in ids if i not in found]}


@app.get("/catalog/supplier-products/{supplier_product_id}", response_model=SupplierProduct)
def supplier_product(conn: Conn, supplier_product_id: Annotated[str, Path(pattern=SUPPLIER_PRODUCT_ID)]):
    row = conn.execute(f"{SUPPLIER_SELECT} WHERE sp.supplier_product_id = %s", (supplier_product_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Référence fournisseur inconnue : {supplier_product_id}")
    return row


@app.get("/catalog/candidates/{product_id}", response_model=Candidates)
def candidates(conn: Conn, product_id: Annotated[str, Path(pattern=PRODUCT_ID)],
               available_only: bool = True, limit: Annotated[int, Query(ge=1, le=50)] = 10):
    """Références fournisseurs de la même catégorie qu'un produit du magasin (accès aux données,
    sans scoring : le classement des remplacements reste la responsabilité d'Analysis)."""
    category = _product_category(conn, product_id)
    rows = conn.execute(f"""{SUPPLIER_SELECT} WHERE sp.category = %s
                            AND (NOT %s OR sp.availability <> 'out_of_stock')
                            ORDER BY sp.price, sp.supplier_product_id LIMIT %s""",
                        (category, available_only, limit)).fetchall()
    return {"product_id": product_id, "category": category, "candidates": rows}
