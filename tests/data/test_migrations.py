"""LSO-5 : création reproductible des schémas et contraintes d'intégrité."""

import psycopg
import pytest

from data import migrate

pytestmark = pytest.mark.db

EXPECTED_TABLES = {
    "catalog": {"categories", "products", "suppliers", "supplier_products", "decisions", "decision_events"},
    "sales": {"product_ref", "sale_lines"},
    "inventory": {"product_ref", "stock_levels", "stock_movements", "stockouts"},
}


def _tables(url):
    with psycopg.connect(url) as c:
        rows = c.execute("SELECT table_schema, table_name FROM information_schema.tables "
                         "WHERE table_schema IN ('catalog','sales','inventory')").fetchall()
    out: dict[str, set] = {}
    for schema, table in rows:
        out.setdefault(schema, set()).add(table)
    return out


def test_migrations_create_the_six_domains_on_blank_database(migrated_db):
    assert _tables(migrated_db) == EXPECTED_TABLES


def test_migrations_are_idempotent(migrated_db):
    assert migrate.migrate(migrated_db) == []  # rien à rejouer
    assert _tables(migrated_db) == EXPECTED_TABLES


def test_modified_applied_migration_is_detected(migrated_db, tmp_path, monkeypatch):
    with psycopg.connect(migrated_db) as c:
        c.execute("UPDATE public.schema_migrations SET checksum = 'x' WHERE service = 'sales'")
    try:
        with pytest.raises(migrate.MigrationError):
            migrate.migrate(migrated_db)
    finally:
        migrate.reset(migrated_db)
        migrate.migrate(migrated_db)


# ---------- contraintes ----------

def _base(c):
    c.execute("INSERT INTO catalog.categories VALUES ('câbles', 'Câbles')")
    c.execute("INSERT INTO catalog.suppliers VALUES ('F01', 'Fournisseur test', 'Maroc', 0.9)")
    c.execute("INSERT INTO catalog.products (product_id, name, category, brand, unit_price, unit_cost, created_on) "
              "VALUES ('P001', 'Câble', 'câbles', 'X', 70, 40, '2025-01-01')")
    c.execute("INSERT INTO catalog.supplier_products (supplier_product_id, supplier_id, name, category, brand, "
              "cost, price, availability, stock_available, lead_time_days) "
              "VALUES ('S101', 'F01', 'Câble tressé', 'câbles', 'Y', 30, 69, 'in_stock', 10, 5)")
    c.execute("INSERT INTO inventory.product_ref VALUES ('P001', 'Câble', 'câbles', 5)")
    c.execute("INSERT INTO sales.product_ref VALUES ('P001', 'Câble', 'câbles')")


INVALID = {
    "id produit mal formé": "INSERT INTO catalog.products (product_id, name, category, brand, unit_price, unit_cost, "
                            "created_on) VALUES ('X1', 'n', 'câbles', 'b', 1, 1, '2025-01-01')",
    "prix négatif": "INSERT INTO catalog.products (product_id, name, category, brand, unit_price, unit_cost, "
                    "created_on) VALUES ('P002', 'nom', 'câbles', 'b', -1, 1, '2025-01-01')",
    "catégorie inconnue": "INSERT INTO catalog.products (product_id, name, category, brand, unit_price, unit_cost, "
                          "created_on) VALUES ('P002', 'nom', 'inconnue', 'b', 1, 1, '2025-01-01')",
    "id fournisseur mal formé": "INSERT INTO catalog.supplier_products (supplier_product_id, supplier_id, name, "
                                "category, brand, cost, price, availability, stock_available, lead_time_days) "
                                "VALUES ('Z1', 'F01', 'nom', 'câbles', 'b', 1, 1, 'in_stock', 1, 1)",
    "disponibilité incohérente": "INSERT INTO catalog.supplier_products (supplier_product_id, supplier_id, name, "
                                 "category, brand, cost, price, availability, stock_available, lead_time_days) "
                                 "VALUES ('S102', 'F01', 'nom', 'câbles', 'b', 1, 1, 'out_of_stock', 5, 1)",
    "délai nul": "INSERT INTO catalog.supplier_products (supplier_product_id, supplier_id, name, category, brand, "
                 "cost, price, availability, stock_available, lead_time_days) "
                 "VALUES ('S102', 'F01', 'nom', 'câbles', 'b', 1, 1, 'in_stock', 1, 0)",
    "replace sans remplaçant": "INSERT INTO catalog.decisions (recorded_by_client, product_id, action, reason, status) "
                               "VALUES ('t', 'P001', 'replace', 'raison', 'pending')",
    "remove avec remplaçant": "INSERT INTO catalog.decisions (recorded_by_client, product_id, action, replacement_id, reason, status) "
                              "VALUES ('t', 'P001', 'remove', 'S101', 'raison', 'pending')",
    "remplaçant inexistant": "INSERT INTO catalog.decisions (recorded_by_client, product_id, action, replacement_id, reason, status) "
                             "VALUES ('t', 'P001', 'replace', 'S999', 'raison', 'pending')",
    "décision tranchée sans date": "INSERT INTO catalog.decisions (recorded_by_client, product_id, action, reason, status, decided_by) "
                                   "VALUES ('t', 'P001', 'keep', 'raison', 'approved', 'manal')",
    "décision tranchée sans auteur": "INSERT INTO catalog.decisions (recorded_by_client, product_id, action, reason, status, decided_at) "
                                     "VALUES ('t', 'P001', 'keep', 'raison', 'approved', now())",
    "action inconnue": "INSERT INTO catalog.decisions (recorded_by_client, product_id, action, reason, status) "
                       "VALUES ('t', 'P001', 'delete', 'raison', 'pending')",
    "vente quantité nulle": "INSERT INTO sales.sale_lines (product_id, sold_on, quantity, unit_price, unit_cost) "
                            "VALUES ('P001', '2026-01-01', 0, 1, 1)",
    "vente produit inconnu": "INSERT INTO sales.sale_lines (product_id, sold_on, quantity, unit_price, unit_cost) "
                             "VALUES ('P999', '2026-01-01', 1, 1, 1)",
    "stock négatif": "INSERT INTO inventory.stock_levels (product_id, on_hand, reserved, updated_on) "
                     "VALUES ('P001', -1, 0, '2026-01-01')",
    "réservé > physique": "INSERT INTO inventory.stock_levels (product_id, on_hand, reserved, updated_on) "
                          "VALUES ('P001', 5, 6, '2026-01-01')",
    "mouvement vente positif": "INSERT INTO inventory.stock_movements (product_id, moved_on, kind, quantity) "
                               "VALUES ('P001', '2026-01-01', 'sale', 3)",
    "réception négative": "INSERT INTO inventory.stock_movements (product_id, moved_on, kind, quantity) "
                          "VALUES ('P001', '2026-01-01', 'receipt', -3)",
    "rupture terminée avant son début": "INSERT INTO inventory.stockouts (product_id, started_on, ended_on) "
                                        "VALUES ('P001', '2026-02-01', '2026-01-01')",
}


@pytest.mark.parametrize("case", list(INVALID))
def test_integrity_constraint_rejects(conn, case):
    _base(conn)
    with pytest.raises(psycopg.errors.IntegrityError):
        conn.execute(INVALID[case])


def test_valid_rows_are_accepted_and_available_is_computed(conn):
    _base(conn)
    conn.execute("INSERT INTO inventory.stock_levels (product_id, on_hand, reserved, updated_on) "
                 "VALUES ('P001', 10, 3, '2026-01-01')")
    assert conn.execute("SELECT available FROM inventory.stock_levels").fetchone()[0] == 7


def test_only_one_open_stockout_per_product(conn):
    _base(conn)
    conn.execute("INSERT INTO inventory.stockouts (product_id, started_on) VALUES ('P001', '2026-01-01')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO inventory.stockouts (product_id, started_on) VALUES ('P001', '2026-02-01')")


def test_idempotency_key_is_unique(conn):
    _base(conn)
    sql = ("INSERT INTO catalog.decisions (recorded_by_client, product_id, action, reason, status, idempotency_key) "
           "VALUES ('t', 'P001', 'keep', 'raison', 'pending', 'k1')")
    conn.execute(sql)
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(sql)


def test_decision_audit_log_is_append_only(conn):
    _base(conn)
    did = conn.execute("INSERT INTO catalog.decisions (recorded_by_client, product_id, action, reason, status) "
                       "VALUES ('t', 'P001', 'keep', 'raison', 'pending') RETURNING decision_id").fetchone()[0]
    conn.execute("INSERT INTO catalog.decision_events (decision_id, to_status, actor, client) "
                 "VALUES (%s, 'pending', 'a', 't')", (did,))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM catalog.decision_events")


def test_concurrent_migrations_are_serialized(migrated_db):
    """Deux démarrages simultanés : chaque migration est appliquée une seule fois, sans erreur."""
    from concurrent.futures import ThreadPoolExecutor
    migrate.reset(migrated_db, allow_decision_loss=True)
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda _: migrate.migrate(migrated_db), range(3)))
    applied = sorted(m for r in results for m in r)
    assert applied == sorted(f"{s}/{f.name}" for s in migrate.SERVICES for f in migrate._files(s))
    assert _tables(migrated_db) == EXPECTED_TABLES
