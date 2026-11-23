"""LSO-6 / LSO-21 : jeu de données reproductible, cohérent, avec scénarios observables.

Les attentes sont vérifiées par des requêtes SQL indépendantes du code des services.
"""

import psycopg
import pytest

from data import generate, verify
from data.catalog_spec import MOCK_PRODUCTS, MOCK_SUPPLIER_PRODUCTS

pytestmark = pytest.mark.db


def q(url, sql, *args):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchall()


def test_seed_on_blank_database_passes_integrity_checks(seeded_db):
    assert verify.check(seeded_db) == []


def test_exactly_200_supplier_candidates_with_ids_accepted_by_orchestrator(seeded_db):
    rows = q(seeded_db, "SELECT supplier_product_id FROM catalog.supplier_products ORDER BY 1")
    ids = [r[0] for r in rows]
    assert len(ids) == 200 and ids[0] == "S101" and ids[-1] == "S300"


def test_mock_products_are_kept_for_orchestrator_continuity(seeded_db):
    rows = dict(q(seeded_db, "SELECT product_id, name FROM catalog.products WHERE product_id <= 'P006'"))
    assert rows == {pid: spec[0] for pid, spec in MOCK_PRODUCTS.items()}
    sup = dict(q(seeded_db, "SELECT supplier_product_id, name FROM catalog.supplier_products "
                            "WHERE supplier_product_id <= 'S104'"))
    assert sup == {sid: spec[0] for sid, spec in MOCK_SUPPLIER_PRODUCTS.items()}
    # catégorie utilisée telle quelle par le faux LLM de l'orchestrateur
    assert q(seeded_db, "SELECT count(*) FROM catalog.supplier_products WHERE category = 'câbles'")[0][0] >= 3


def _units(url, pid, months_back_start, months_back_end):
    """Unités vendues entre ref - start mois (exclu) et ref - end mois (inclus)."""
    return q(url, """SELECT coalesce(sum(quantity), 0) FROM sales.sale_lines
                     WHERE product_id = %s
                       AND sold_on >  DATE '2026-10-31' - make_interval(months => %s)
                       AND sold_on <= DATE '2026-10-31' - make_interval(months => %s)""",
             pid, months_back_start, months_back_end)[0][0]


@pytest.mark.parametrize("pid", ["P003", "P005", "P042"])
def test_decline_with_overstock_scenario(seeded_db, pid):
    last3, prev3 = _units(seeded_db, pid, 3, 0), _units(seeded_db, pid, 6, 3)
    assert last3 < 0.6 * prev3
    on_hand = q(seeded_db, "SELECT on_hand FROM inventory.stock_levels WHERE product_id = %s", pid)[0][0]
    sold30 = q(seeded_db, "SELECT coalesce(-sum(quantity), 0) FROM inventory.stock_movements WHERE product_id = %s "
                          "AND kind = 'sale' AND moved_on > DATE '2026-10-31' - 30", pid)[0][0]
    assert sold30 == 0 or on_hand / (sold30 / 30) > 180


@pytest.mark.parametrize("pid", ["P006", "P050"])
def test_ongoing_stockout_scenario(seeded_db, pid):
    assert q(seeded_db, "SELECT on_hand FROM inventory.stock_levels WHERE product_id = %s", pid)[0][0] == 0
    assert q(seeded_db, "SELECT count(*) FROM inventory.stockouts WHERE product_id = %s AND ended_on IS NULL",
             pid)[0][0] == 1


@pytest.mark.parametrize("pid", ["P020", "P031"])
def test_past_stockout_scenario(seeded_db, pid):
    assert q(seeded_db, "SELECT count(*) FROM inventory.stockouts WHERE product_id = %s AND ended_on IS NOT NULL",
             pid)[0][0] >= 1


def test_dead_product_has_stock_but_no_recent_sales(seeded_db):
    assert _units(seeded_db, "P013", 2, 0) == 0
    assert q(seeded_db, "SELECT on_hand FROM inventory.stock_levels WHERE product_id = 'P013'")[0][0] > 0


def test_growth_scenario(seeded_db):
    assert _units(seeded_db, "P001", 3, 0) > _units(seeded_db, "P001", 6, 3)


def test_seasonality_black_friday_month_above_average(seeded_db):
    monthly = q(seeded_db, "SELECT date_trunc('month', sold_on), sum(quantity) FROM sales.sale_lines "
                           "WHERE sold_on > '2025-04-30' GROUP BY 1 ORDER BY 1")
    avg = sum(u for _, u in monthly) / len(monthly)
    november = next(u for m, u in monthly if m.month == 11)
    assert november > 1.2 * avg


def test_demo_decisions_have_author_date_and_audit(seeded_db):
    rows = q(seeded_db, "SELECT d.status, d.decided_by, d.decided_at, count(e.*) FROM catalog.decisions d "
                        "JOIN catalog.decision_events e USING (decision_id) GROUP BY 1, 2, 3")
    assert {r[0] for r in rows} == {"approved", "refused"}
    assert all(r[1] and r[2] and r[3] >= 1 for r in rows)


def test_same_seed_gives_identical_data_and_other_seed_differs(migrated_db):
    generate.run(migrated_db, seed=42, reset=True)
    first = verify.fingerprint(migrated_db)
    generate.run(migrated_db, seed=42, reset=True)
    assert verify.fingerprint(migrated_db) == first
    generate.run(migrated_db, seed=7, reset=True)
    assert verify.fingerprint(migrated_db) != first
    generate.run(migrated_db, seed=42, reset=True)  # laisse la base dans l'état de référence


def test_refuses_to_seed_non_empty_database_without_reset(seeded_db):
    with pytest.raises(generate.SeedError):
        generate.run(seeded_db, seed=42)


def test_documented_commands_succeed(seeded_db, capsys):
    assert generate.main(["--database-url", seeded_db, "--seed", "42", "--reset"]) == 0
    assert verify.main(["--database-url", seeded_db]) == 0
    assert "0 anomalie" in capsys.readouterr().out


def test_concurrent_if_empty_seeds_once(migrated_db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from data import migrate

    migrate.reset(migrated_db, allow_decision_loss=True)
    migrate.migrate(migrated_db)
    build = generate.build_products
    both_saw_empty = Barrier(2, timeout=30)

    def synchronized_build(rng):
        both_saw_empty.wait()
        return build(rng)

    monkeypatch.setattr(generate, "build_products", synchronized_build)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(generate.run, migrated_db, if_empty=True) for _ in range(2)]
        results = [job.result() for job in jobs]
    assert sum(result == {"skipped": True} for result in results) == 1
    assert sum(result.get("products") == 60 for result in results) == 1
    assert verify.check(migrated_db) == []
