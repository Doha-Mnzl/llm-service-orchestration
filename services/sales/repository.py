"""Requêtes SQL du service ventes (schéma `sales` uniquement, requêtes paramétrées)."""

from datetime import date
from decimal import Decimal

import psycopg

from services.sales.metrics import normalize, sql_normalized

Window = tuple[date, date]


def resolve_as_of(conn: psycopg.Connection, configured: str | None) -> date:
    if configured:
        return date.fromisoformat(configured)
    row = conn.execute("SELECT max(sold_on) AS d FROM sales.sale_lines").fetchone()
    return row["d"] or date.today()


def product_exists(conn: psycopg.Connection, product_id: str) -> bool:
    return conn.execute("SELECT 1 FROM sales.product_ref WHERE product_id = %s", (product_id,)).fetchone() is not None


def aggregate(conn: psycopg.Connection, windows: list[Window], category: str | None = None,
              product_id: str | None = None) -> list[dict]:
    """Unités, CA et marge par produit pour chaque fenêtre de dates (bornes incluses).

    Renvoie, pour chaque produit de la référence (même sans vente) :
    {product_id, name, category, windows: [{units, revenue, margin}, ...]}.
    """
    cols, params = [], {"cat": normalize(category) if category else None, "pid": product_id,
                        "lo": min(s for s, _ in windows), "hi": max(e for _, e in windows)}
    for i, (start, end) in enumerate(windows):
        params[f"s{i}"], params[f"e{i}"] = start, end
        f = f"FILTER (WHERE l.sold_on BETWEEN %(s{i})s AND %(e{i})s)"
        cols += [f"coalesce(sum(l.quantity) {f}, 0) AS u{i}",
                 f"coalesce(sum(l.quantity * l.unit_price) {f}, 0) AS r{i}",
                 f"coalesce(sum(l.quantity * (l.unit_price - l.unit_cost)) {f}, 0) AS m{i}"]
    sql = f"""
        SELECT p.product_id, p.name, p.category, {', '.join(cols)}
        FROM sales.product_ref p
        LEFT JOIN sales.sale_lines l
               ON l.product_id = p.product_id AND l.sold_on BETWEEN %(lo)s AND %(hi)s
        WHERE (%(cat)s::text IS NULL OR {sql_normalized('p.category')} = %(cat)s::text)
          AND (%(pid)s::text IS NULL OR p.product_id = %(pid)s::text)
        GROUP BY p.product_id, p.name, p.category
        ORDER BY p.product_id"""
    out = []
    for row in conn.execute(sql, params):
        out.append({"product_id": row["product_id"], "name": row["name"], "category": row["category"],
                    "windows": [{"units": int(row[f"u{i}"]), "revenue": _money(row[f"r{i}"]),
                                 "margin": _money(row[f"m{i}"])} for i in range(len(windows))]})
    return out


def _money(value: Decimal) -> float:
    return float(round(Decimal(value), 2))
