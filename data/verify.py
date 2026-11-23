"""Vérifications d'intégrité et empreinte de reproductibilité du jeu de données.

    python -m data.verify            # code de sortie 1 si une anomalie est trouvée
"""

import argparse
import hashlib
import os
import sys

import psycopg

from data import migrate

CHECKS = {
    "au moins 50 produits": "SELECT count(*) < 50 FROM catalog.products",
    "exactement 200 produits fournisseurs":
        "SELECT count(*) <> 200 FROM catalog.supplier_products",
    "références produit identiques dans les 3 schémas":
        """SELECT count(*) > 0 FROM (
             (SELECT product_id, name, category FROM catalog.products
              EXCEPT SELECT product_id, name, category FROM sales.product_ref)
             UNION ALL (SELECT product_id, name, category FROM sales.product_ref
              EXCEPT SELECT product_id, name, category FROM catalog.products)
             UNION ALL (SELECT product_id, name, category FROM catalog.products
              EXCEPT SELECT product_id, name, category FROM inventory.product_ref)
             UNION ALL (SELECT product_id, name, category FROM inventory.product_ref
              EXCEPT SELECT product_id, name, category FROM catalog.products)) x""",
    "un niveau de stock par produit":
        "SELECT (SELECT count(*) FROM inventory.stock_levels) <> (SELECT count(*) FROM catalog.products)",
    "Σ mouvements = stock physique":
        """SELECT count(*) > 0 FROM inventory.stock_levels l
           LEFT JOIN (SELECT product_id, sum(quantity) s FROM inventory.stock_movements GROUP BY 1) m
                  USING (product_id) WHERE coalesce(m.s, 0) <> l.on_hand""",
    "ventes (sales) = sorties de stock (inventory)":
        """SELECT count(*) > 0 FROM (
             SELECT product_id, sold_on AS d, quantity AS q FROM sales.sale_lines
             EXCEPT SELECT product_id, moved_on, -quantity FROM inventory.stock_movements WHERE kind = 'sale'
             UNION ALL
             (SELECT product_id, moved_on, -quantity FROM inventory.stock_movements WHERE kind = 'sale'
              EXCEPT SELECT product_id, sold_on, quantity FROM sales.sale_lines)) x""",
    "rupture en cours ⇔ stock physique nul":
        """SELECT count(*) > 0 FROM inventory.stock_levels l
           LEFT JOIN inventory.stockouts s ON s.product_id = l.product_id AND s.ended_on IS NULL
           WHERE (s.stockout_id IS NOT NULL) <> (l.on_hand = 0)""",
    "≥ 3 candidats fournisseurs disponibles par catégorie":
        """SELECT count(*) > 0 FROM catalog.categories c WHERE (SELECT count(*) FROM catalog.supplier_products sp
           WHERE sp.category = c.name AND sp.availability = 'in_stock') < 3""",
    "catégorie 'câbles' présente (faux LLM de l'orchestrateur)":
        "SELECT NOT EXISTS (SELECT 1 FROM catalog.categories WHERE name = 'câbles')",
    "ventes datées dans la période d'historique":
        "SELECT count(*) > 0 FROM sales.sale_lines WHERE sold_on > (SELECT max(updated_on) FROM inventory.stock_levels)",
}

FINGERPRINT_QUERIES = [
    "SELECT product_id, name, category, brand, attributes::text, unit_price, unit_cost FROM catalog.products ORDER BY 1",
    "SELECT supplier_product_id, supplier_id, name, category, cost, price, availability, stock_available, "
    "lead_time_days FROM catalog.supplier_products ORDER BY 1",
    "SELECT product_id, sold_on, quantity, unit_price, unit_cost FROM sales.sale_lines ORDER BY 1, 2",
    "SELECT product_id, moved_on, kind, quantity FROM inventory.stock_movements ORDER BY 1, 2, 3, 4",
    "SELECT product_id, on_hand, reserved, reorder_point FROM inventory.stock_levels "
    "JOIN inventory.product_ref USING (product_id) ORDER BY 1",
    "SELECT product_id, started_on, ended_on FROM inventory.stockouts ORDER BY 1, 2",
    "SELECT product_id, action, replacement_id, status, decided_by, decided_at FROM catalog.decisions ORDER BY 1",
]


def check(database_url: str) -> list[str]:
    """Renvoie la liste des anomalies (vide si le jeu est cohérent)."""
    with psycopg.connect(database_url) as conn:
        return [name for name, sql in CHECKS.items() if conn.execute(sql).fetchone()[0]]


def fingerprint(database_url: str) -> str:
    h = hashlib.sha256()
    with psycopg.connect(database_url) as conn:
        for sql in FINGERPRINT_QUERIES:
            for row in conn.execute(sql):
                h.update(repr(row).encode())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL", migrate.DEFAULT_URL))
    a = p.parse_args(argv)
    try:
        anomalies = check(a.database_url)
        fp = fingerprint(a.database_url)
    except psycopg.Error as e:
        print(f"ÉCHEC : {e}", file=sys.stderr)
        return 1
    for name in anomalies:
        print(f"ANOMALIE : {name}")
    print(f"{len(anomalies)} anomalie(s) sur {len(CHECKS)} vérifications — empreinte {fp[:16]}")
    return 1 if anomalies else 0


if __name__ == "__main__":
    sys.exit(main())
