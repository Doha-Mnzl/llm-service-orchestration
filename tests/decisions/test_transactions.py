"""H3 : une réponse de succès n'est envoyée qu'après le COMMIT ; décision et audit sont atomiques.

Les échecs sont provoqués par des déclencheurs installés uniquement dans la base de test :
- un déclencheur DIFFÉRÉ (DEFERRABLE INITIALLY DEFERRED) qui n'échoue qu'au moment du COMMIT ;
- un déclencheur sur le journal d'audit qui fait échouer l'insertion de l'événement.
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

from services.catalog.main import app
from tests.decisions.helpers import auth, decision_body

pytestmark = pytest.mark.db

TRIGGERS = """
CREATE OR REPLACE FUNCTION catalog.test_fail() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'échec simulé (%)', TG_NAME; END $$;
CREATE CONSTRAINT TRIGGER test_fail_on_commit AFTER INSERT OR UPDATE ON catalog.decisions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    WHEN (NEW.reason LIKE '%ECHEC_COMMIT%' AND NEW.status <> 'pending')
    EXECUTE FUNCTION catalog.test_fail();
CREATE FUNCTION catalog.test_fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT reason FROM catalog.decisions WHERE decision_id = NEW.decision_id) LIKE '%ECHEC_AUDIT%' THEN
        RAISE EXCEPTION 'échec simulé du journal d''audit';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER test_fail_audit BEFORE INSERT ON catalog.decision_events
    FOR EACH ROW EXECUTE FUNCTION catalog.test_fail_audit();
"""
DROP = """
DROP TRIGGER IF EXISTS test_fail_on_commit ON catalog.decisions;
DROP TRIGGER IF EXISTS test_fail_audit ON catalog.decision_events;
DROP FUNCTION IF EXISTS catalog.test_fail();
DROP FUNCTION IF EXISTS catalog.test_fail_audit();
"""


@pytest.fixture
def client(reference_db, point_services_to, service_tokens):
    with psycopg.connect(reference_db, autocommit=True) as c:
        c.execute("TRUNCATE catalog.decision_events, catalog.decisions RESTART IDENTITY")
        c.execute(DROP)
        c.execute(TRIGGERS)
    point_services_to(reference_db)
    yield TestClient(app, raise_server_exceptions=False)
    with psycopg.connect(reference_db, autocommit=True) as c:
        c.execute(DROP)


def _count(url):
    with psycopg.connect(url) as c:
        return (c.execute("SELECT count(*) FROM catalog.decisions").fetchone()[0],
                c.execute("SELECT count(*) FROM catalog.decision_events").fetchone()[0])


def test_commit_failure_never_returns_success(client, reference_db):
    r = client.post("/decisions", json=decision_body(reason="ECHEC_COMMIT au moment du commit"), headers=auth())
    assert r.status_code == 500 and "annulée" in r.json()["detail"]
    assert _count(reference_db) == (0, 0)


def test_audit_failure_rolls_back_the_decision(client, reference_db):
    r = client.post("/decisions", json=decision_body(reason="ECHEC_AUDIT du journal"), headers=auth())
    assert r.status_code == 500
    assert _count(reference_db) == (0, 0)


def test_transition_commit_failure_keeps_proposal_pending(client, reference_db):
    created = client.post("/decisions", json=decision_body(decision="pending", decided_by=None, reason="proposition normale"),
                          headers=auth()).json()
    with psycopg.connect(reference_db, autocommit=True) as c:  # la transition échouera au commit
        c.execute("UPDATE catalog.decisions SET reason = 'ECHEC_COMMIT plus tard' WHERE decision_id = %s",
                  (created["decision_id"],))
    r = client.post(f"/decisions/{created['decision_id']}/approve", json={"decided_by": "manal"}, headers=auth())
    assert r.status_code == 500
    after = client.get(f"/decisions/{created['decision_id']}").json()
    assert after["decision_status"] == "pending" and len(after["events"]) == 1


def test_success_is_durable_when_reported(client, reference_db):
    r = client.post("/decisions", json=decision_body(), headers=auth())
    assert r.status_code == 201
    # relu par une AUTRE connexion : la décision et son audit sont déjà commités
    assert _count(reference_db) == (1, 1)


# ---------- cause racine : la dépendance de connexion des 3 services ----------

@pytest.mark.parametrize("service", ["sales", "inventory", "catalog"])
def test_connection_dependency_never_reports_success_before_commit(service, migrated_db, monkeypatch):
    """Reproduit l'ancien schéma : écriture simple, dépendance à la portée par défaut (« request »).
    Avant correction, get_conn commitait APRÈS l'envoi de la réponse : 201 malgré l'échec du COMMIT."""
    import importlib

    from fastapi import Depends, FastAPI

    db = importlib.import_module(f"services.{service}.db")
    config = importlib.import_module(f"services.{service}.config")
    monkeypatch.setattr(config.settings, "database_url", migrated_db)
    with psycopg.connect(migrated_db, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS public.test_tx; CREATE TABLE public.test_tx (v TEXT)")
        c.execute("""CREATE OR REPLACE FUNCTION public.test_tx_fail() RETURNS trigger LANGUAGE plpgsql AS $$
                     BEGIN RAISE EXCEPTION 'échec au commit'; END $$;
                     CREATE CONSTRAINT TRIGGER test_tx_fail AFTER INSERT ON public.test_tx
                     DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.test_tx_fail()""")
    probe = FastAPI()
    for exc, handler in db.EXCEPTION_HANDLERS:
        probe.add_exception_handler(exc, handler)

    @probe.post("/write", status_code=201)
    def write(conn=Depends(db.get_conn)):
        conn.execute("INSERT INTO public.test_tx VALUES ('x')")
        return {"ok": True}

    try:
        r = TestClient(probe, raise_server_exceptions=False).post("/write")
        assert r.status_code == 500 and r.json() == {"detail": "erreur de base de données : opération annulée"}
        with psycopg.connect(migrated_db) as c:
            assert c.execute("SELECT count(*) FROM public.test_tx").fetchone()[0] == 0
    finally:
        with psycopg.connect(migrated_db, autocommit=True) as c:
            c.execute("DROP TABLE IF EXISTS public.test_tx; DROP FUNCTION IF EXISTS public.test_tx_fail()")
