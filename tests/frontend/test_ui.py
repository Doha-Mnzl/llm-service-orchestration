"""LSO-12 / LSO-13 / LSO-24 / LSO-25 : parcours utilisateur Streamlit (AppTest + faux backend HTTP).

Le faux backend enregistre toutes les requêtes : on vérifie ainsi que l'interface ne parle
qu'à l'orchestrateur pour agir, et seulement en GET aux services métier.
"""

import json
import re
import sys
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from frontend import client
from frontend.config import settings

APP = str(Path(__file__).resolve().parents[2] / "frontend" / "app.py")

CHAT_OK = {
    "answer": "Le produit P005 est en forte baisse ; remplacement proposé : S104.", "status": "ok", "llm": "fake",
    "warnings": [],
    "trace": [{"tool": "find_underperforming_products", "args": {}, "status": "ok", "attempts": 1,
               "duration_ms": 12, "detail": None},
              {"tool": "get_stock_levels", "args": {}, "status": "ok", "attempts": 2, "duration_ms": 40,
               "detail": None},
              {"tool": "record_decision", "args": {}, "status": "pending_validation", "attempts": 0,
               "duration_ms": 0, "detail": None}],
    "pending_actions": [{"action_id": "ab12cd34", "tool": "record_decision",
                         "args": {"product_id": "P005", "action": "replace", "replacement_id": "S104",
                                  "reason": "Ventes en forte baisse et stock élevé"}}],
}
SUMMARY = {"period_months": 1, "start": "2026-10-01", "end": "2026-10-31", "currency": "MAD", "items": [
    {"product_id": "P001", "name": "Câble USB-C 1 m", "category": "câbles", "units_sold": 100, "revenue": 7000.0,
     "gross_margin": 2660.0, "margin_rate": 0.38, "trend_6m": 0.2, "previous_revenue": 5000.0,
     "revenue_change": 0.4},
    {"product_id": "P005", "name": "Écouteurs filaires jack", "category": "écouteurs", "units_sold": 10,
     "revenue": 800.0, "gross_margin": 80.0, "margin_rate": 0.1, "trend_6m": -0.6, "previous_revenue": 1000.0,
     "revenue_change": -0.2}]}
MONTHLY = {"currency": "MAD", "category": None, "product_id": None, "items": [
    {"month": "2026-09", "start": "2026-09-01", "end": "2026-09-30", "units": 90, "revenue": 6500.0,
     "gross_margin": 2000.0},
    {"month": "2026-10", "start": "2026-10-01", "end": "2026-10-31", "units": 110, "revenue": 7800.0,
     "gross_margin": 2740.0}]}
STOCK = {"product_id": "P005", "name": "Écouteurs filaires jack", "category": "écouteurs", "quantity": 280,
         "on_hand": 280, "reserved": 0, "available": 280, "reorder_point": 10, "avg_daily_demand": 0.4,
         "coverage_days": 700, "coverage_status": "overstock"}
ROUTES = {
    ("sales", "/sales/summary"): SUMMARY,
    ("sales", "/sales/monthly"): MONTHLY,
    ("inventory", "/stocks"): {"as_of": "2026-10-31", "items": [STOCK], "unknown_product_ids": []},
    ("inventory", "/stocks/P005"): {**STOCK, "as_of": "2026-10-31", "open_stockout": None},
    ("inventory", "/stocks/alerts"): {"as_of": "2026-10-31", "overstock": [
        {"product_id": "P005", "name": "Écouteurs filaires jack", "category": "écouteurs", "quantity": 280,
         "coverage_days": 700, "coverage_status": "overstock"}],
        "stockout": [{"product_id": "P006", "name": "Câble micro-USB 2 m", "category": "câbles", "quantity": 0,
                      "since": "2026-09-20", "days_out": 41}], "critical": []},
    ("catalog", "/catalog/categories"): [{"name": "câbles", "label": "Câbles", "product_count": 6,
                                          "supplier_product_count": 20}],
    ("catalog", "/catalog/products/P005"): {"product_id": "P005", "name": "Écouteurs filaires jack",
                                            "category": "écouteurs", "brand": "Kebo", "attributes": {},
                                            "unit_price": 80.0, "unit_cost": 72.0, "status": "active",
                                            "created_on": "2025-05-01"},
    ("catalog", "/catalog/supplier-products/S104"): {"supplier_product_id": "S104", "name": "Écouteurs Bluetooth",
                                                     "category": "écouteurs", "price": 240.0, "cost": 130.0,
                                                     "estimated_margin_rate": 0.46, "supplier_id": "F01",
                                                     "supplier_name": "Atlas Distribution", "brand": "Pixa",
                                                     "attributes": {}, "availability": "in_stock",
                                                     "stock_available": 50, "lead_time_days": 6},
    ("catalog", "/decisions"): {"total": 1, "items": [
        {"decision_id": 1, "product_id": "P042", "action": "replace", "replacement_id": "S150",
         "reason": "Ventes en forte baisse", "decision_status": "approved", "source": "seed",
         "decided_by": "manal", "recorded_by_client": "seed", "decided_at": "2026-10-11T10:00:00Z",
         "idempotency_key": "seed-1",
         "created_at": "2026-10-11T09:00:00Z", "idempotent_replay": False}]},
    ("catalog", "/decisions/7"): {"decision_id": 7, "product_id": "P005", "action": "replace",
                                  "replacement_id": "S104", "decision_status": "approved", "events": []},
    ("catalog", "/decisions/8"): {"decision_id": 8, "product_id": "P005", "action": "replace",
                                  "replacement_id": "S104", "decision_status": "approved", "events": []},
    ("orch", "/actions/pending"): CHAT_OK["pending_actions"],
    ("orch", "/health"): {"status": "ok", "llm": "fake", "mock_services": False},
    ("orch", "/services/health"): {"sales": "up", "inventory": "up", "catalog": "down",
                                   "analysis": "unknown (RabbitMQ)"},
}


class FakeBackend:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.down: set[str] = set()
        self.chat = CHAT_OK
        self.decision = {"status": "executed", "result": {"decision_id": 7, "status": "recorded"}}
        self.decision_fault: str | None = None  # "down" | "timeout" | "http500" : panne de la seule décision
        self.pending = list(CHAT_OK["pending_actions"])
        self.pop_on_decide = False   # comme l'orchestrateur réel : l'action quitte la file dès qu'elle est traitée
        self.catalog_health = {"status": "ok", "service": "catalog", "legacy_mode": False}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host in self.down:
            raise httpx.ConnectError("refusé", request=request)
        if path.endswith("/health") and host != "orch":
            return httpx.Response(200, json=self.catalog_health if host == "catalog" else {"status": "ok"})
        if (host, path) == ("orch", "/chat"):
            return httpx.Response(200, json=self.chat)
        if host == "orch" and re.fullmatch(r"/actions/\w+/decision", path):
            if self.pop_on_decide:
                self.pending = [a for a in self.pending if a["action_id"] not in path]
            if self.decision_fault == "down":
                raise httpx.ConnectError("refusé", request=request)
            if self.decision_fault == "timeout":
                raise httpx.ReadTimeout("délai", request=request)
            if self.decision_fault == "http500":
                return httpx.Response(500, json={"detail": "erreur interne"})
            return httpx.Response(200, json=self.decision)
        if (host, path) == ("orch", "/actions/pending"):
            return httpx.Response(200, json=self.pending)
        if (host, path) in ROUTES:
            return httpx.Response(200, json=ROUTES[(host, path)])
        return httpx.Response(404, json={"detail": "inconnu"})

    def sent(self, host=None, method=None):
        return [r for r in self.requests if (host is None or r.url.host == host)
                and (method is None or r.method == method)]


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(settings, "orchestrator_url", "http://orch")
    monkeypatch.setattr(settings, "service_urls", {s: f"http://{s}" for s in ("sales", "inventory", "catalog")})
    monkeypatch.setattr(settings, "direct_read", True)
    return fake


def app(page="Assistant") -> AppTest:
    at = AppTest.from_file(APP, default_timeout=20)
    at.run()
    if page != "Assistant":
        at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception
    return at


def texts(at) -> str:
    parts = [m.value for m in at.markdown] + [e.value for e in at.error] + [w.value for w in at.warning] + \
            [s.value for s in at.success] + [i.value for i in at.info] + [c.value for c in at.caption]
    return "\n".join(str(p) for p in parts)


# ---------- assistant ----------

def test_question_shows_answer_call_trace_and_proposal(backend):
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    assert not at.exception
    page = texts(at)
    assert CHAT_OK["answer"] in page
    assert any("Appels aux services (3)" in e.label for e in at.expander)
    trace = at.dataframe[0].value
    assert list(trace["Statut"]) == ["✅ Réussi", "✅ Réussi", "⏳ En attente de validation"]
    assert list(trace["Tentatives"]) == [1, 2, 0]
    assert "Écouteurs filaires jack" in page and "Écouteurs Bluetooth" in page  # proposition enrichie
    assert json.loads(backend.sent("orch", "POST")[0].content) == {"message": "Quels produits retirer ?"}


def test_validation_requires_confirmation_and_goes_through_orchestrator(backend):
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    at.button(key="approve_1_ab12cd34").click().run()
    assert not [r for r in backend.sent("orch", "POST") if "decision" in r.url.path]  # pas encore envoyé
    assert "Confirmer la validation" in texts(at)
    at.button(key="confirm_btn_1_ab12cd34").click().run()
    calls = [r for r in backend.sent("orch", "POST") if "decision" in r.url.path]
    assert len(calls) == 1 and calls[0].url.path == "/actions/ab12cd34/decision"
    assert json.loads(calls[0].content) == {"approved": True}
    assert "vérifiée dans le catalogue (n° 7)" in texts(at)
    assert not [b for b in at.button if b.key == "approve_1_ab12cd34"]  # plus de double décision


def test_validation_with_simulated_services_is_not_presented_as_real(backend):
    ROUTES[("orch", "/health")]["mock_services"] = True
    try:
        at = app()
        at.chat_input[0].set_value("Quels produits retirer ?").run()
        at.button(key="approve_1_ab12cd34").click().run()
        at.button(key="confirm_btn_1_ab12cd34").click().run()
        assert any("services simulés" in w.value for w in at.warning)
        assert not at.success
    finally:
        ROUTES[("orch", "/health")]["mock_services"] = False


def _validate(at, key="1_ab12cd34"):
    at.button(key=f"approve_{key}").click().run()
    at.button(key=f"confirm_btn_{key}").click().run()


def _keys(at):
    return {b.key for b in at.button}


@pytest.mark.parametrize("fault", ["down", "timeout", "http500"])
def test_unknown_outcome_is_uncertain_then_retry_after_real_state_check(backend, fault):
    backend.decision_fault = fault
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    assert any("Résultat inconnu" in w.value for w in at.warning)
    assert not at.success and "approve_1_ab12cd34" not in _keys(at)
    assert "check_1_ab12cd34" in _keys(at)
    backend.decision_fault = None                          # la panne est passée, l'action est encore en attente
    at.button(key="check_1_ab12cd34").click().run()
    assert "approve_1_ab12cd34" in _keys(at)              # nouvelle tentative possible
    _validate(at)
    assert "vérifiée dans le catalogue (n° 7)" in texts(at)
    calls = [r for r in backend.sent("orch", "POST") if "decision" in r.url.path]
    assert len(calls) == 2


def test_uncertain_outcome_never_turns_into_a_false_success(backend):
    backend.decision_fault = "timeout"
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    backend.pending = []                                   # file momentanément vide (orchestrateur en reprise)
    at.button(key="check_1_ab12cd34").click().run()
    assert any("absente de la file" in w.value for w in at.warning)
    assert not at.success and "approve_1_ab12cd34" not in _keys(at)
    assert "check_1_ab12cd34" in _keys(at)                # une file vide n'est pas une preuve : revérifier


def test_proposal_that_reappears_after_a_timeout_is_unlocked(backend):
    """Cas reproduit par Codex : timeout, file vide, puis l'orchestrateur remet la proposition en file."""
    backend.decision_fault = "timeout"
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    backend.pending = []
    at.button(key="check_1_ab12cd34").click().run()
    backend.pending = list(CHAT_OK["pending_actions"])   # remise en file après l'échec côté orchestrateur
    at.button(key="check_1_ab12cd34").click().run()
    assert "approve_1_ab12cd34" in _keys(at) and not at.success


def test_decisions_page_unlocks_a_returning_proposal_without_any_click(backend):
    backend.pop_on_decide, backend.decision_fault = True, "timeout"
    at = app("Décisions")
    _validate(at, key="dec_ab12cd34")
    backend.pending = list(CHAT_OK["pending_actions"])
    at.sidebar.radio[0].set_value("État du système").run()
    at.sidebar.radio[0].set_value("Décisions").run()      # simple réaffichage : la file fait foi
    assert "approve_dec_ab12cd34" in _keys(at)


def test_check_while_orchestrator_still_down_keeps_uncertain_state(backend):
    backend.decision_fault = "down"
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    backend.down.add("orch")
    at.button(key="check_1_ab12cd34").click().run()
    assert any("toujours injoignable" in e.value for e in at.error)
    assert "approve_1_ab12cd34" not in _keys(at) and "check_1_ab12cd34" in _keys(at)


def test_orchestrator_reported_failure_is_retryable(backend):
    backend.decision = {"status": "failed", "detail": "Service 'catalog' indisponible : ConnectError"}
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    assert any("Résultat incertain" in w.value for w in at.warning)
    assert "non enregistrée" not in texts(at)
    backend.decision = {"status": "executed", "result": {"decision_id": 8, "status": "recorded"}}
    at.button(key="check_1_ab12cd34").click().run()
    _validate(at)
    assert "(n° 8)" in texts(at)


def test_refusal_message_does_not_claim_an_archive(backend):
    backend.decision = {"status": "refused", "action": {}}
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    at.button(key="refuse_1_ab12cd34").click().run()
    at.button(key="confirm_btn_1_ab12cd34").click().run()
    assert "pas encore archivé" in texts(at)


def test_decision_timeout_covers_orchestrator_retries(backend, monkeypatch):
    """L'orchestrateur peut réessayer l'écriture (3 × 5 s) : l'interface ne doit pas conclure trop tôt."""
    monkeypatch.setattr(settings, "decision_timeout", 42.0, raising=False)
    client.orchestrator.decide("ab12cd34", True)
    call = [r for r in backend.sent("orch", "POST") if "decision" in r.url.path][0]
    assert call.extensions["timeout"]["read"] == 42.0
    assert settings.http_timeout < 30 <= type(settings)().decision_timeout


def test_unknown_upstream_status_values_do_not_break_the_proposal(backend, monkeypatch):
    import copy
    routes = copy.deepcopy(ROUTES)
    routes[("inventory", "/stocks/P005")]["coverage_status"] = "nouveau_statut"
    routes[("catalog", "/catalog/supplier-products/S104")]["availability"] = "sur_commande"
    monkeypatch.setattr(sys.modules[__name__], "ROUTES", routes)
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    assert not at.exception and "approve_1_ab12cd34" in {b.key for b in at.button}
    assert "nouveau_statut" in texts(at) and "sur_commande" in texts(at)


def test_decisions_page_keeps_the_result_after_the_action_leaves_the_queue(backend):
    backend.pop_on_decide = True
    at = app("Décisions")
    _validate(at, key="dec_ab12cd34")
    assert "vérifiée dans le catalogue (n° 7)" in texts(at)


def test_decisions_page_shows_uncertain_result_then_real_state(backend):
    backend.pop_on_decide, backend.decision_fault = True, "timeout"
    at = app("Décisions")
    _validate(at, key="dec_ab12cd34")
    assert any("Résultat inconnu" in w.value for w in at.warning)
    at.button(key="check_dec_ab12cd34").click().run()
    assert any("absente de la file" in w.value for w in at.warning) and not at.success
    assert "check_dec_ab12cd34" in _keys(at)


def test_mock_mode_is_announced_and_never_mixed_with_real_data(backend):
    ROUTES[("orch", "/health")]["mock_services"] = True
    try:
        at = app()
        assert any("Mode simulé" in w.value for w in at.warning)
        at.chat_input[0].set_value("Quels produits retirer ?").run()
        assert not [r for r in backend.requests if r.url.host in ("sales", "inventory", "catalog")]
        assert "non affichés en mode simulé" in texts(at)
    finally:
        ROUTES[("orch", "/health")]["mock_services"] = False


def test_real_mode_has_no_simulation_banner(backend):
    at = app()
    assert not any("Mode simulé" in w.value for w in at.warning)


def test_status_page_flags_insecure_legacy_catalog(backend):
    backend.catalog_health = {"status": "ok", "service": "catalog", "legacy_mode": True}
    at = app("État du système")
    assert any("NON SÛR" in w.value for w in at.warning)


def test_mock_mode_never_shows_real_history_or_dashboard_data(backend):
    ROUTES[("orch", "/health")]["mock_services"] = True
    try:
        for page in ("Décisions", "Tableau de bord"):
            at = app(page)
            assert "Mode simulé" in texts(at) + "".join(w.value for w in at.warning)
        assert not [r for r in backend.requests if r.url.host in ("sales", "inventory", "catalog")]
    finally:
        ROUTES[("orch", "/health")]["mock_services"] = False


def test_unknown_orchestrator_mode_never_enriches_with_real_data(backend, monkeypatch):
    original = backend.__call__

    def flaky_health(request):
        if (request.url.host, request.url.path) == ("orch", "/health"):
            backend.requests.append(request)
            return httpx.Response(503, json={"detail": "indisponible"})
        return original(request)

    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(flaky_health))
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    assert "mode de l'orchestrateur inconnu" in texts(at)
    assert not [r for r in backend.requests if r.url.host in ("sales", "inventory", "catalog")]


def test_success_is_never_claimed_without_reading_the_decision_back(backend):
    backend.decision = {"status": "executed", "result": {"decision_id": 99, "status": "recorded"}}  # introuvable
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    assert not at.success and any("non vérifiée" in w.value for w in at.warning)


def test_unknown_provenance_after_execution_is_not_presented_as_persisted(backend, monkeypatch):
    original = backend.__call__
    calls = {"health": 0}

    def health_fails_after_execution(request):
        if (request.url.host, request.url.path) == ("orch", "/health"):
            calls["health"] += 1
            if any("decision" in r.url.path for r in backend.requests):
                backend.requests.append(request)
                return httpx.Response(503, json={"detail": "indisponible"})
        return original(request)

    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(health_fails_after_execution))
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    at.session_state["_orchestrator_mode"] = None            # cache expiré : le mode doit être relu
    _validate(at)
    assert not at.success and any("non vérifiée" in w.value for w in at.warning)


def test_health_without_explicit_boolean_means_unknown_mode(backend):
    ROUTES[("orch", "/health")].pop("mock_services")
    try:
        at = app()
        at.chat_input[0].set_value("Quels produits retirer ?").run()
        assert "mode de l'orchestrateur inconnu" in texts(at)
        assert not [r for r in backend.requests if r.url.host in ("sales", "inventory", "catalog")]
        at = app("État du système")
        assert {m.label: m.value for m in at.metric}["Services"] == "inconnu"
    finally:
        ROUTES[("orch", "/health")]["mock_services"] = False


def test_zero_margin_with_units_sold_is_not_reported_as_no_sales():
    from frontend.views import dashboard
    assert dashboard.has_sales([{"units": 200, "revenue": 1000.0, "gross_margin": 0.0}]) is True
    assert dashboard.has_sales([{"units": 0, "revenue": 0.0, "gross_margin": 0.0}]) is False


def test_orchestrator_404_is_not_final_and_a_returning_proposal_is_unlocked(backend, monkeypatch):
    original = backend.__call__

    def decision_404(request):
        if request.url.host == "orch" and request.url.path.endswith("/decision"):
            backend.requests.append(request)
            return httpx.Response(404, json={"detail": "Action inconnue ou déjà traitée"})
        return original(request)

    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(decision_404))
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    assert "check_1_ab12cd34" in _keys(at)                 # 404 : pas une preuve définitive
    at.button(key="check_1_ab12cd34").click().run()        # la proposition est toujours en file
    assert "approve_1_ab12cd34" in _keys(at)


@pytest.mark.parametrize("initial_mode", [False, None])
def test_recheck_after_switch_to_mock_keeps_original_result_uncertain(backend, monkeypatch, initial_mode):
    health = ROUTES[("orch", "/health")]
    monkeypatch.setitem(health, "mock_services", initial_mode)
    backend.decision = {"status": "executed", "result": {"decision_id": 99}}
    backend.pending = []
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    assert any("non vérifiée" in w.value for w in at.warning)

    monkeypatch.setitem(health, "mock_services", True)
    backend.requests.clear()
    at.button(key="check_1_ab12cd34").click().run()
    assert any("non vérifiée" in w.value for w in at.warning)
    assert "check_1_ab12cd34" in _keys(at)
    assert not any(r.url.path == "/decisions/99" for r in backend.requests)


def test_catalog_proof_requires_an_approved_decision(backend, monkeypatch):
    import copy
    routes = copy.deepcopy(ROUTES)
    routes[("catalog", "/decisions/7")]["decision_status"] = "pending"
    monkeypatch.setattr(sys.modules[__name__], "ROUTES", routes)
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    assert not at.success and any("non vérifiée" in w.value for w in at.warning)


def test_unknown_mode_hides_real_history_and_dashboard(backend):
    ROUTES[("orch", "/health")].pop("mock_services")
    try:
        for page in ("Décisions", "Tableau de bord"):
            at = app(page)
            assert "mode de l'orchestrateur inconnu" in "".join(w.value for w in at.warning).lower()
        assert not [r for r in backend.requests if r.url.host in ("sales", "inventory", "catalog")]
    finally:
        ROUTES[("orch", "/health")]["mock_services"] = False


def test_reopened_proposal_shows_why_the_last_attempt_failed(backend, monkeypatch):
    original = backend.__call__

    def decision_422(request):
        if request.url.host == "orch" and request.url.path.endswith("/decision"):
            backend.requests.append(request)
            return httpx.Response(422, json={"detail": "argument invalide"})
        return original(request)

    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(decision_422))
    at = app("Décisions")
    _validate(at, key="dec_ab12cd34")
    assert "argument invalide" in texts(at) and "approve_dec_ab12cd34" in _keys(at)


def test_unexpected_decision_status_is_reported_as_uncertain(backend):
    backend.decision = {"status": "error"}
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    _validate(at)
    assert any("statut « error »" in w.value for w in at.warning)


def test_refusal_flow(backend):
    backend.decision = {"status": "refused", "action": {}}
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    at.button(key="refuse_1_ab12cd34").click().run()
    at.button(key="confirm_btn_1_ab12cd34").click().run()
    call = [r for r in backend.sent("orch", "POST") if "decision" in r.url.path][0]
    assert json.loads(call.content) == {"approved": False}
    assert "Proposition refusée" in texts(at)


def test_cancel_confirmation_sends_nothing(backend):
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    at.button(key="approve_1_ab12cd34").click().run()
    at.button(key="cancel_1_ab12cd34").click().run()
    assert not [r for r in backend.sent("orch", "POST") if "decision" in r.url.path]


def test_orchestrator_down_is_visible_and_nothing_is_invented(backend):
    backend.down.add("orch")
    at = app()
    at.chat_input[0].set_value("Quelles ventes ?").run()
    page = texts(at)
    assert "L'orchestrateur ne répond pas" in page and "aucun chiffre n'est affiché" in page
    assert CHAT_OK["answer"] not in page


def test_partial_and_stopped_statuses_are_explicit(backend):
    backend.chat = {**CHAT_OK, "status": "partial", "pending_actions": [],
                    "warnings": ["Service « inventory » indisponible : réponse partielle."]}
    at = app()
    at.chat_input[0].set_value("Stocks ?").run()
    assert any("Réponse partielle" in w.value and "inventory" in w.value for w in at.warning)
    backend.chat = {**CHAT_OK, "status": "stopped", "pending_actions": [], "answer": "Analyse impossible."}
    at.chat_input[0].set_value("Ventes ?").run()
    assert any("Analyse arrêtée" in e.value for e in at.error)


def test_proposal_still_shown_when_catalog_details_unavailable(backend):
    backend.down.update({"catalog", "inventory"})
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    assert not at.exception and "Détails indisponibles" in texts(at)


# ---------- tableau de bord ----------

def test_dashboard_kpis_match_api_values(backend):
    at = app("Tableau de bord")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Chiffre d'affaires"] == "7 800 DH"   # 7000 + 800
    assert metrics["Marge brute"] == "2 740 DH"
    assert metrics["Taux de marge"] == "35,1 %"                  # 2740 / 7800
    assert metrics["Unités vendues"] == "110"
    assert metrics["⛔ Ruptures"] == "1" and metrics["📦 Surstocks et sans demande"] == "1"
    watch = next(d.value for d in at.dataframe if "Tendance 6 mois" in d.value.columns)
    assert watch.iloc[0]["Produit"].startswith("P005")  # plus forte baisse en premier
    assert watch.iloc[0]["État du stock"] == "📦 Surstock"
    assert metrics["Propositions en attente de validation"] == "1"


def test_dashboard_sales_down_other_blocks_keep_working(backend):
    backend.down.add("sales")
    at = app("Tableau de bord")
    assert any("Service ventes indisponible" in e.value for e in at.error)
    assert "Chiffre d'affaires" not in {m.label for m in at.metric}
    assert {m.label: m.value for m in at.metric}["⛔ Ruptures"] == "1"


def test_dashboard_direct_read_can_be_disabled(backend, monkeypatch):
    monkeypatch.setattr(settings, "direct_read", False)
    at = app("Tableau de bord")
    assert "désactivée" in texts(at)
    assert not [r for r in backend.requests if r.url.host in ("sales", "inventory", "catalog")]


# ---------- décisions et état ----------

def test_decisions_page_lists_pending_and_audited_history(backend):
    at = app("Décisions")
    assert any(b.key == "approve_dec_ab12cd34" for b in at.button)
    hist = at.dataframe[0].value
    assert hist.iloc[0]["Statut"] == "✅ Validée" and hist.iloc[0]["Décideur (déclaré)"] == "manal"
    assert hist.iloc[0]["Enregistrée par (client authentifié)"] == "seed"


def test_status_page_shows_each_service(backend):
    backend.down.add("catalog")
    at = app("État du système")
    rows = {r["Service"]: r["État"] for r in at.dataframe[1].value.to_dict("records")}
    assert rows["service ventes"] == "✅ Disponible" and rows["service catalogue"] == "⛔ Indisponible"
    seen = {r["Service"]: r["État vu par l'orchestrateur"] for r in at.dataframe[0].value.to_dict("records")}
    assert seen["catalog"] == "⛔ Indisponible"


def test_status_page_orchestrator_down(backend):
    backend.down.add("orch")
    at = app("État du système")
    assert any("orchestrateur indisponible" in e.value for e in at.error)


# ---------- règles d'accès ----------

def test_ui_never_writes_to_business_services(backend):
    at = app()
    at.chat_input[0].set_value("Quels produits retirer ?").run()
    at.button(key="approve_1_ab12cd34").click().run()
    at.button(key="confirm_btn_1_ab12cd34").click().run()
    for page in ("Tableau de bord", "Décisions", "État du système"):
        at.sidebar.radio[0].set_value(page).run()
    business = [r for r in backend.requests if r.url.host in ("sales", "inventory", "catalog")]
    assert business and all(r.method == "GET" for r in business)
    assert {r.url.host for r in backend.requests} <= {"orch", "sales", "inventory", "catalog"}


def test_frontend_code_has_no_llm_provider_access():
    src = "\n".join(p.read_text(encoding="utf-8") for p in Path(APP).parent.rglob("*.py"))
    for forbidden in ("groq.com", "api.openai", "anthropic", "11434", "GROQ_API_KEY"):
        assert forbidden not in src
    assert [m for m in vars(client.Services) if not m.startswith("_")] == ["LABELS", "get", "health"]
