"""H1 : aucune réinitialisation automatique ne peut effacer des décisions humaines."""

import psycopg
import pytest

from data import generate, migrate, verify
from tests.conftest import assert_test_database

pytestmark = pytest.mark.db

HUMAN_DECISION = """INSERT INTO catalog.decisions (product_id, action, reason, status, source, decided_by, decided_at,
                                                  recorded_by_client)
                    VALUES ('P001', 'keep', 'décision humaine réelle', 'approved', 'orchestrator', 'manal', now(),
                            'orchestrator') RETURNING decision_id"""


def _decisions(url):
    with psycopg.connect(url) as c:
        return c.execute("SELECT count(*) FROM catalog.decisions WHERE source <> 'seed'").fetchone()[0]


@pytest.fixture
def db_with_human_decision(migrated_db):
    generate.run(migrated_db, seed=42, reset=True, allow_decision_loss=True)
    with psycopg.connect(migrated_db) as c:
        c.execute(HUMAN_DECISION)
    return migrated_db


def test_reset_commands_refuse_to_erase_human_decisions(db_with_human_decision):
    with pytest.raises(migrate.MigrationError):
        migrate.reset(db_with_human_decision)
    with pytest.raises(generate.SeedError):
        generate.run(db_with_human_decision, seed=42, reset=True)
    assert migrate.main(["--database-url", db_with_human_decision, "--reset"]) == 1
    assert generate.main(["--database-url", db_with_human_decision, "--reset"]) == 1
    assert _decisions(db_with_human_decision) == 1


def test_restart_sequence_preserves_decisions_and_data(db_with_human_decision):
    """Commande de démarrage proposée pour Compose, rejouée comme après un redémarrage."""
    before = verify.fingerprint(db_with_human_decision)
    for _ in range(2):
        assert migrate.main(["--database-url", db_with_human_decision]) == 0
        assert generate.main(["--database-url", db_with_human_decision, "--if-empty"]) == 0
    assert _decisions(db_with_human_decision) == 1
    assert verify.fingerprint(db_with_human_decision) == before


def test_if_empty_seeds_a_blank_database(migrated_db):
    with psycopg.connect(migrated_db) as c:
        c.execute(f"TRUNCATE {', '.join(generate.TABLES)} RESTART IDENTITY CASCADE")
    assert generate.main(["--database-url", migrated_db, "--if-empty"]) == 0
    assert verify.check(migrated_db) == []


def test_explicit_opt_in_allows_reset(db_with_human_decision):
    generate.run(db_with_human_decision, seed=42, reset=True, allow_decision_loss=True)
    assert _decisions(db_with_human_decision) == 0


def test_seed_only_decisions_can_be_regenerated_without_opt_in(migrated_db):
    generate.run(migrated_db, seed=42, reset=True, allow_decision_loss=True)
    generate.run(migrated_db, seed=42, reset=True)  # uniquement des décisions de démonstration


@pytest.mark.parametrize("url", ["postgresql://lso:lso@localhost:5433/lso",
                                 "postgresql://lso:lso@localhost:5433/lso_dev"])
def test_test_suite_refuses_non_test_databases(url):
    with pytest.raises(pytest.UsageError):
        assert_test_database(url)


def _wait_until_blocked_on_a_lock(url, worker, timeout=60):
    """Synchronisation déterministe : attend qu'une autre session soit bloquée sur un verrou."""
    import time
    deadline = time.monotonic() + timeout
    with psycopg.connect(url, autocommit=True) as c:
        while time.monotonic() < deadline:
            if not worker.is_alive():
                return
            if c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                         "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()").fetchone()[0]:
                return
            time.sleep(0.05)
    raise AssertionError("la réinitialisation n'a jamais attendu de verrou")


@pytest.mark.parametrize("reset", ["generate", "migrate"])
def test_decision_committed_during_reset_is_never_erased(db_with_human_decision, reset):
    """Course compte → effacement : une décision insérée pendant la réinitialisation doit bloquer celle-ci."""
    import threading
    import time

    url = db_with_human_decision
    with psycopg.connect(url) as c:
        c.execute("DELETE FROM catalog.decisions WHERE source <> 'seed'")  # décision sans événement d'audit
    writer = psycopg.connect(url)
    writer.execute(HUMAN_DECISION)                     # transaction ouverte, non validée
    outcome = {}

    def run_reset():
        try:
            if reset == "generate":
                generate.run(url, seed=42, reset=True)
            else:
                migrate.reset(url)
            outcome["result"] = "effacé"
        except (generate.SeedError, migrate.MigrationError) as e:
            outcome["result"] = type(e).__name__

    worker = threading.Thread(target=run_reset)
    worker.start()
    _wait_until_blocked_on_a_lock(url, worker)         # la réinitialisation attend un verrou (LOCK ou TRUNCATE)
    writer.commit()
    writer.close()
    worker.join(30)
    assert outcome["result"] in ("SeedError", "MigrationError")
    assert _decisions(url) == 1


@pytest.mark.parametrize("name", ["lso_latest", "contest_prod", "attestation"])
def test_test_database_guard_matches_a_whole_word(name):
    with pytest.raises(pytest.UsageError):
        assert_test_database(f"postgresql://lso:lso@localhost:5433/{name}")


@pytest.mark.parametrize("name", ["lso_test", "test_lso", "lso-test-2"])
def test_test_database_guard_accepts_test_names(name):
    assert_test_database(f"postgresql://lso:lso@localhost:5433/{name}")
