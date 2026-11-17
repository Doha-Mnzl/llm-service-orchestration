"""API de l'orchestrateur.

Lancer depuis la racine du dépôt :
    uvicorn orchestrator.main:app --reload --port 8000
Puis ouvrir http://127.0.0.1:8000/docs
"""

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from orchestrator import agent
from orchestrator.config import settings
from orchestrator.tools import TOOLS

app = FastAPI(title="Orchestrateur LLM", version="0.1.0")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)


class DecisionRequest(BaseModel):
    approved: bool


@app.get("/health")
def health():
    return {"status": "ok", "llm": settings.llm_provider, "mock_services": settings.use_mock_services}


@app.get("/tools")
def list_tools():
    return [{"name": t.name, "service": t.service, "description": t.description,
             "critical": t.critical, "requires_validation": t.requires_validation}
            for t in TOOLS.values()]


@app.post("/chat", response_model=agent.ChatResponse)
async def chat(req: ChatRequest):
    return await agent.run_chat(req.message)


@app.get("/actions/pending", response_model=list[agent.PendingAction])
def pending_actions():
    return list(agent.PENDING.values())


@app.post("/actions/{action_id}/decision")
async def decide(action_id: str, req: DecisionRequest):
    res = await agent.decide(action_id, req.approved)
    if res["status"] == "not_found":
        raise HTTPException(404, "Action inconnue ou déjà traitée")
    return res


@app.get("/services/health")
async def services_health():
    """État des services, pour l'interface (panneau « état du système »)."""
    if settings.use_mock_services:
        return {s: ("down" if s in settings.fail_services else "up (mock)")
                for s in ["sales", "inventory", "catalog", "analysis"]}
    out = {}
    async with httpx.AsyncClient(timeout=2) as client:
        for name, url in settings.service_urls.items():
            try:
                r = await client.get(f"{url}/health")
                out[name] = "up" if r.status_code == 200 else f"error {r.status_code}"
            except httpx.HTTPError:
                out[name] = "down"
    out["analysis"] = "unknown (RabbitMQ)"
    return out
