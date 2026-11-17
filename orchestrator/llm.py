"""Connexion au LLM, interchangeable par configuration (LLM_PROVIDER).

- fake   : réponses prédéfinies, pour développer et pour les tests de charge.
- groq   : offre gratuite Groq (API compatible OpenAI), pour la démo.
- ollama : modèle local, solution de secours (API compatible OpenAI).

Les messages suivent le format OpenAI : system / user / assistant (tool_calls) / tool.
Le LLM ne fait que PROPOSER des appels d'outils ; il n'exécute rien.
"""

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

import httpx

from orchestrator.config import settings
from orchestrator.tools import to_openai_schema


class LLMUnavailable(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMStep:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: Optional[str] = None  # réponse finale quand il n'y a plus d'outil à appeler


def assistant_message(step: LLMStep) -> dict:
    """Message assistant à ajouter à l'historique après une étape avec outils."""
    return {
        "role": "assistant",
        "content": step.text,
        "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
            for c in step.tool_calls
        ],
    }


# ---------------------------------------------------------------- Faux LLM

class FakeLLM:
    """Planificateur déterministe par mots-clés. Il imite le comportement d'un vrai LLM :
    il demande un outil, lit le résultat, puis demande l'outil suivant ou répond."""

    name = "fake"

    async def next_step(self, messages: list[dict]) -> LLMStep:
        question = next(m["content"] for m in reversed(messages) if m["role"] == "user").lower()
        results = _tool_results(messages)  # {nom_outil: résultat (dict)}

        # Cas de test du contrôle.
        if "outil_interdit" in question:
            return self._once(results, "drop_database", {"table": "products"})
        if "args_invalides" in question:
            return self._once(results, "get_sales_summary", {"period_months": 99})

        if re.search(r"retir|rempla|peu performant", question):
            return self._plan_replacement(results)
        if re.search(r"rupture|surstock|stock", question):
            return self._plan([("get_stock_alerts", {"kind": "all"})], results)
        if re.search(r"vente|chiffre|marge|\bca\b", question):
            return self._plan([("get_sales_summary", {"period_months": 1})], results)
        if re.search(r"fournisseur|catalogue", question):
            return self._plan([("search_supplier_catalog", {"category": "câbles"})], results)
        return LLMStep(text="Je n'ai pas compris la demande. Exemples : « Quels produits dois-je "
                            "retirer ce mois-ci ? », « Quels produits sont en rupture ? »")

    # -- plans --

    def _plan_replacement(self, results: dict) -> LLMStep:
        if "find_underperforming_products" not in results:
            return _call("find_underperforming_products", {"period_months": 1, "top_n": 3})
        under = results["find_underperforming_products"].get("items", [])
        ids = [p["product_id"] for p in under]
        if not ids:
            return LLMStep(text="Aucun produit peu performant sur la période.")
        if "get_stock_levels" not in results:
            return _call("get_stock_levels", {"product_ids": ids})
        if "find_replacements" not in results:
            return _call("find_replacements", {"product_id": ids[0], "max_results": 3})
        if "record_decision" not in results:
            cands = results["find_replacements"].get("candidates", [])
            args = {"product_id": ids[0],
                    "action": "replace" if cands else "remove",
                    "reason": "Ventes en forte baisse et stock élevé"}
            if cands:
                args["replacement_id"] = cands[0]["supplier_product_id"]
            return _call("record_decision", args)
        return LLMStep(text=_summary_replacement(results))

    def _plan(self, plan: list[tuple[str, dict]], results: dict) -> LLMStep:
        for name, args in plan:
            if name not in results:
                return _call(name, args)
        return LLMStep(text=_summary_generic(results))

    def _once(self, results: dict, name: str, args: dict) -> LLMStep:
        if name in results:
            return LLMStep(text=f"Action impossible : {results[name].get('error', 'refusée')}")
        return _call(name, args)


def _call(name: str, args: dict) -> LLMStep:
    return LLMStep(tool_calls=[ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, args=args)])


def _tool_results(messages: list[dict]) -> dict:
    names = {}
    for m in messages:
        for c in m.get("tool_calls") or []:
            names[c["id"]] = c["function"]["name"]
    out = {}
    for m in messages:
        if m["role"] == "tool":
            try:
                out[names.get(m["tool_call_id"], "?")] = json.loads(m["content"])
            except (json.JSONDecodeError, TypeError):
                out[names.get(m["tool_call_id"], "?")] = {"raw": m["content"]}
    return out


def _summary_replacement(r: dict) -> str:
    lines = ["Produits peu performants ce mois-ci :"]
    stock = {s["product_id"]: s for s in r.get("get_stock_levels", {}).get("items", [])}
    for p in r["find_underperforming_products"]["items"]:
        s = stock.get(p["product_id"])
        st = f"stock {s['quantity']} ({s['coverage_days']} j de couverture)" if s else "stock inconnu"
        lines.append(f"- {p['name']} ({p['product_id']}) : ventes {p['trend_6m']:+.0%} sur 6 mois, {st}")
    if "error" in r.get("get_stock_levels", {}):
        lines.append("⚠ Service stocks indisponible : les niveaux de stock ne sont pas affichés.")
    cands = r.get("find_replacements", {}).get("candidates", [])
    if cands:
        c = cands[0]
        lines.append(f"Remplacement proposé : {c['name']} ({c['supplier_product_id']}, {c['price']} €).")
    if r.get("record_decision", {}).get("status") == "pending_validation":
        lines.append("La décision est en attente de votre validation.")
    return "\n".join(lines)


def _summary_generic(r: dict) -> str:
    parts = []
    for name, res in r.items():
        if "error" in res:
            parts.append(f"⚠ {name} : {res['error']}")
        else:
            n = sum(len(v) for v in res.values() if isinstance(v, list))
            parts.append(f"{name} : {n} élément(s) trouvé(s).")
    return "\n".join(parts)


# ---------------------------------------------------------------- Vrais LLM

class OpenAICompatibleLLM:
    """Groq et Ollama exposent la même API (/chat/completions avec « tools »)."""

    def __init__(self, name: str, base_url: str, model: str, api_key: str = ""):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    async def next_step(self, messages: list[dict]) -> LLMStep:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {"model": self.model, "messages": messages, "tools": to_openai_schema(),
                   "tool_choice": "auto", "parallel_tool_calls": True, "temperature": 0}
        resp = None
        for attempt in range(1 + settings.llm_retries):
            try:
                async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
                    resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            except httpx.HTTPError as e:
                if attempt == settings.llm_retries:
                    raise LLMUnavailable(f"{self.name} : {type(e).__name__} {e}") from e
                await asyncio.sleep(1 + attempt)
                continue
            # 429 = limite de l'offre gratuite ; 5xx = panne passagère : on attend puis on réessaie.
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < settings.llm_retries:
                wait = _retry_after(resp, default=2 * (attempt + 1))
                await asyncio.sleep(min(wait, 20))
                continue
            break
        if resp.status_code >= 400:
            raise LLMUnavailable(f"{self.name} : HTTP {resp.status_code} {resp.text[:200]}")

        msg = resp.json()["choices"][0]["message"]
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": c["function"].get("arguments")}
            calls.append(ToolCall(id=c["id"], name=c["function"]["name"], args=args))
        return LLMStep(tool_calls=calls, text=msg.get("content"))


def _retry_after(resp: httpx.Response, default: float) -> float:
    try:
        return float(resp.headers.get("retry-after", default))
    except ValueError:
        return default


def get_llm():
    p = settings.llm_provider
    if p == "fake":
        return FakeLLM()
    if p == "groq":
        if not settings.groq_api_key:
            raise LLMUnavailable("GROQ_API_KEY manquante")
        return OpenAICompatibleLLM("groq", settings.groq_url, settings.groq_model, settings.groq_api_key)
    if p == "ollama":
        return OpenAICompatibleLLM("ollama", settings.ollama_url, settings.ollama_model)
    raise LLMUnavailable(f"LLM_PROVIDER inconnu : {p}")
