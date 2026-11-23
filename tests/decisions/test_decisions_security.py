"""H2 (autorisation, pas d'approbation implicite) et H4 (idempotence stricte)."""

from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi.testclient import TestClient

from services.catalog import auth as catalog_auth
from services.catalog.config import Settings
from services.catalog.main import app
from tests.decisions.helpers import AUTH, OTHER_AUTH, OTHER_TOKEN, auth, decision_body

pytestmark = pytest.mark.db

# Corps EXACT envoyé aujourd'hui par l'orchestrateur de la PR #1 (sans en-tête, sans `decision`).
LEGACY_BODY = {"product_id": "P101", "action": "replace", "replacement_id": "S201",
               "reason": "Ventes en forte baisse et stock élevé"}


@pytest.fixture
def client(reference_db, point_services_to, service_tokens):
    with psycopg.connect(reference_db) as c:
        c.execute("TRUNCATE catalog.decision_events, catalog.decisions RESTART IDENTITY")
    point_services_to(reference_db)
    return TestClient(app)


def rows(url, sql="SELECT recorded_by_client, decided_by, status FROM catalog.decisions ORDER BY 1"):
    with psycopg.connect(url) as c:
        return c.execute(sql).fetchall()


# ---------- H2 : authentification et autorisation ----------

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer mauvais-jeton-0123456789"},
                                     {"Authorization": "Basic dXNlcjpwYXNz"}, {"Authorization": "Bearer "}])
def test_write_without_valid_service_token_is_rejected(client, reference_db, headers):
    r = client.post("/decisions", json=decision_body(), headers=headers)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert rows(reference_db) == []


def test_transitions_require_a_service_token(client):
    did = client.post("/decisions", json=decision_body(decision="pending", decided_by=None), headers=auth()).json()["decision_id"]
    for verb in ("approve", "refuse", "cancel"):
        assert client.post(f"/decisions/{did}/{verb}", json={"decided_by": "manal"}).status_code == 401
    assert client.get(f"/decisions/{did}").json()["decision_status"] == "pending"


def test_no_implicit_approval_decision_must_be_explicit(client, reference_db):
    r = client.post("/decisions", json=LEGACY_BODY, headers=auth())
    assert r.status_code == 422 and "decision" in r.text
    assert rows(reference_db) == []


def test_settled_decision_requires_declared_decider(client):
    assert client.post("/decisions", json=decision_body(decided_by=None), headers=auth()).status_code == 422


def test_identity_comes_from_token_not_from_body(client, reference_db):
    body = {**decision_body(decided_by="directeur"), "source": "seed", "recorded_by_client": "orchestrator"}
    r = client.post("/decisions", json=body, headers=auth(OTHER_TOKEN))
    assert r.status_code == 201
    created = r.json()
    assert created["recorded_by_client"] == "client-test" and created["source"] == "api"
    assert rows(reference_db) == [("client-test", "directeur", "approved")]
    events = client.get(f"/decisions/{created['decision_id']}").json()["events"]
    assert [e["client"] for e in events] == ["client-test"]


def test_fail_closed_when_no_token_is_configured(client, reference_db, monkeypatch):
    from services.catalog.config import settings
    monkeypatch.setattr(settings, "service_tokens", {})
    assert client.post("/decisions", json=decision_body(), headers=auth()).status_code == 401


def test_legacy_mode_is_off_by_default_and_tokens_are_validated(monkeypatch):
    monkeypatch.delenv("DECISIONS_LEGACY_ORCHESTRATOR", raising=False)
    monkeypatch.setenv("CATALOG_SERVICE_TOKENS", "orchestrator:court, ui:jeton-suffisamment-long-01, mal-forme")
    s = Settings()
    assert s.legacy_orchestrator is False
    assert s.service_tokens == {"ui": "jeton-suffisamment-long-01"}  # jetons < 16 caractères ignorés


def test_legacy_mode_accepts_current_orchestrator_and_marks_it_unauthenticated(client, reference_db,
                                                                                monkeypatch):
    from services.catalog.config import settings
    monkeypatch.setattr(settings, "legacy_orchestrator", True)
    r = client.post("/decisions", json=LEGACY_BODY)
    assert r.status_code == 201 and r.json()["status"] == "recorded"
    assert r.json()["recorded_by_client"] == catalog_auth.LEGACY_CLIENT
    assert rows(reference_db) == [(catalog_auth.LEGACY_CLIENT, settings.legacy_decided_by, "approved")]
    # un jeton présenté mais invalide reste refusé, même en mode legacy
    assert client.post("/decisions", json=LEGACY_BODY,
                       headers={"Authorization": "Bearer faux-jeton-0123456789"}).status_code == 401


# ---------- H4 : idempotence ----------

def test_same_key_same_request_is_replayed_without_duplicate(client, reference_db):
    h = {**AUTH, "Idempotency-Key": "act-1"}
    first, second = client.post("/decisions", json=decision_body(), headers=h), \
        client.post("/decisions", json=decision_body(), headers=h)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["decision_id"] == second.json()["decision_id"] and second.json()["idempotent_replay"]
    assert len(rows(reference_db)) == 1
    assert rows(reference_db, "SELECT count(*) FROM catalog.decision_events") == [(1,)]


@pytest.mark.parametrize("change", [{"reason": "Autre motif tout à fait différent"}, {"decision": "refused"},
                                    {"decided_by": "quelqu'un d'autre"}, {"replacement_id": "S204"},
                                    {"action": "remove", "replacement_id": None}, {"product_id": "P105"}])
def test_same_key_with_any_significant_change_is_a_conflict(client, reference_db, change):
    h = {**AUTH, "Idempotency-Key": "act-2"}
    assert client.post("/decisions", json=decision_body(), headers=h).status_code == 201
    r = client.post("/decisions", json=decision_body(**change), headers=h)
    assert r.status_code == 409
    assert len(rows(reference_db)) == 1


def test_keys_are_scoped_per_authenticated_client(client, reference_db):
    assert client.post("/decisions", json=decision_body(), headers={**AUTH, "Idempotency-Key": "k"}).status_code == 201
    assert client.post("/decisions", json=decision_body(),
                       headers={**OTHER_AUTH, "Idempotency-Key": "k"}).status_code == 201
    assert len(rows(reference_db)) == 2


@pytest.mark.parametrize("headers, body", [
    ({"Idempotency-Key": "bad key!"}, {}),
    ({"Idempotency-Key": "k" * 101}, {}),
    ({"Idempotency-Key": "a"}, {"idempotency_key": "b"}),
])
def test_invalid_or_ambiguous_idempotency_keys_are_rejected(client, reference_db, headers, body):
    assert client.post("/decisions", json={**decision_body(), **body}, headers={**AUTH, **headers}).status_code == 422
    assert rows(reference_db) == []


def test_concurrent_retries_create_exactly_one_decision(client, reference_db):
    def post(_):
        return TestClient(app).post("/decisions", json=decision_body(), headers={**AUTH, "Idempotency-Key": "dbl"})

    with ThreadPoolExecutor(8) as pool:
        codes = sorted(r.status_code for r in pool.map(post, range(8)))
    assert codes == [200] * 7 + [201]
    assert len(rows(reference_db)) == 1
    assert rows(reference_db, "SELECT count(*) FROM catalog.decision_events") == [(1,)]


def test_transition_replay_requires_same_target_and_same_decider(client):
    did = client.post("/decisions", json=decision_body(decision="pending", decided_by=None), headers=auth()).json()["decision_id"]
    assert client.post(f"/decisions/{did}/approve", json={"decided_by": "manal"}, headers=auth()).status_code == 200
    replay = client.post(f"/decisions/{did}/approve", json={"decided_by": "manal"}, headers=auth())
    assert replay.status_code == 200 and replay.json()["idempotent_replay"]
    assert client.post(f"/decisions/{did}/approve", json={"decided_by": "autre"}, headers=auth()).status_code == 409
    assert client.post(f"/decisions/{did}/refuse", json={"decided_by": "manal"}, headers=auth()).status_code == 409
    assert len(client.get(f"/decisions/{did}").json()["events"]) == 2


def test_legacy_mode_never_allows_unauthenticated_transitions(client, monkeypatch):
    """Le mode legacy ne couvre que l'appel POST /decisions de l'orchestrateur actuel."""
    from services.catalog.config import settings
    did = client.post("/decisions", json=decision_body(decision="pending", decided_by=None), headers=auth()).json()["decision_id"]
    monkeypatch.setattr(settings, "legacy_orchestrator", True)
    for verb in ("approve", "refuse", "cancel"):
        assert client.post(f"/decisions/{did}/{verb}", json={"decided_by": "intrus"}).status_code == 401
    assert client.get(f"/decisions/{did}").json()["decision_status"] == "pending"


def test_duplicate_or_reserved_client_tokens_are_rejected(monkeypatch):
    monkeypatch.setenv("CATALOG_SERVICE_TOKENS", "orchestrator:jeton-partage-0123456789,ui:jeton-partage-0123456789,"
                                                 "seed:jeton-seed-0123456789ab,legacy-unauthenticated:jeton-x-0123456789,"
                                                 "avant-002:jeton-y-0123456789ab,analysis:jeton-analysis-012345")
    assert Settings().service_tokens == {"analysis": "jeton-analysis-012345"}


def test_unauthenticated_request_is_rejected_before_any_database_access(client, monkeypatch):
    from services.catalog.config import settings
    monkeypatch.setattr(settings, "database_url", "postgresql://x:y@127.0.0.1:1/none")
    assert client.post("/decisions", json=decision_body()).status_code == 401
    assert client.post("/decisions/1/approve", json={"decided_by": "manal"}).status_code == 401


def test_approving_a_decision_that_was_never_pending_is_a_conflict(client):
    did = client.post("/decisions", json=decision_body(), headers=auth()).json()["decision_id"]  # créée approuvée
    r = client.post(f"/decisions/{did}/approve", json={"decided_by": "manal"}, headers=auth())
    assert r.status_code == 409


# ---------- revue finale : clé obligatoire, mode legacy strict, rejeux ----------

def test_authenticated_write_requires_an_idempotency_key(client, reference_db):
    r = client.post("/decisions", json=decision_body(), headers=AUTH)
    assert r.status_code == 400 and "Idempotency-Key" in r.json()["detail"]
    assert rows(reference_db) == []


@pytest.fixture
def legacy(client, monkeypatch):
    from services.catalog.config import settings
    monkeypatch.setattr(settings, "legacy_orchestrator", True)
    return settings


@pytest.mark.parametrize("extra, headers", [
    ({"decision": "refused"}, {}), ({"decision": "approved"}, {}), ({"decided_by": "directeur"}, {}),
    ({"idempotency_key": "k1"}, {}), ({}, {"Idempotency-Key": "k2"}),
])
def test_legacy_mode_only_accepts_the_exact_current_orchestrator_call(client, reference_db, legacy, extra, headers):
    r = client.post("/decisions", json={**LEGACY_BODY, **extra}, headers=headers)
    assert r.status_code == 422
    assert rows(reference_db) == []


def test_legacy_decision_is_marked_with_its_own_source(client, reference_db, legacy):
    created = client.post("/decisions", json=LEGACY_BODY).json()
    assert (created["source"], created["decision_status"], created["decided_by"]) == \
           ("legacy", "approved", legacy.legacy_decided_by)


def test_legacy_retries_are_deduplicated(client, reference_db, legacy):
    first, retry = client.post("/decisions", json=LEGACY_BODY), client.post("/decisions", json=LEGACY_BODY)
    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json()["decision_id"] == first.json()["decision_id"] and retry.json()["idempotent_replay"]
    assert client.post("/decisions", json={**LEGACY_BODY, "reason": "Autre motif"}).status_code == 201
    assert len(rows(reference_db)) == 2


def test_concurrent_legacy_retries_create_one_decision(client, reference_db, legacy):
    with ThreadPoolExecutor(6) as pool:
        codes = sorted(r.status_code for r in pool.map(lambda _: TestClient(app).post("/decisions", json=LEGACY_BODY),
                                                       range(6)))
    assert codes == [200] * 5 + [201]
    assert len(rows(reference_db)) == 1


def test_legacy_deduplication_window_expires(client, reference_db, legacy):
    client.post("/decisions", json=LEGACY_BODY)
    with psycopg.connect(reference_db) as c:
        c.execute("UPDATE catalog.decisions SET created_at = now() - interval '10 minutes'")
    assert client.post("/decisions", json=LEGACY_BODY).status_code == 201


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_legacy_decider_falls_back_to_default(monkeypatch, value):
    monkeypatch.setenv("LEGACY_DECIDED_BY", value)
    assert Settings().legacy_decided_by == "inconnu (orchestrateur non authentifié)"


def test_health_and_startup_expose_legacy_mode(client, legacy, caplog):
    assert client.get("/health").json()["legacy_mode"] is True
    with caplog.at_level("WARNING"), TestClient(app):
        pass
    assert any("NON SÛR" in r.getMessage() for r in caplog.records)


def test_health_reports_secure_mode_by_default(client):
    assert client.get("/health").json()["legacy_mode"] is False


def test_pending_proposal_rejects_a_decider(client):
    assert client.post("/decisions", json=decision_body(decision="pending", decided_by="manal"),
                       headers=auth()).status_code == 422


def test_transition_replay_by_another_client_is_a_conflict(client):
    did = client.post("/decisions", json=decision_body(decision="pending", decided_by=None),
                      headers=auth()).json()["decision_id"]
    assert client.post(f"/decisions/{did}/approve", json={"decided_by": "manal"}, headers=auth()).status_code == 200
    r = client.post(f"/decisions/{did}/approve", json={"decided_by": "manal"}, headers=auth(OTHER_TOKEN))
    assert r.status_code == 409


def test_legacy_mode_rejects_unknown_fields(client, reference_db, legacy):
    r = client.post("/decisions", json={**LEGACY_BODY, "approved": True, "status": "refused"})
    assert r.status_code == 422 and rows(reference_db) == []


def test_missing_key_is_reported_before_other_validation_errors(client):
    body = {k: v for k, v in decision_body().items() if k != "decision"}
    assert client.post("/decisions", json=body, headers=AUTH).status_code == 400
    assert "400" in client.get("/openapi.json").json()["paths"]["/decisions"]["post"]["responses"]


def test_malformed_token_entry_is_logged_without_its_value(caplog):
    from services.catalog.config import parse_service_tokens
    with caplog.at_level("WARNING"):
        tokens = parse_service_tokens("orchestrator:jeton-correct-0123456789, valeursecretesansseparateur123456")
    assert tokens == {"orchestrator": "jeton-correct-0123456789"}
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "valeursecrete" not in logged and "entrée 2" in logged
