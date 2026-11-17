"""Tests d'intégration de l'orchestrateur (faux LLM + faux services).

Lancer depuis la racine du dépôt :  pytest -v
"""

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import settings
from orchestrator.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_settings():
    settings.llm_provider = "fake"
    settings.use_mock_services = True
    settings.fail_services = set()
    yield
    settings.fail_services = set()


def chat(msg: str) -> dict:
    r = client.post("/chat", json={"message": msg})
    assert r.status_code == 200
    return r.json()


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_scenario_complet_retrait_remplacement():
    res = chat("Quels produits dois-je retirer ce mois-ci, et par quoi les remplacer ?")
    assert res["status"] == "ok"
    tools = [t["tool"] for t in res["trace"]]
    assert tools == ["find_underperforming_products", "get_stock_levels",
                     "find_replacements", "record_decision"]
    # La décision n'est PAS exécutée : elle attend la validation humaine.
    assert res["trace"][-1]["status"] == "pending_validation"
    assert len(res["pending_actions"]) == 1
    assert "validation" in res["answer"]


def test_validation_humaine_accepter_puis_refuser():
    action_id = chat("Quels produits retirer ?")["pending_actions"][0]["action_id"]
    r = client.post(f"/actions/{action_id}/decision", json={"approved": True})
    assert r.json()["status"] == "executed"
    # Une action ne peut être traitée qu'une fois.
    assert client.post(f"/actions/{action_id}/decision", json={"approved": True}).status_code == 404

    action_id = chat("Quels produits retirer ?")["pending_actions"][0]["action_id"]
    r = client.post(f"/actions/{action_id}/decision", json={"approved": False})
    assert r.json()["status"] == "refused"


def test_outil_non_autorise_refuse():
    res = chat("outil_interdit")
    assert res["trace"][0]["status"] == "rejected"
    assert "non autorisé" in res["trace"][0]["detail"]


def test_arguments_invalides_refuses():
    res = chat("args_invalides")
    assert res["trace"][0]["status"] == "rejected"
    assert "period_months" in res["trace"][0]["detail"]


def test_panne_stocks_reponse_partielle():
    settings.fail_services = {"inventory"}
    res = chat("Quels produits dois-je retirer ce mois-ci ?")
    assert res["status"] == "partial"
    stock = next(t for t in res["trace"] if t["tool"] == "get_stock_levels")
    assert stock["status"] == "failed" and stock["attempts"] == 1 + settings.max_retries
    assert res["warnings"]


def test_panne_analyse_arret_sans_invention():
    settings.fail_services = {"analysis"}
    res = chat("Quels produits dois-je retirer ce mois-ci ?")
    assert res["status"] == "stopped"
    assert "Aucune donnée n'a été inventée" in res["answer"]
    assert res["pending_actions"] == []


def test_llm_indisponible():
    settings.llm_provider = "groq"
    old_key = settings.groq_api_key
    settings.groq_api_key = ""
    try:
        res = chat("Quels produits retirer ?")
    finally:
        settings.groq_api_key = old_key
    assert res["status"] == "error"
    assert "LLM est indisponible" in res["answer"]


def test_question_stock():
    res = chat("Quels produits sont en rupture ?")
    assert [t["tool"] for t in res["trace"]] == ["get_stock_alerts"]
    assert res["status"] == "ok"
