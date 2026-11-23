"""Fixtures communes de la Partie 3.

Les tests marqués `db` utilisent une base PostgreSQL DÉDIÉE AUX TESTS, désignée par
TEST_DATABASE_URL (elle est réinitialisée). Sans cette variable, ils sont ignorés avec
un message explicite, pour ne pas casser le `pytest` des autres membres de l'équipe.

    docker run -d --name lso-p3-postgres -e POSTGRES_USER=lso -e POSTGRES_PASSWORD=lso \
        -e POSTGRES_DB=lso_test -p 127.0.0.1:5433:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://lso:lso@localhost:5433/lso_test pytest

Garde-fou : le nom de la base doit contenir « test », sinon la session s'arrête avant toute
écriture (une base de démonstration ou de production ne peut pas être effacée par erreur).
"""

import os
import re

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def assert_test_database(url: str, variable: str = "TEST_DATABASE_URL") -> None:
    """Refuse toute base dont le nom ne contient pas « test »."""
    from psycopg.conninfo import conninfo_to_dict

    name = conninfo_to_dict(url).get("dbname") or ""
    if not re.search(r"(^|[_-])test([_-]|$)", name.lower()):
        raise pytest.UsageError(f"{variable} désigne la base « {name} » : les tests effacent leur base, "
                                "utilisez une base dédiée dont le nom contient « test » (ex. lso_test).")


def pytest_configure(config):
    config.addinivalue_line("markers", "db: test nécessitant PostgreSQL (TEST_DATABASE_URL)")
    config.addinivalue_line("markers", "integration: test d'intégration avec un autre composant")


def pytest_collection_modifyitems(config, items):
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL non défini : tests PostgreSQL ignorés")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def db_url() -> str:
    assert_test_database(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def migrated_db(db_url) -> str:
    """Base de TEST réinitialisée puis migrée une fois pour la session."""
    from data import migrate

    migrate.reset(db_url, allow_decision_loss=True)
    migrate.migrate(db_url)
    return db_url


@pytest.fixture
def conn(migrated_db):
    """Connexion en transaction annulée à la fin du test."""
    import psycopg

    with psycopg.connect(migrated_db) as c:
        yield c
        c.rollback()


@pytest.fixture(scope="module")
def reference_db(migrated_db) -> str:
    """Jeu de référence minimal, aux valeurs calculables à la main."""
    from tests import reference_data

    reference_data.load(migrated_db)
    return migrated_db


@pytest.fixture(scope="module")
def seeded_db(migrated_db) -> str:
    """Jeu complet produit par le générateur (graine 42)."""
    from data import generate

    generate.run(migrated_db, seed=42, reset=True, allow_decision_loss=True)
    return migrated_db


@pytest.fixture
def point_services_to(monkeypatch):
    """Fait pointer les trois services sur une base donnée."""

    def _point(url: str) -> None:
        from services.catalog.config import settings as cs
        from services.inventory.config import settings as is_
        from services.sales.config import settings as ss

        for s in (ss, is_, cs):
            monkeypatch.setattr(s, "database_url", url)

    return _point


@pytest.fixture
def service_tokens(monkeypatch):
    """Jetons de service du catalogue pour les tests (mode sûr, sans compatibilité legacy)."""
    from services.catalog.config import settings
    from tests.decisions.helpers import OTHER_TOKEN, TOKEN

    monkeypatch.setattr(settings, "service_tokens", {"orchestrator": TOKEN, "client-test": OTHER_TOKEN},
                        raising=False)
    monkeypatch.setattr(settings, "legacy_orchestrator", False, raising=False)
    return settings
