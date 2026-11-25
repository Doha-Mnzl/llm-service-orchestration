"""Boucle LLM → contrôle → exécution.

1. Le LLM propose un ou plusieurs appels d'outils.
2. L'orchestrateur contrôle chaque appel : outil autorisé ? arguments valides ? limite d'appels ?
3. Les appels acceptés sont exécutés (timeouts, reprises). Les décisions sont mises
   en attente de validation humaine au lieu d'être exécutées.
4. Les résultats sont renvoyés au LLM, qui continue ou rédige la réponse finale.

Pannes :
- service non critique (stocks, catalogue) : réponse partielle + avertissement ;
- service critique (ventes, analyse) : arrêt, message clair, rien d'inventé ;
- LLM indisponible : message d'erreur clair.
"""

import json
import time
import uuid
from typing import Any, Literal, Optional

import httpx
from pydantic import BaseModel, ValidationError

from orchestrator.config import settings
from orchestrator.executor import ServiceUnavailable, execute
from orchestrator.llm import LLMUnavailable, assistant_message, get_llm
from orchestrator.tools import TOOLS

SYSTEM_PROMPT = """Tu es l'assistant de gestion de stock et de catalogue d'un site e-commerce \
d'accessoires informatiques. Tu réponds en français, de façon concise.

Règles :
- Tu n'as PAS accès à la base de données. Toute donnée vient des outils fournis.
- N'invente aucun chiffre ni aucune justification absente des résultats des outils \
(pas de « concurrence », « tendance du marché », etc.).
- Si un outil renvoie une erreur, dis-le clairement et n'invente pas la donnée manquante.

Pour une demande de retrait ou de remplacement de produits, suis ces étapes :
1. find_underperforming_products (top_n = 3 sauf si le responsable demande un autre nombre) ;
2. EN UNE SEULE ÉTAPE (appels d'outils en parallèle) : get_stock_levels avec les identifiants \
trouvés et find_replacements pour chaque produit ;
4. record_decision pour chaque produit à retirer (action "remove") ou à remplacer \
(action "replace" avec replacement_id). Ces décisions ne sont PAS exécutées : elles sont \
soumises au responsable, qui les valide ou les refuse dans l'interface. Ne lui demande donc \
pas la permission dans le texte : appelle record_decision, en parallèle pour tous les produits.

Réponse finale : un court tableau (produit, baisse des ventes, stock, remplacement proposé), \
puis une phrase indiquant que les décisions attendent sa validation."""


class TraceEntry(BaseModel):
    tool: str
    args: dict[str, Any]
    status: Literal["ok", "rejected", "failed", "pending_validation"]
    attempts: int = 0
    duration_ms: int = 0
    detail: Optional[str] = None


class PendingAction(BaseModel):
    action_id: str
    tool: str
    args: dict[str, Any]


class ChatResponse(BaseModel):
    answer: str
    status: Literal["ok", "partial", "stopped", "error"]
    llm: str
    trace: list[TraceEntry] = []
    warnings: list[str] = []
    pending_actions: list[PendingAction] = []


# Actions en attente de validation humaine (en mémoire pour l'instant).
PENDING: dict[str, PendingAction] = {}


def _tool_message(call_id: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload, ensure_ascii=False)}


async def run_chat(message: str) -> ChatResponse:
    trace: list[TraceEntry] = []
    warnings: list[str] = []
    pending: list[PendingAction] = []
    llm_name = settings.llm_provider

    try:
        llm = get_llm()
    except LLMUnavailable as e:
        return ChatResponse(answer=f"Le LLM est indisponible ({e}). Réessayez plus tard.",
                            status="error", llm=llm_name)

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": message}]
    calls_done = 0

    for _ in range(settings.max_llm_steps):
        try:
            step = await llm.next_step(messages)
        except LLMUnavailable as e:
            return ChatResponse(answer=f"Le LLM est indisponible ({e}). Aucune analyse n'a été faite.",
                                status="error", llm=llm_name, trace=trace, warnings=warnings,
                                pending_actions=pending)

        if not step.tool_calls:  # réponse finale
            status = "partial" if warnings else "ok"
            return ChatResponse(answer=step.text or "", status=status, llm=llm_name,
                                trace=trace, warnings=warnings, pending_actions=pending)

        messages.append(assistant_message(step))

        for call in step.tool_calls:
            # --- Contrôle 1 : limite d'appels
            calls_done += 1
            if calls_done > settings.max_tool_calls:
                trace.append(TraceEntry(tool=call.name, args=call.args, status="rejected",
                                        detail="limite d'appels atteinte"))
                return ChatResponse(answer="Analyse arrêtée : trop d'appels d'outils pour une seule demande.",
                                    status="stopped", llm=llm_name, trace=trace, warnings=warnings,
                                    pending_actions=pending)

            # --- Contrôle 2 : outil autorisé
            tool = TOOLS.get(call.name)
            if tool is None:
                trace.append(TraceEntry(tool=call.name, args=call.args, status="rejected",
                                        detail="outil non autorisé"))
                messages.append(_tool_message(call.id, {"error": f"outil '{call.name}' non autorisé"}))
                continue

            # --- Contrôle 3 : arguments valides
            try:
                args = tool.args_model(**call.args).model_dump(exclude_none=True)
            except (ValidationError, TypeError) as e:
                detail = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}"
                                   for err in e.errors()) if isinstance(e, ValidationError) else str(e)
                trace.append(TraceEntry(tool=call.name, args=call.args, status="rejected",
                                        detail=f"arguments invalides ({detail})"))
                messages.append(_tool_message(call.id, {"error": f"arguments invalides : {detail}"}))
                continue

            # --- Validation humaine pour les actions qui modifient des données
            if tool.requires_validation:
                action = PendingAction(action_id=uuid.uuid4().hex[:8], tool=tool.name, args=args)
                PENDING[action.action_id] = action
                pending.append(action)
                trace.append(TraceEntry(tool=tool.name, args=args, status="pending_validation"))
                messages.append(_tool_message(call.id, {"status": "pending_validation",
                                                        "action_id": action.action_id}))
                continue

            # --- Exécution
            t0 = time.perf_counter()
            try:
                result, attempts = await execute(tool, args)
            except ServiceUnavailable as e:
                ms = int((time.perf_counter() - t0) * 1000)
                trace.append(TraceEntry(tool=tool.name, args=args, status="failed",
                                        attempts=e.attempts, duration_ms=ms, detail=e.detail))
                if tool.critical:
                    return ChatResponse(
                        answer=(f"Analyse impossible : le service « {e.service} » ne répond pas "
                                f"(après {e.attempts} tentatives). Aucune donnée n'a été inventée. "
                                "Réessayez dans quelques instants."),
                        status="stopped", llm=llm_name, trace=trace, warnings=warnings,
                        pending_actions=pending)
                warnings.append(f"Service « {e.service} » indisponible : réponse partielle.")
                messages.append(_tool_message(call.id, {"error": f"service {e.service} indisponible"}))
                continue
            except Exception as e:  # erreur métier du service (ex. 404)
                trace.append(TraceEntry(tool=tool.name, args=args, status="failed", detail=str(e)))
                messages.append(_tool_message(call.id, {"error": str(e)}))
                continue

            ms = int((time.perf_counter() - t0) * 1000)
            trace.append(TraceEntry(tool=tool.name, args=args, status="ok", attempts=attempts, duration_ms=ms))
            messages.append(_tool_message(call.id, result))

    return ChatResponse(answer="Analyse arrêtée : nombre maximal d'étapes atteint.", status="stopped",
                        llm=llm_name, trace=trace, warnings=warnings, pending_actions=pending)


def _catalog_headers(action_id: str) -> dict[str, str]:
    """En-têtes du contrat sécurisé du catalogue (docs/partie3/api.md, Partie 3)."""
    headers = {"Idempotency-Key": action_id}  # stable : la même pour chaque reprise de cette action
    if settings.catalog_token:
        headers["Authorization"] = f"Bearer {settings.catalog_token}"
    return headers


def _error_detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail", resp.text)
    except ValueError:
        detail = resp.text
    return str(detail)[:300]


async def decide(action_id: str, approved: bool, decided_by: str) -> dict:
    """Validation humaine : envoie la décision (acceptée OU refusée) au catalogue.

    - Les deux cas sont enregistrés, pour un historique complet.
    - Les champs humains (decision, decided_by) sont ajoutés ici, après la validation des arguments
      du LLM : le LLM ne peut ni les choisir ni les modifier.
    - Si le catalogue refuse (401, 409, 422…) ou est en panne, l'action reste en attente :
      une proposition n'est jamais perdue.
    """
    action = PENDING.pop(action_id, None)  # retirée pendant l'appel : pas de double traitement
    if action is None:
        return {"status": "not_found"}
    decision = "approved" if approved else "refused"
    payload = {**action.args, "decision": decision, "decided_by": decided_by}
    try:
        result, _ = await execute(TOOLS[action.tool], payload, headers=_catalog_headers(action_id))
    except ServiceUnavailable as e:
        PENDING[action_id] = action
        return {"status": "failed", "detail": str(e), "action": action.model_dump()}
    except httpx.HTTPStatusError as e:
        PENDING[action_id] = action
        code = e.response.status_code
        return {"status": "failed", "catalog_status": code,
                "detail": f"Le catalogue a refusé l'écriture (HTTP {code}) : {_error_detail(e.response)}",
                "action": action.model_dump()}
    return {"status": "executed" if approved else "refused", "decided_by": decided_by,
            "result": result, "action": action.model_dump()}
