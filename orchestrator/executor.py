"""Exécution des outils : appel du service avec timeout et reprises.

Le contrôle (outil autorisé, arguments valides) est fait AVANT d'arriver ici,
dans agent.py. Ce module ne fait qu'appeler les services et signaler les pannes.
"""

import asyncio
import time

import httpx

from orchestrator import mock_services
from orchestrator.config import settings
from orchestrator.tools import Tool


class ServiceUnavailable(Exception):
    def __init__(self, service: str, detail: str, attempts: int):
        super().__init__(f"Service '{service}' indisponible : {detail}")
        self.service = service
        self.detail = detail
        self.attempts = attempts


async def _call_once(tool: Tool, args: dict, headers: dict[str, str]) -> dict:
    # Mode développement : faux services, avec pannes simulées.
    if settings.use_mock_services:
        if tool.service in settings.fail_services:
            raise ConnectionError("panne simulée (FAIL_SERVICES)")
        return mock_services.call(tool.name, args, headers)

    if tool.method == "QUEUE":
        # TODO (avec Zakaria) : publier la tâche dans RabbitMQ et attendre la réponse (RPC).
        raise ConnectionError("appel RabbitMQ pas encore branché")

    base = settings.service_urls[tool.service]
    path_args = {k: v for k, v in args.items() if "{" + k + "}" in tool.path}
    other = {k: v for k, v in args.items() if k not in path_args and v is not None}
    url = base + tool.path.format(**path_args)

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        if tool.method == "GET":
            resp = await client.get(url, params=other)
        else:
            resp = await client.post(url, json=other, headers=headers)
    if resp.status_code >= 500:
        raise ConnectionError(f"HTTP {resp.status_code}")
    resp.raise_for_status()  # 4xx = refus métier (401, 404, 409, 422…) : pas de reprise
    return resp.json()


async def execute(tool: Tool, args: dict, headers: dict[str, str] | None = None) -> tuple[dict, int]:
    """Renvoie (résultat, nombre de tentatives).

    - Panne (réseau, délai, 5xx) : reprises, puis ServiceUnavailable.
    - Refus métier (4xx) : httpx.HTTPStatusError immédiatement, sans reprise.
    Les en-têtes (jeton, Idempotency-Key) sont identiques à chaque reprise : une écriture
    rejouée après un délai dépassé ne crée pas de doublon côté catalogue.
    """
    headers = headers or {}
    attempts = 0
    last_error = ""
    for attempt in range(1 + settings.max_retries):
        attempts = attempt + 1
        try:
            return await asyncio.wait_for(_call_once(tool, args, headers), timeout=settings.http_timeout), attempts
        except (httpx.TransportError, ConnectionError, asyncio.TimeoutError) as e:
            last_error = type(e).__name__ + (f" : {e}" if str(e) else "")
            if attempt < settings.max_retries:
                await asyncio.sleep(0.2 * (attempt + 1))  # petite attente croissante
    raise ServiceUnavailable(tool.service, last_error, attempts)


def now_ms() -> float:
    return time.perf_counter() * 1000
