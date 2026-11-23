"""Intégration Partie 2 ↔ Partie 3 avec les VRAIS services, sur une pile ISOLÉE (LSO-14).

Configuration obligatoire, sans valeur par défaut (voir tests/integration/live.py) :
LIVE_SERVICES=1, LIVE_SALES_URL, LIVE_INVENTORY_URL, LIVE_CATALOG_URL (catalogue en MODE SÛR),
LIVE_STACK_DATABASE_URL (base de la pile, nom contenant « test ») ; les tests de chat via l'API HTTP de
l'orchestrateur sont en LECTURE seule ; les écritures passent par son exécuteur, dont la cible est prouvée ; facultatif : LIVE_CATALOG_TOKEN,
LIVE_LEGACY_CATALOG_URL (catalogue avec DECISIONS_LEGACY_ORCHESTRATOR=true), LIVE_ORCHESTRATOR_URL
(orchestrateur PR #1, USE_MOCK_SERVICES=false LLM_PROVIDER=fake), ORCHESTRATOR_SRC (paquet
`orchestrator` de la PR #1, réutilisé SANS modification). Les ports de la démo existante et
`lso_dev` sont refusés ; une preuve canari vérifie la base réellement utilisée avant toute écriture.
"""

import asyncio
import sys
import uuid

import httpx
import pytest

from tests.integration.live import assert_catalog_uses_database, assert_executor_uses_database, live_config

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live():
    cfg = live_config()
    assert_catalog_uses_database(cfg["LIVE_CATALOG_URL"], cfg["LIVE_STACK_DATABASE_URL"])
    if cfg["LIVE_LEGACY_CATALOG_URL"]:
        assert_catalog_uses_database(cfg["LIVE_LEGACY_CATALOG_URL"], cfg["LIVE_STACK_DATABASE_URL"])
    return cfg


@pytest.fixture(scope="module")
def orch(live):
    if not live["ORCHESTRATOR_SRC"]:
        pytest.skip("ORCHESTRATOR_SRC requis (paquet orchestrator de la PR #1)")
    sys.path.insert(0, live["ORCHESTRATOR_SRC"])
    from orchestrator import executor, mock_services, tools
    from orchestrator.config import settings
    settings.use_mock_services = False
    settings.service_urls = {"sales": live["LIVE_SALES_URL"], "inventory": live["LIVE_INVENTORY_URL"],
                             "catalog": live["LIVE_CATALOG_URL"]}
    settings.fail_services = set()
    assert_executor_uses_database(executor, tools, live["LIVE_STACK_DATABASE_URL"])
    return executor, mock_services, tools


SAMPLE_ARGS = {
    "get_sales_summary": {"period_months": 1, "category": "câbles"},
    "get_sales_trend": {"product_id": "P003", "months": 6},
    "get_stock_levels": {"product_ids": ["P003", "P005"]},
    "get_stock_alerts": {"kind": "all"},
    "search_supplier_catalog": {"category": "câbles", "query": "usb", "max_price": 200},
}


NULLABLE = {"margin_rate", "trend_6m", "revenue_change", "coverage_days", "quantity"}  # null documenté


def _scalar_issues(live, mock, path: str) -> list[str]:
    key = path.rsplit(".", 1)[-1].removesuffix("[]")
    if live is None:
        return [] if key in NULLABLE or mock is None else [f"{path} : null inattendu"]
    if isinstance(mock, bool):
        ok = isinstance(live, bool)
    elif isinstance(mock, (int, float)):
        ok = isinstance(live, (int, float)) and not isinstance(live, bool)
    elif isinstance(mock, str):
        ok = isinstance(live, str)
    else:
        ok = True
    return [] if ok else [f"{path} : {type(live).__name__} au lieu de {type(mock).__name__}"]


def _contract_issues(live, mock, path: str = "") -> list[str]:
    """Écarts entre une réponse réelle et le format des mocks de la PR #1 : champs absents, listes vides
    alors que le jeu de référence en contient, types scalaires différents (revue Codex)."""
    if isinstance(mock, dict):
        if not isinstance(live, dict):
            return [f"{path or 'racine'} : objet attendu"]
        issues = []
        for k, v in mock.items():
            sub = f"{path}.{k}" if path else k
            issues += [f"{sub} absent"] if k not in live else _contract_issues(live[k], v, sub)
        return issues
    if isinstance(mock, list):
        if not isinstance(live, list):
            return [f"{path} : liste attendue"]
        if mock and not live:
            return [f"{path} : liste vide alors que le jeu de référence en contient"]
        if not mock:
            return []
        if isinstance(mock[0], dict):  # forme attendue = union des éléments du mock (premier type non nul)
            expected: dict = {}
            for item in mock:
                for k, v in item.items():
                    if expected.get(k) is None:
                        expected[k] = v
            return [i for item in live for i in _contract_issues(item, expected, f"{path}[]")]
        return [i for item in live for i in _scalar_issues(item, mock[0], f"{path}[]")]
    return _scalar_issues(live, mock, path)


def test_contract_checker_uses_every_mock_item():
    mock = {"stockout": [{"product_id": "P006", "since": None}, {"product_id": "P050", "since": "2026-09-20"}]}
    assert _contract_issues({"stockout": [{"product_id": "P006", "since": 123}]}, mock) == \
        ["stockout[].since : int au lieu de str"]


def test_contract_checker_rejects_empty_lists_and_wrong_types():
    mock = {"items": [{"product_id": "P003", "revenue": 126.0, "trend_6m": -0.6}]}
    assert _contract_issues({"items": []}, mock) == ["items : liste vide alors que le jeu de référence en contient"]
    assert _contract_issues({"items": [{"product_id": 3, "revenue": "126", "trend_6m": None}]}, mock) == \
        ["items[].product_id : int au lieu de str", "items[].revenue : str au lieu de float"]


@pytest.mark.parametrize("tool_name", list(SAMPLE_ARGS))
def test_every_http_tool_works_on_real_services_with_mock_format(orch, tool_name):
    executor, mock_services, tools = orch
    tool = tools.TOOLS[tool_name]
    args = tool.args_model(**SAMPLE_ARGS[tool_name]).model_dump(exclude_none=True)  # validation de Doha
    result, attempts = asyncio.run(executor.execute(tool, args))
    assert attempts == 1
    expected = mock_services.HANDLERS[tool_name](**args)
    assert _contract_issues(result, expected) == []


DECISION_ARGS = dict(product_id="P005", action="replace", replacement_id="S104",
                     reason="Test d'intégration : ventes en forte baisse")


def test_secure_catalog_rejects_current_orchestrator_call(orch):
    """H5 : l'orchestrateur actuel n'envoie ni jeton ni décision explicite ⇒ 401, rien n'est enregistré."""
    executor, _, tools = orch
    tool = tools.TOOLS["record_decision"]
    with pytest.raises(httpx.HTTPStatusError) as e:
        asyncio.run(executor.execute(tool, tool.args_model(**DECISION_ARGS).model_dump(exclude_none=True)))
    assert e.value.response.status_code == 401


def test_legacy_catalog_accepts_current_orchestrator_and_marks_it(orch, live):
    if not live["LIVE_LEGACY_CATALOG_URL"]:
        pytest.skip("LIVE_LEGACY_CATALOG_URL requis")
    executor, _, tools = orch
    from orchestrator.config import settings
    settings.service_urls = {**settings.service_urls, "catalog": live["LIVE_LEGACY_CATALOG_URL"]}
    try:
        tool = tools.TOOLS["record_decision"]
        args = tool.args_model(**{**DECISION_ARGS, "reason": f"Intégration legacy {uuid.uuid4().hex[:6]}"})
        result, _ = asyncio.run(executor.execute(tool, args.model_dump(exclude_none=True)))
        replay, _ = asyncio.run(executor.execute(tool, args.model_dump(exclude_none=True)))  # reprise réseau
    finally:
        settings.service_urls = {**settings.service_urls, "catalog": live["LIVE_CATALOG_URL"]}
    assert result["status"] == "recorded" and result["recorded_by_client"] == "legacy-unauthenticated"
    assert result["source"] == "legacy" and replay["decision_id"] == result["decision_id"]


def test_target_contract_network_retry_creates_one_decision(live):
    """Contrat cible proposé à Doha : jeton + décision explicite + Idempotency-Key = action_id."""
    if not live["LIVE_CATALOG_TOKEN"]:
        pytest.skip("LIVE_CATALOG_TOKEN requis")
    catalog, token = live["LIVE_CATALOG_URL"], live["LIVE_CATALOG_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": f"action-{uuid.uuid4().hex[:8]}"}
    body = {**DECISION_ARGS, "decision": "approved", "decided_by": "manal"}
    first = httpx.post(f"{catalog}/decisions", json=body, headers=headers, timeout=10)
    retry = httpx.post(f"{catalog}/decisions", json=body, headers=headers, timeout=10)  # réponse « perdue »
    assert (first.status_code, retry.status_code) == (201, 200)
    assert first.json()["decision_id"] == retry.json()["decision_id"]
    changed = httpx.post(f"{catalog}/decisions", json={**body, "decision": "refused"}, headers=headers, timeout=10)
    assert changed.status_code == 409
    no_key = httpx.post(f"{catalog}/decisions", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert no_key.status_code == 400


@pytest.mark.xfail(strict=True, reason="PR #1 (Doha) : agent.decide() ne restaure l'action que sur "
                   "ServiceUnavailable ; un refus HTTP du catalogue (401) donne un 500 et la proposition disparaît. "
                   "Contrat cible : docs/partie3/decisions-equipe.md. Ce test passera (XPASS ⇒ à réactiver) "
                   "quand la PR #1 sera corrigée.")
def test_rejected_catalog_write_keeps_the_proposal_pending(orch, live):
    """Régression reproduite par Codex, à la frontière /actions/{id}/decision de l'orchestrateur (en mémoire)."""
    from fastapi.testclient import TestClient
    from orchestrator import agent, main
    action = agent.PendingAction(action_id="regression401", tool="record_decision", args=dict(DECISION_ARGS))
    agent.PENDING[action.action_id] = action
    try:
        r = TestClient(main.app, raise_server_exceptions=False).post(
            f"/actions/{action.action_id}/decision", json={"approved": True})
        assert r.status_code != 500, "refus du catalogue transformé en erreur interne"
        assert action.action_id in agent.PENDING, "proposition perdue après le refus du catalogue"
    finally:
        agent.PENDING.pop(action.action_id, None)


def test_unknown_product_is_a_business_error_not_an_outage(orch):
    executor, _, tools = orch
    tool = tools.TOOLS["get_sales_trend"]
    with pytest.raises(httpx.HTTPStatusError) as e:  # 404 : pas de reprise, transmis au LLM
        asyncio.run(executor.execute(tool, {"product_id": "P999", "months": 6}))
    assert e.value.response.status_code == 404


# ---------- de bout en bout via l'API de l'orchestrateur ----------

@pytest.fixture(scope="module")
def chat(live):
    if not live["LIVE_ORCHESTRATOR_URL"]:
        pytest.skip("LIVE_ORCHESTRATOR_URL requis")

    def _chat(message: str) -> dict:
        r = httpx.post(f"{live['LIVE_ORCHESTRATOR_URL']}/chat", json={"message": message}, timeout=60)
        assert r.status_code == 200
        return r.json()
    return _chat


def test_orchestrator_sees_real_services_up(live):
    if not live["LIVE_ORCHESTRATOR_URL"]:
        pytest.skip("LIVE_ORCHESTRATOR_URL requis")
    health = httpx.get(f"{live['LIVE_ORCHESTRATOR_URL']}/services/health", timeout=10).json()
    assert {health[s] for s in ("sales", "inventory", "catalog")} == {"up"}


@pytest.mark.parametrize("question, tool", [
    ("Quels produits sont en rupture ?", "get_stock_alerts"),
    ("Quelles sont les ventes du mois ?", "get_sales_summary"),
    ("Cherche dans le catalogue fournisseur", "search_supplier_catalog"),
])
def test_questions_answered_from_real_services(chat, question, tool):
    res = chat(question)
    assert res["status"] == "ok"
    assert [(t["tool"], t["status"]) for t in res["trace"]] == [(tool, "ok")]


def test_seeded_scenarios_are_visible_through_real_apis(live):
    alerts = httpx.get(f"{live['LIVE_INVENTORY_URL']}/stocks/alerts", timeout=10).json()
    assert {"P006", "P050"} <= {a["product_id"] for a in alerts["stockout"]}
    assert {"P003", "P005", "P042", "P013"} <= {a["product_id"] for a in alerts["overstock"]}
    worst = httpx.get(f"{live['LIVE_SALES_URL']}/sales/summary", params={"sort": "trend", "limit": 8}, timeout=10).json()
    assert {"P003", "P005", "P006"} <= {i["product_id"] for i in worst["items"]}


def test_replacement_scenario_stops_cleanly_until_analysis_is_connected(chat):
    """Bloqué par la Partie 1 : le worker Analysis (RabbitMQ) n'est pas encore branché."""
    res = chat("Quels produits dois-je retirer ce mois-ci ?")
    assert res["status"] == "stopped"
    assert res["trace"][0]["tool"] == "find_underperforming_products" and res["trace"][0]["status"] == "failed"
