"""LSO-10 / LSO-20 / LSO-21 : produits du magasin et catalogue fournisseurs."""

import pytest
from fastapi.testclient import TestClient

from services.catalog.main import app

pytestmark = pytest.mark.db


@pytest.fixture
def client(reference_db, point_services_to):
    point_services_to(reference_db)
    return TestClient(app)


def ids(items, key="product_id"):
    return [i[key] for i in items]


def test_categories_with_counts(client):
    assert client.get("/catalog/categories").json() == [
        {"name": "câbles", "label": "Câbles", "product_count": 3, "supplier_product_count": 2},
        {"name": "souris", "label": "Souris", "product_count": 2, "supplier_product_count": 2}]


def test_product_search_by_category_and_keyword_ignoring_accents(client):
    assert ids(client.get("/catalog/products", params={"category": "Souris"}).json()["items"]) == ["P102", "P103"]
    assert ids(client.get("/catalog/products", params={"query": "cable"}).json()["items"]) == ["P101", "P104", "P105"]
    assert client.get("/catalog/products", params={"query": "introuvable"}).json()["items"] == []


def test_product_pagination_is_deterministic_and_bounded(client):
    page1 = client.get("/catalog/products", params={"size": 2, "page": 1}).json()
    assert ids(page1["items"]) == ["P101", "P102"] and page1["total"] == 5
    assert ids(client.get("/catalog/products", params={"size": 2, "page": 3}).json()["items"]) == ["P105"]
    assert client.get("/catalog/products", params={"size": 2, "page": 9}).json()["items"] == []
    assert client.get("/catalog/products", params={"size": 101}).status_code == 422
    assert client.get("/catalog/products", params={"page": 0}).status_code == 422


def test_product_by_id_with_characteristics(client):
    body = client.get("/catalog/products/P101").json()
    assert body["attributes"] == {"exemple": "oui"} and body["unit_price"] == 100.0 and body["brand"] == "Voltix"
    assert client.get("/catalog/products/P999").status_code == 404
    assert client.get("/catalog/products/ABC").status_code == 422


def test_supplier_search_keeps_contract_and_adds_cost_availability_lead_time(client):
    body = client.get("/catalog/search", params={"category": "souris"}).json()
    assert ids(body["items"], "supplier_product_id") == ["S202", "S203"]  # tri par prix
    s203 = body["items"][1]
    assert {"supplier_product_id", "name", "category", "price"} <= s203.keys()  # contrat docs/api.md
    assert (s203["cost"], s203["availability"], s203["lead_time_days"], s203["supplier_name"]) == \
           (60.0, "limited", 7, "Atlas Distribution")
    assert s203["estimated_margin_rate"] == pytest.approx(0.6)


@pytest.mark.parametrize("params, expected", [
    ({"category": "souris", "available_only": "true"}, ["S203"]),
    ({"category": "souris", "max_price": 130}, ["S202"]),
    ({"category": "souris", "query": "ergo"}, ["S203"]),
    ({"category": "CABLES"}, ["S204", "S201"]),
    ({"category": "inconnue"}, []),
])
def test_supplier_search_filters(client, params, expected):
    assert ids(client.get("/catalog/search", params=params).json()["items"], "supplier_product_id") == expected


def test_supplier_product_lookup_and_unknown_references(client):
    assert client.get("/catalog/supplier-products/S201").json()["lead_time_days"] == 5
    assert client.get("/catalog/supplier-products/S999").status_code == 404
    assert client.get("/catalog/supplier-products/Z1").status_code == 422
    body = client.get("/catalog/supplier-products", params=[("ids", "S201"), ("ids", "S999")]).json()
    assert ids(body["items"], "supplier_product_id") == ["S201"] and body["unknown_ids"] == ["S999"]


def test_candidates_exist_in_catalog_and_match_category(client):
    body = client.get("/catalog/candidates/P102").json()
    assert body["category"] == "souris" and ids(body["candidates"], "supplier_product_id") == ["S203"]
    all_ = client.get("/catalog/candidates/P102", params={"available_only": "false"}).json()["candidates"]
    assert ids(all_, "supplier_product_id") == ["S202", "S203"]
    for c in all_:  # aucune référence inventée
        assert client.get(f"/catalog/supplier-products/{c['supplier_product_id']}").status_code == 200
    assert client.get("/catalog/candidates/P999").status_code == 404
