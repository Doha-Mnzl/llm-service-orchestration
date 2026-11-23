"""Garde des tests d'intégration « live » : jamais les services de démonstration ni `lso_dev`.

- LIVE_SERVICES=1 active ces tests (sinon : ignorés) ;
- les URL doivent être EXPLICITES (aucune valeur par défaut) et ne pas viser les ports de la démo
  existante (8000–8003, 8010, 8011, 8501, 8502) ;
- LIVE_STACK_DATABASE_URL désigne la base de la pile ; son nom doit contenir le mot « test » ;
- preuve « canari » : une catégorie temporaire écrite dans cette base doit être lue par le catalogue,
  sinon la pile n'utilise pas la base annoncée et les tests s'arrêtent avant toute écriture.
"""

import os
import uuid
from urllib.parse import urlparse

import httpx
import psycopg
import pytest

from tests.conftest import assert_test_database

DEMO_PORTS = {8000, 8001, 8002, 8003, 8010, 8011, 8501, 8502}
REQUIRED = ("LIVE_SALES_URL", "LIVE_INVENTORY_URL", "LIVE_CATALOG_URL", "LIVE_STACK_DATABASE_URL")
OPTIONAL_URLS = ("LIVE_LEGACY_CATALOG_URL", "LIVE_ORCHESTRATOR_URL")


def live_config(env=None) -> dict:
    env = os.environ if env is None else env
    if env.get("LIVE_SERVICES") != "1":
        pytest.skip("tests d'intégration live désactivés (LIVE_SERVICES=1 requis)")
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise pytest.UsageError(f"LIVE_SERVICES=1 sans {', '.join(missing)} : aucune valeur par défaut "
                                "n'est utilisée, pour ne jamais viser les services de démonstration")
    for key in (*REQUIRED[:3], *OPTIONAL_URLS):
        if env.get(key) and urlparse(env[key]).port in DEMO_PORTS:
            raise pytest.UsageError(f"{key}={env[key]} vise un port de la démonstration existante : "
                                    "utiliser une pile isolée")
    assert_test_database(env["LIVE_STACK_DATABASE_URL"], "LIVE_STACK_DATABASE_URL")
    return {k: env.get(k) for k in (*REQUIRED, *OPTIONAL_URLS, "LIVE_CATALOG_TOKEN", "ORCHESTRATOR_SRC")}


def assert_catalog_uses_database(catalog_url: str, database_url: str) -> None:
    canary = f"zz-canari-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(database_url, autocommit=True) as c:
        c.execute("INSERT INTO catalog.categories VALUES (%s, 'canari de test')", (canary,))
        try:
            names = {cat["name"] for cat in httpx.get(f"{catalog_url}/catalog/categories", timeout=10).json()}
        finally:
            c.execute("DELETE FROM catalog.categories WHERE name = %s", (canary,))
    if canary not in names:
        raise pytest.UsageError(f"{catalog_url} n'utilise pas la base LIVE_STACK_DATABASE_URL : arrêt avant "
                                "toute écriture")


def assert_executor_uses_database(executor, tools, database_url: str) -> None:
    """Preuve que l'exécuteur de l'orchestrateur (en mémoire) interroge la pile isolée AVANT toute écriture :
    une référence fournisseur temporaire, présente seulement dans cette base, doit remonter par son outil."""
    import asyncio
    import random

    sid = f"S9{random.randint(1000, 9999)}"
    with psycopg.connect(database_url, autocommit=True) as c:
        c.execute("INSERT INTO catalog.supplier_products (supplier_product_id, supplier_id, name, category, brand, "
                  "cost, price, availability, stock_available, lead_time_days) "
                  "SELECT %s, supplier_id, 'Canari exécuteur', 'câbles', 'Test', 1, 1, 'in_stock', 1, 1 "
                  "FROM catalog.suppliers ORDER BY 1 LIMIT 1", (sid,))
        try:
            tool = tools.TOOLS["search_supplier_catalog"]
            result, _ = asyncio.run(executor.execute(tool, {"category": "câbles", "query": "canari"}))
        finally:
            c.execute("DELETE FROM catalog.supplier_products WHERE supplier_product_id = %s", (sid,))
    if sid not in {i["supplier_product_id"] for i in result.get("items", [])}:
        raise pytest.UsageError("l'exécuteur de l'orchestrateur n'interroge pas la pile isolée : arrêt")
