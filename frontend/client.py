"""Clients HTTP de l'interface : SEUL point de sortie réseau du frontend.

- Orchestrator : chat, actions en attente, Valider/Refuser, état du système.
  Toute action qui modifie des données passe par lui (contrôles et validation humaine).
- Services : lecture seule (GET) de Sales, Inventory et Catalog pour les tableaux de bord.
  Aucune méthode d'écriture n'existe sur ce client.

Erreurs typées : Unavailable (réseau, délai, HTTP 5xx) et ApiError (HTTP 4xx).
"""

import time

import httpx

from frontend.config import settings

TRANSPORT: httpx.BaseTransport | None = None  # injecté par les tests (httpx.MockTransport)


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Unavailable(ApiError):
    def __init__(self, target: str, detail: str):
        super().__init__(503, f"{target} indisponible : {detail}")
        self.target = target


def _request(target: str, base: str, method: str, path: str, timeout: float, **kw) -> dict | list:
    try:
        with httpx.Client(base_url=base, timeout=timeout, transport=TRANSPORT) as c:
            r = c.request(method, path, **kw)
    except httpx.TimeoutException:
        raise Unavailable(target, f"aucune réponse en {timeout:g} s") from None
    except httpx.TransportError:
        raise Unavailable(target, "connexion impossible") from None
    if r.status_code >= 500:
        raise Unavailable(target, f"erreur HTTP {r.status_code}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ApiError(r.status_code, str(detail))
    return r.json()


class Orchestrator:
    name = "orchestrateur"

    def _call(self, method, path, timeout=None, **kw):
        return _request(self.name, settings.orchestrator_url, method, path,
                        timeout or settings.http_timeout, **kw)

    def chat(self, message: str) -> dict:
        return self._call("POST", "/chat", timeout=settings.chat_timeout, json={"message": message})

    def pending_actions(self) -> list[dict]:
        return self._call("GET", "/actions/pending")

    def decide(self, action_id: str, approved: bool) -> dict:
        return self._call("POST", f"/actions/{action_id}/decision", timeout=settings.decision_timeout,
                          json={"approved": approved})

    def health(self) -> dict:
        return self._call("GET", "/health")

    def services_health(self) -> dict:
        return self._call("GET", "/services/health")


class Services:
    """Lecture seule des services métier (GET uniquement)."""

    LABELS = {"sales": "service ventes", "inventory": "service stocks", "catalog": "service catalogue"}

    def get(self, service: str, path: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        return _request(self.LABELS[service], settings.service_urls[service], "GET", path,
                        settings.http_timeout, params=params)

    def health(self, service: str) -> tuple[str, float | None, dict]:
        """(état, temps de réponse en ms, contenu de /health)."""
        t0 = time.perf_counter()
        try:
            payload = self.get(service, "/health")
            return "up", round((time.perf_counter() - t0) * 1000), payload if isinstance(payload, dict) else {}
        except Unavailable:
            return "down", None, {}
        except ApiError as e:
            return f"erreur {e.status}", None, {}


orchestrator = Orchestrator()
services = Services()
