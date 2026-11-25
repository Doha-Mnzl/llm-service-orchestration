"""Tests d'intégration de l'orchestrateur (faux LLM + faux services).

Lancer depuis la racine du dépôt :  pytest -v
"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestrator import agent, executor, mock_services
from orchestrator.config import settings
from orchestrator.main import app
from orchestrator.tools import TOOLS

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_settings():
    settings.llm_provider = "fake"
    settings.use_mock_services = True
    settings.fail_services = set()
    settings.catalog_token = ""
    mock_services.REQUIRED_TOKEN = None
    yield
    settings.fail_services = set()
    settings.use_mock_services = True
    settings.catalog_token = ""
    mock_services.REQUIRED_TOKEN = None
    agent.PENDING.clear()


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


def decide(action_id: str, approved: bool, decided_by: str = "doha"):
    return client.post(f"/actions/{action_id}/decision", json={"approved": approved, "decided_by": decided_by})


def test_validation_humaine_accepter_puis_refuser():
    action_id = chat("Quels produits retirer ?")["pending_actions"][0]["action_id"]
    r = decide(action_id, True)
    assert r.json()["status"] == "executed"
    assert r.json()["result"]["decision_status"] == "approved"
    assert r.json()["result"]["decided_by"] == "doha"
    # Une action ne peut être traitée qu'une fois.
    assert decide(action_id, True).status_code == 404

    action_id = chat("Quels produits retirer ?")["pending_actions"][0]["action_id"]
    r = decide(action_id, False)
    assert r.json()["status"] == "refused"


# ---------- Contrat sécurisé du catalogue (retour de Manal, Partie 3) ----------

def test_decideur_obligatoire():
    action_id = chat("Quels produits retirer ?")["pending_actions"][0]["action_id"]
    assert client.post(f"/actions/{action_id}/decision", json={"approved": True}).status_code == 422
    assert decide(action_id, True, decided_by="   ").status_code == 422
    assert action_id in agent.PENDING  # rien n'a été consommé


def test_refus_enregistre_dans_le_catalogue():
    action_id = chat("Quels produits retirer ?")["pending_actions"][0]["action_id"]
    res = decide(action_id, False, decided_by="manal").json()
    assert res["status"] == "refused"
    assert res["result"]["decision_status"] == "refused"
    assert res["result"]["decided_by"] == "manal"
    assert res["result"]["idempotency_key"] == action_id


def test_refus_du_catalogue_401_pas_de_500_et_proposition_gardee():
    mock_services.REQUIRED_TOKEN = "jeton-attendu-1234567890"  # le catalogue exige un jeton
    settings.catalog_token = "mauvais-jeton-1234567890"
    action_id = chat("Quels produits retirer ?")["pending_actions"][0]["action_id"]
    r = decide(action_id, True)
    assert r.status_code == 200
    assert r.json()["status"] == "failed" and r.json()["catalog_status"] == 401
    pending = [a["action_id"] for a in client.get("/actions/pending").json()]
    assert action_id in pending  # la proposition n'a pas disparu
    # Avec le bon jeton, la même proposition passe ensuite.
    settings.catalog_token = "jeton-attendu-1234567890"
    assert decide(action_id, True).json()["status"] == "executed"


def test_panne_catalogue_proposition_gardee():
    action_id = chat("Quels produits retirer ?")["pending_actions"][0]["action_id"]
    settings.fail_services = {"catalog"}
    res = decide(action_id, True).json()
    assert res["status"] == "failed"
    assert action_id in agent.PENDING


def test_requete_http_envoyee_au_vrai_catalogue():
    """Vérifie les en-têtes et le corps exacts du contrat, et qu'une reprise réutilise la même clé."""
    settings.use_mock_services = False
    settings.catalog_token = "jeton-orchestrateur-123456"
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:  # première tentative : délai dépassé (réponse perdue)
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(201, json={"decision_id": 7, "decision_status": "approved", "status": "recorded"})

    real_client = httpx.AsyncClient
    executor.httpx.AsyncClient = lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    try:
        agent.PENDING["act12345"] = agent.PendingAction(
            action_id="act12345", tool="record_decision",
            args={"product_id": "P005", "action": "replace", "replacement_id": "S104", "reason": "Ventes en baisse"})
        res = asyncio.run(agent.decide("act12345", True, "doha"))
    finally:
        executor.httpx.AsyncClient = real_client
    assert res["status"] == "executed"
    assert len(seen) == 2
    for req in seen:
        assert req.method == "POST" and req.url.path == "/decisions"
        assert req.headers["Authorization"] == "Bearer jeton-orchestrateur-123456"
        assert req.headers["Idempotency-Key"] == "act12345"  # même clé à chaque reprise
        assert json.loads(req.content) == {"product_id": "P005", "action": "replace", "replacement_id": "S104",
                                           "reason": "Ventes en baisse", "decision": "approved",
                                           "decided_by": "doha"}


def test_le_llm_ne_peut_pas_choisir_la_decision():
    """« decision » et « decided_by » viennent de l'humain : proposés par le LLM, ils sont refusés."""
    with pytest.raises(Exception):
        TOOLS["record_decision"].args_model(product_id="P005", action="remove", reason="test",
                                            decision="approved", decided_by="llm")


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
