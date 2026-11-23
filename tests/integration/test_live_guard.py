"""La garde des tests live refuse toute configuration qui pourrait viser la démo ou `lso_dev`."""

import pytest

from tests.integration.live import live_config

OK = {"LIVE_SERVICES": "1", "LIVE_SALES_URL": "http://localhost:28001", "LIVE_INVENTORY_URL": "http://localhost:28002",
      "LIVE_CATALOG_URL": "http://localhost:28003",
      "LIVE_STACK_DATABASE_URL": "postgresql://lso:lso@localhost:5433/lso_integration_test"}


def test_disabled_without_live_services():
    with pytest.raises(pytest.skip.Exception):
        live_config({})


@pytest.mark.parametrize("missing", ["LIVE_SALES_URL", "LIVE_CATALOG_URL", "LIVE_STACK_DATABASE_URL"])
def test_no_implicit_default_urls(missing):
    with pytest.raises(pytest.UsageError):
        live_config({k: v for k, v in OK.items() if k != missing})


@pytest.mark.parametrize("key, value", [
    ("LIVE_CATALOG_URL", "http://localhost:8003"), ("LIVE_SALES_URL", "http://127.0.0.1:8001"),
    ("LIVE_ORCHESTRATOR_URL", "http://localhost:8010"), ("LIVE_LEGACY_CATALOG_URL", "http://localhost:8002"),
])
def test_demo_ports_are_refused(key, value):
    with pytest.raises(pytest.UsageError):
        live_config({**OK, key: value})


@pytest.mark.parametrize("db", ["lso_dev", "lso", "lso_latest"])
def test_non_test_stack_database_is_refused(db):
    with pytest.raises(pytest.UsageError):
        live_config({**OK, "LIVE_STACK_DATABASE_URL": f"postgresql://lso:lso@localhost:5433/{db}"})


def test_valid_isolated_configuration_is_accepted():
    assert live_config(OK)["LIVE_CATALOG_URL"] == "http://localhost:28003"
