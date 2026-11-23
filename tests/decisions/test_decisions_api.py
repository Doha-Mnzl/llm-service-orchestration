"""LSO-11 / LSO-22 / LSO-23 : décisions humaines, audit et transitions (client authentifié).

Sécurité, idempotence et concurrence : test_decisions_security.py ; transactions : test_transactions.py.
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

from services.catalog.main import app
from tests.decisions.helpers import auth, decision_body

pytestmark = pytest.mark.db


@pytest.fixture
def client(reference_db, point_services_to, service_tokens):
    with psycopg.connect(reference_db) as c:
        c.execute("TRUNCATE catalog.decision_events, catalog.decisions RESTART IDENTITY")
    point_services_to(reference_db)
    return TestClient(app)


def post(client, **changes):
    return client.post("/decisions", json=decision_body(**changes), headers=auth())


def test_explicit_approval_is_recorded_with_audit(client):
    r = post(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "recorded"  # champ du contrat docs/api.md (PR #1)
    assert (body["decision_status"], body["decided_by"], body["recorded_by_client"]) == \
           ("approved", "manal", "orchestrator")
    assert body["decided_at"]
    events = client.get(f"/decisions/{body['decision_id']}").json()["events"]
    assert [(e["from_status"], e["to_status"], e["actor"], e["client"]) for e in events] == \
           [(None, "approved", "manal", "orchestrator")]


@pytest.mark.parametrize("changes, code", [
    ({"replacement_id": None}, 422),                      # replace sans remplaçant
    ({"action": "remove"}, 422),                          # remove avec remplaçant
    ({"replacement_id": "S999"}, 422),                    # référence fournisseur inexistante
    ({"product_id": "P999"}, 404),                        # produit inconnu
    ({"action": "delete"}, 422),
    ({"reason": "x"}, 422),
    ({"decided_by": ""}, 422),
    ({"decision": "cancelled"}, 422),                     # on ne crée pas une décision annulée
])
def test_invalid_decisions_are_rejected(client, changes, code):
    assert post(client, **changes).status_code == code
    assert client.get("/decisions").json()["total"] == 0


def test_explicit_refusal_is_audited(client):
    body = post(client, decision="refused").json()
    assert (body["decision_status"], body["decided_by"]) == ("refused", "manal")


def test_pending_proposal_lifecycle_and_illegal_transitions(client):
    did = post(client, decision="pending", decided_by=None).json()["decision_id"]
    assert client.get(f"/decisions/{did}").json()["decided_at"] is None
    assert client.post(f"/decisions/{did}/approve", json={}, headers=auth()).status_code == 422  # auteur obligatoire
    ok = client.post(f"/decisions/{did}/approve", json={"decided_by": "manal"}, headers=auth())
    assert ok.status_code == 200 and ok.json()["decision_status"] == "approved"
    illegal = client.post(f"/decisions/{did}/refuse", json={"decided_by": "manal"}, headers=auth())
    assert illegal.status_code == 409 and "approved" in illegal.json()["detail"]
    events = client.get(f"/decisions/{did}").json()["events"]
    assert [(e["from_status"], e["to_status"]) for e in events] == [(None, "pending"), ("pending", "approved")]
    assert client.post("/decisions/999/approve", json={"decided_by": "manal"}, headers=auth()).status_code == 404


def test_cancelled_proposal_cannot_be_approved(client):
    did = post(client, decision="pending", decided_by=None).json()["decision_id"]
    cancelled = client.post(f"/decisions/{did}/cancel", json={"decided_by": "manal"}, headers=auth())
    assert cancelled.json()["decision_status"] == "cancelled"
    assert client.post(f"/decisions/{did}/approve", json={"decided_by": "manal"}, headers=auth()).status_code == 409


def test_history_filters(client):
    post(client)
    post(client, product_id="P102", action="remove", replacement_id=None, reason="Ventes nulles",
         decision="refused")
    assert client.get("/decisions").json()["total"] == 2
    assert [d["product_id"] for d in client.get("/decisions", params={"status": "refused"}).json()["items"]] == \
        ["P102"]
    assert client.get("/decisions", params={"product_id": "P101"}).json()["total"] == 1
    assert client.get("/decisions/12345").status_code == 404


def test_approved_removal_never_deletes_or_modifies_the_product(client):
    before = client.get("/catalog/products/P102").json()
    assert post(client, product_id="P102", action="remove", replacement_id=None,
                reason="Ventes nulles").status_code == 201
    assert client.get("/catalog/products/P102").json() == before
