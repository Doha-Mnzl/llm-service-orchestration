"""LSO-9 / LSO-18 / LSO-19 : service stocks (valeurs attendues : tests/reference_data.py)."""

import pytest
from fastapi.testclient import TestClient

from services.inventory import metrics
from services.inventory.main import app
from tests.reference_data import AS_OF

# ---------- règles de couverture (sans base) ----------


@pytest.mark.parametrize("available, units_out, reorder, expected", [
    (100, 12, 30, (250, "overstock")),       # 12 sorties / 30 j = 0,4 par jour
    (0, 12, 30, (0, "stockout")),
    (15, 0, 20, (None, "no_demand")),         # demande nulle : couverture non définie
    (5, 6, 10, (25, "critical")),             # sous le seuil d'alerte
    (40, 60, 10, (20, "ok")),
    (None, 30, 10, (None, "missing_data")),   # valeur manquante
    (20, 1, 0, (600, "overstock")),           # cas Codex : 20 / (1/30) = 600 exactement (pas 601)
    (5, 12, 0, (13, "ok")),                   # 12,5 jours ⇒ 13 (arrondi au plus proche, pas « au pair »)
])
def test_coverage_rules(available, units_out, reorder, expected):
    assert metrics.coverage(available, units_out, 30, reorder, overstock_days=180) == expected


def test_average_daily_demand():
    assert metrics.average_daily_demand(12, 30) == pytest.approx(0.4)
    assert metrics.average_daily_demand(0, 30) == 0.0


# ---------- API ----------

pytestmark_db = pytest.mark.db


@pytest.fixture
def client(reference_db, point_services_to, monkeypatch):
    from services.inventory.config import settings
    point_services_to(reference_db)
    monkeypatch.setattr(settings, "as_of", AS_OF)
    return TestClient(app)


def _items(body):
    return {i["product_id"]: i for i in body["items"]}


@pytestmark_db
def test_stock_levels_physical_reserved_available_and_coverage(client):
    items = _items(client.get("/stocks").json())
    p = items["P101"]
    assert (p["on_hand"], p["reserved"], p["available"], p["quantity"]) == (120, 20, 100, 100)
    assert p["avg_daily_demand"] == pytest.approx(0.4) and p["coverage_days"] == 250
    assert p["coverage_status"] == "overstock"
    assert (items["P102"]["quantity"], items["P102"]["coverage_days"]) == (0, 0)
    assert (items["P103"]["coverage_days"], items["P103"]["coverage_status"]) == (None, "no_demand")
    assert (items["P104"]["quantity"], items["P104"]["coverage_status"]) == (None, "missing_data")
    assert items["P105"]["coverage_status"] == "critical"


@pytestmark_db
def test_contract_fields_kept(client):
    item = client.get("/stocks").json()["items"][0]
    assert {"product_id", "name", "category", "quantity", "coverage_days"} <= item.keys()


@pytestmark_db
def test_repeated_product_ids_and_unknown_products_are_reported(client):
    # format envoyé par l'orchestrateur : product_ids=P101&product_ids=P999
    body = client.get("/stocks", params=[("product_ids", "P101"), ("product_ids", "P999"),
                                         ("product_ids", "bad")]).json()
    assert list(_items(body)) == ["P101"]
    assert body["unknown_product_ids"] == ["P999", "bad"]


@pytestmark_db
def test_category_filter_ignores_case_and_accents(client):
    assert set(_items(client.get("/stocks", params={"category": "CÂBLES"}).json())) == {"P101", "P104", "P105"}


@pytestmark_db
def test_alerts(client):
    body = client.get("/stocks/alerts").json()
    assert [i["product_id"] for i in body["overstock"]] == ["P101", "P103"]
    assert body["stockout"] == [{"product_id": "P102", "name": "Souris filaire", "category": "souris",
                                 "quantity": 0, "since": "2026-06-05", "days_out": 25}]
    assert [i["product_id"] for i in body["critical"]] == ["P105"]
    assert set(client.get("/stocks/alerts", params={"kind": "stockout"}).json()) == {"stockout", "as_of", "historical"}
    assert client.get("/stocks/alerts", params={"kind": "nope"}).status_code == 422


@pytestmark_db
def test_single_product_with_open_stockout(client):
    assert client.get("/stocks/P101").json()["open_stockout"] is None
    assert client.get("/stocks/P102").json()["open_stockout"]["started_on"] == "2026-06-05"
    assert client.get("/stocks/P999").status_code == 404
    assert client.get("/stocks/X12").status_code == 422


@pytestmark_db
def test_movements_have_no_double_counting(client):
    body = client.get("/stocks/P101/movements").json()
    assert len(body["items"]) == 8
    assert (body["total_in"], body["total_out"], body["net"]) == (207, 87, 120)
    assert body["net"] == client.get("/stocks/P101").json()["on_hand"]
    assert len(client.get("/stocks/P101/movements", params={"kind": "sale"}).json()["items"]) == 7
    assert client.get("/stocks/P999/movements").status_code == 404


@pytestmark_db
def test_stockout_history(client):
    assert len(client.get("/stockouts").json()["items"]) == 2
    assert len(client.get("/stockouts", params={"open": "true"}).json()["items"]) == 1
    closed = client.get("/stockouts", params={"product_id": "P102", "open": "false"}).json()["items"]
    assert closed == [{"product_id": "P102", "name": "Souris filaire", "started_on": "2026-02-01",
                       "ended_on": "2026-02-10", "duration_days": 9, "open": False}]


def test_database_down_returns_503(monkeypatch):
    from services.inventory.config import settings
    monkeypatch.setattr(settings, "database_url", "postgresql://x:y@127.0.0.1:1/none")
    c = TestClient(app)
    assert c.get("/stocks").status_code == 503
    assert c.get("/health").status_code == 503


# ---------- revue Codex : état à une date antérieure à l'instantané ----------

@pytestmark_db
def test_stock_before_the_snapshot_is_rebuilt_from_movements(client, monkeypatch):
    from services.inventory.config import settings
    monkeypatch.setattr(settings, "as_of", "2026-06-04")   # veille de la rupture de P102 (05/06)
    body = client.get("/stocks").json()
    items = _items(body)
    assert body["historical"] is True
    # réservé non historisé ⇒ disponible inconnu (null), jamais confondu avec le physique (revue de code)
    p101 = items["P101"]
    assert (p101["on_hand"], p101["reserved"], p101["available"], p101["quantity"]) == (132, None, None, None)
    assert p101["coverage_status"] == "missing_data"
    assert (items["P102"]["on_hand"], items["P102"]["available"]) == (3, None)
    alerts = client.get("/stocks/alerts", params={"kind": "stockout"}).json()
    assert alerts["stockout"] == [] and alerts["historical"] is True
    detail = client.get("/stocks/P102").json()
    assert detail["open_stockout"] is None and detail["historical"] is True


@pytestmark_db
def test_stockouts_are_seen_as_they_were_at_the_analysis_date(client, monkeypatch):
    from services.inventory.config import settings
    monkeypatch.setattr(settings, "as_of", "2026-02-05")
    items = client.get("/stockouts").json()["items"]
    assert items == [{"product_id": "P102", "name": "Souris filaire", "started_on": "2026-02-01",
                      "ended_on": None, "duration_days": 4, "open": True}]
    assert all(i["duration_days"] >= 0 for i in items)


@pytestmark_db
def test_current_snapshot_is_not_flagged_historical(client):
    assert client.get("/stocks").json()["historical"] is False


@pytestmark_db
def test_alerts_have_a_documented_response_model(client):
    schema = client.get("/openapi.json").json()
    ref = schema["paths"]["/stocks/alerts"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    model = schema["components"]["schemas"][ref["$ref"].rsplit("/", 1)[-1]]
    assert {"as_of", "overstock", "stockout", "critical"} <= set(model["properties"])


@pytestmark_db
def test_alert_items_keep_null_fields_required_by_the_contract(client):
    """Régression trouvée par le comparateur de contrat : un produit sans demande garde coverage_days: null."""
    overstock = {i["product_id"]: i for i in client.get("/stocks/alerts").json()["overstock"]}
    assert "coverage_days" in overstock["P103"] and overstock["P103"]["coverage_days"] is None
