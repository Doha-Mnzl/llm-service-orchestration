"""LSO-8 / LSO-16 / LSO-17 : service ventes, valeurs attendues calculées à la main
(voir tests/reference_data.py)."""

import pytest
from fastapi.testclient import TestClient

from services.sales import metrics
from services.sales.main import app
from tests.reference_data import AS_OF

# ---------- calculs purs (sans base) ----------


def test_period_is_rolling_months_ending_on_analysis_date():
    from datetime import date
    assert metrics.period_bounds(date(2026, 6, 30), 1) == (date(2026, 6, 1), date(2026, 6, 30))
    assert metrics.period_bounds(date(2026, 3, 31), 1) == (date(2026, 3, 1), date(2026, 3, 31))
    assert metrics.period_bounds(date(2026, 6, 30), 3) == (date(2026, 4, 1), date(2026, 6, 30))


def test_ratios_handle_zero_denominators():
    assert metrics.ratio(440, 1160) == pytest.approx(0.3793, abs=1e-4)
    assert metrics.ratio(0, 0) is None
    assert metrics.change(1160, 500) == pytest.approx(1.32)
    assert metrics.change(150, 0) is None
    assert metrics.trend([20, 20, 20, 10, 5, 12]) == pytest.approx(-0.55)


# ---------- API ----------

@pytest.fixture
def client(reference_db, point_services_to, monkeypatch):
    from services.sales.config import settings
    point_services_to(reference_db)
    monkeypatch.setattr(settings, "as_of", AS_OF)
    return TestClient(app)


pytestmark_db = pytest.mark.db


@pytestmark_db
def test_summary_computes_revenue_margin_and_trend_exactly(client):
    r = client.get("/sales/summary", params={"period_months": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["period_months"] == 1 and body["start"] == "2026-06-01" and body["end"] == "2026-06-30"
    items = {i["product_id"]: i for i in body["items"]}
    p101 = items["P101"]
    assert (p101["units_sold"], p101["revenue"], p101["gross_margin"]) == (12, 1160.0, 440.0)
    assert p101["margin_rate"] == pytest.approx(0.3793, abs=1e-4)
    assert p101["trend_6m"] == pytest.approx(-0.55)
    assert p101["previous_revenue"] == 500.0 and p101["revenue_change"] == pytest.approx(1.32)
    assert items["P102"]["margin_rate"] == pytest.approx(0.10) and items["P102"]["trend_6m"] is None
    assert items["P103"]["units_sold"] == 0 and items["P103"]["margin_rate"] is None


@pytestmark_db
def test_summary_keeps_fields_of_shared_contract(client):
    item = client.get("/sales/summary").json()["items"][0]
    # champs de docs/api.md (PR #1) consommés par l'orchestrateur et Analysis
    assert {"product_id", "name", "category", "units_sold", "revenue", "margin_rate", "trend_6m"} <= item.keys()


@pytestmark_db
def test_summary_over_three_months_and_category_filter_ignores_accents(client):
    body = client.get("/sales/summary", params={"period_months": 3, "category": "CABLES"}).json()
    items = {i["product_id"]: i for i in body["items"]}
    assert set(items) == {"P101", "P104", "P105"}
    assert items["P101"]["units_sold"] == 27 and items["P101"]["revenue"] == 2660.0


@pytestmark_db
def test_summary_product_filter_sort_and_limit(client):
    body = client.get("/sales/summary", params={"product_id": "P102"}).json()
    assert [i["product_id"] for i in body["items"]] == ["P102"]
    body = client.get("/sales/summary", params={"sort": "revenue", "limit": 2}).json()
    assert [i["product_id"] for i in body["items"]] == ["P101", "P105"]


@pytestmark_db
def test_empty_period_returns_zeros_not_error(client, monkeypatch):
    from services.sales.config import settings
    monkeypatch.setattr(settings, "as_of", "2025-03-31")
    body = client.get("/sales/summary").json()
    assert all(i["units_sold"] == 0 and i["revenue"] == 0 for i in body["items"])


@pytestmark_db
def test_unknown_category_returns_empty_list(client):
    assert client.get("/sales/summary", params={"category": "inconnue"}).json()["items"] == []


@pytestmark_db
@pytest.mark.parametrize("params", [{"period_months": 0}, {"period_months": 25}, {"sort": "x"}, {"limit": 0},
                                    {"product_id": "X1"}])
def test_invalid_parameters_return_422(client, params):
    assert client.get("/sales/summary", params=params).status_code == 422


@pytestmark_db
def test_unknown_product_returns_404(client):
    assert client.get("/sales/summary", params={"product_id": "P999"}).status_code == 404
    assert client.get("/sales/trend/P999").status_code == 404
    assert client.get("/sales/products/P999").status_code == 404


@pytestmark_db
def test_trend_returns_monthly_units_oldest_first(client):
    body = client.get("/sales/trend/P101", params={"months": 6}).json()
    assert body["monthly_units"] == [20, 20, 20, 10, 5, 12]
    assert body["monthly_revenue"] == [2000.0, 2000.0, 2000.0, 1000.0, 500.0, 1160.0]
    assert body["periods"][0]["start"] == "2026-01-01"


@pytestmark_db
def test_product_detail_compares_with_previous_period(client):
    body = client.get("/sales/products/P101", params={"period_months": 1}).json()
    assert body["current"]["revenue"] == 1160.0 and body["previous"]["revenue"] == 500.0
    assert body["changes"]["revenue"] == pytest.approx(1.32)
    assert body["changes"]["units"] == pytest.approx(1.4)  # (12 − 5) / 5


@pytestmark_db
def test_monthly_totals_for_dashboard(client):
    body = client.get("/sales/monthly", params={"months": 2}).json()
    assert [m["month"] for m in body["items"]] == ["2026-05", "2026-06"]
    june = body["items"][1]
    assert june["revenue"] == 1160.0 + 150.0 + 180.0 and june["units"] == 21
    assert body["currency"] == "MAD"


@pytestmark_db
def test_health_reports_database(client):
    assert client.get("/health").json() == {"status": "ok", "service": "sales", "database": "up"}


def test_database_down_returns_503_so_orchestrator_retries(monkeypatch):
    from services.sales.config import settings
    monkeypatch.setattr(settings, "database_url", "postgresql://x:y@127.0.0.1:1/none")
    c = TestClient(app)
    assert c.get("/sales/summary").status_code == 503
    r = c.get("/health")
    assert r.status_code == 503 and r.json()["database"] == "down"


# ---------- revue Codex : bornes dérivées de la même date d'analyse ----------

def test_previous_period_matches_monthly_windows_on_a_non_month_end_date():
    from datetime import date
    as_of = date(2026, 3, 30)
    assert metrics.previous_period_bounds(as_of, 1) == (date(2026, 1, 31), date(2026, 2, 28))
    assert metrics.previous_period_bounds(as_of, 1) == metrics.monthly_windows(as_of, 2)[0]
    assert metrics.period_bounds(as_of, 1) == metrics.monthly_windows(as_of, 2)[1]


@pytestmark_db
def test_summary_and_monthly_share_the_same_boundaries(client, monkeypatch):
    from services.sales.config import settings
    monkeypatch.setattr(settings, "as_of", "2026-03-30")
    summary = client.get("/sales/summary").json()
    monthly = client.get("/sales/monthly", params={"months": 2}).json()
    assert summary["as_of"] == "2026-03-30"
    assert (summary["previous_start"], summary["previous_end"]) == ("2026-01-31", "2026-02-28")
    assert (monthly["items"][0]["start"], monthly["items"][0]["end"]) == ("2026-01-31", "2026-02-28")
    assert summary["start"] == monthly["items"][1]["start"] == "2026-03-01"
