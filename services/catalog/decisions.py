"""Décisions humaines persistantes (LSO-11 / LSO-22 / LSO-23), après corrections H2, H3 et H4.

Sécurité (H2) :
- toute écriture exige un client de service authentifié (voir auth.py) ; l'identité enregistrée
  (`recorded_by_client`) vient du jeton, jamais du corps ; `decided_by` est une DÉCLARATION ;
- aucune approbation implicite : `decision` est obligatoire pour un client authentifié ; seul le
  mode legacy explicite interprète l'appel actuel de l'orchestrateur comme « validé par l'humain ».

Transactions (H3) : décision + événement d'audit dans `with conn.transaction()` ; le COMMIT a lieu
avant la réponse de succès ; toute erreur annule l'ensemble.

Idempotence (H4) : clé `Idempotency-Key` propre à chaque client ; un rejeu n'est accepté (200) que si
l'empreinte de TOUS les champs significatifs est identique, sinon 409. Transitions : même cible et
même décideur ⇒ 200 (rejeu), sinon 409. Aucune décision ne modifie ni ne supprime de produit.
"""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from services.catalog.auth import Caller, require_authenticated_client, require_service_client
from services.catalog.config import settings
from services.catalog.db import get_conn

router = APIRouter(tags=["décisions"])
Conn = Annotated[psycopg.Connection, Depends(get_conn, scope="function")]
Client = Annotated[Caller, Depends(require_service_client)]
Authenticated = Annotated[Caller, Depends(require_authenticated_client)]
Status = Literal["pending", "approved", "refused", "cancelled"]
KEY_PATTERN = r"^[A-Za-z0-9._:-]{1,100}$"
Decider = Field(min_length=1, max_length=100)


class DecisionCreate(BaseModel):
    # Champs inconnus conservés à part (model_extra) : sans effet pour un client authentifié (un `source`
    # ou `recorded_by_client` envoyé n'est jamais utilisé), refusés en mode legacy (forme exacte exigée).
    model_config = ConfigDict(extra="allow")

    product_id: str = Field(pattern=r"^P\d{3,5}$")
    action: Literal["remove", "replace", "keep"]
    replacement_id: str | None = Field(None, pattern=r"^S\d{3,5}$")
    reason: str = Field(min_length=3, max_length=300)
    decision: Literal["approved", "refused", "pending"] | None = None
    decided_by: str | None = Field(None, min_length=1, max_length=100)
    idempotency_key: str | None = Field(None, pattern=KEY_PATTERN)


class Transition(BaseModel):
    decided_by: str = Decider
    note: str | None = Field(None, max_length=300)


class Event(BaseModel):
    from_status: str | None
    to_status: str
    actor: str
    client: str
    at: datetime
    note: str | None


class Decision(BaseModel):
    decision_id: int
    product_id: str
    action: str
    replacement_id: str | None
    reason: str
    decision_status: Status
    source: str
    decided_by: str | None = Field(description="Décideur DÉCLARÉ par le client (non authentifié)")
    recorded_by_client: str = Field(description="Client de service authentifié ayant enregistré la décision")
    decided_at: datetime | None
    idempotency_key: str | None
    created_at: datetime
    idempotent_replay: bool = False


class DecisionCreated(Decision):
    status: Literal["recorded"] = "recorded"  # champ du contrat docs/api.md


class DecisionDetail(Decision):
    events: list[Event]


class DecisionList(BaseModel):
    items: list[Decision]
    total: int


COLUMNS = ("decision_id, product_id, action, replacement_id, reason, status AS decision_status, source, "
           "decided_by, recorded_by_client, decided_at, idempotency_key, created_at")


def _get(conn, decision_id: int) -> dict:
    row = conn.execute(f"SELECT {COLUMNS} FROM catalog.decisions WHERE decision_id = %s", (decision_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Décision inconnue : {decision_id}")
    return row


def _event(conn, decision_id: int, from_status, to_status: str, actor: str, client: str,
           note: str | None = None) -> None:
    conn.execute("INSERT INTO catalog.decision_events (decision_id, from_status, to_status, actor, client, note) "
                 "VALUES (%s, %s, %s, %s, %s, %s)", (decision_id, from_status, to_status, actor, client, note))


def _request_hash(fields: dict) -> str:
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _resolve(body: DecisionCreate, caller: Caller, header_key: str | None) -> tuple[str, str | None]:
    """Statut et décideur déclaré, sans aucune valeur implicite pour un client authentifié."""
    if not caller.authenticated:
        # Mode legacy : uniquement la forme EXACTE de l'appel actuel de l'orchestrateur (PR #1), qui
        # n'est émis qu'après « Valider ». Un appelant anonyme ne choisit ni le statut ni le décideur.
        if body.decision is not None or body.decided_by is not None or body.idempotency_key or header_key \
                or body.model_extra:
            raise HTTPException(422, "mode legacy : « decision », « decided_by » et la clé d'idempotence sont "
                                     "réservés aux clients authentifiés")
        return "approved", settings.legacy_decided_by
    decision, decided_by = body.decision, body.decided_by
    if decision is None:
        raise HTTPException(422, "champ « decision » obligatoire (approved, refused ou pending) : "
                                 "aucune approbation implicite")
    if decision == "pending":
        if decided_by is not None:
            raise HTTPException(422, "« decided_by » interdit pour une proposition en attente")
        return decision, None
    if decided_by is None:
        raise HTTPException(422, "champ « decided_by » obligatoire pour une décision approuvée ou refusée")
    return decision, decided_by


@router.post("/decisions", status_code=201, response_model=DecisionCreated,
             responses={200: {"description": "Rejeu idempotent : décision existante renvoyée"},
                        400: {"description": "Clé d'idempotence absente (client authentifié)"},
                        401: {"description": "Client de service non authentifié"},
                        404: {"description": "Produit inconnu"},
                        409: {"description": "Clé d'idempotence déjà utilisée pour une requête différente"},
                        422: {"description": "Données invalides ou référence fournisseur inexistante"}})
def create_decision(caller: Client, conn: Conn, body: DecisionCreate,
                    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key",
                                                                  pattern=KEY_PATTERN)] = None):
    """Enregistre une décision explicite. Appelé par l'orchestrateur après validation humaine."""
    if idempotency_key and body.idempotency_key and idempotency_key != body.idempotency_key:
        raise HTTPException(422, "clé d'idempotence différente dans l'en-tête et dans le corps")
    key = idempotency_key or body.idempotency_key
    if caller.authenticated and not key:
        # Contrat IETF « Idempotency-Key » : clé obligatoire, vérifiée avant toute autre validation.
        raise HTTPException(400, "clé d'idempotence obligatoire : en-tête Idempotency-Key (ou champ "
                                 "idempotency_key), une valeur stable par action réutilisée à chaque reprise")
    if (body.action == "replace") != (body.replacement_id is not None):
        raise HTTPException(422, "replacement_id est obligatoire pour « replace » et interdit sinon")
    decision, decided_by = _resolve(body, caller, idempotency_key)
    source = "legacy" if not caller.authenticated else ("orchestrator" if caller.client == "orchestrator" else "api")
    request_hash = _request_hash({"product_id": body.product_id, "action": body.action,
                                  "replacement_id": body.replacement_id, "reason": body.reason,
                                  "decision": decision, "decided_by": decided_by})

    with conn.transaction():
        if not conn.execute("SELECT 1 FROM catalog.products WHERE product_id = %s", (body.product_id,)).fetchone():
            raise HTTPException(404, f"Produit inconnu : {body.product_id}")
        if body.replacement_id and not conn.execute(
                "SELECT 1 FROM catalog.supplier_products WHERE supplier_product_id = %s",
                (body.replacement_id,)).fetchone():
            raise HTTPException(422, f"Référence fournisseur inexistante : {body.replacement_id}")
        if not caller.authenticated:  # reprises legacy sans clé : dédupliquées sous verrou consultatif
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (request_hash,))
            previous = conn.execute(
                f"""SELECT {COLUMNS} FROM catalog.decisions WHERE source = 'legacy' AND request_hash = %s
                    AND created_at > now() - make_interval(secs => %s)
                    ORDER BY decision_id DESC LIMIT 1""",
                (request_hash, settings.legacy_dedup_seconds)).fetchone()
            if previous:
                return JSONResponse(status_code=200, content=DecisionCreated(**previous, idempotent_replay=True)
                                    .model_dump(mode="json"))
        row = conn.execute(
            f"""INSERT INTO catalog.decisions (product_id, action, replacement_id, reason, status, source,
                                              proposed_by, decided_by, decided_at, idempotency_key,
                                              recorded_by_client, request_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT decisions_client_idempotency_key DO NOTHING
                RETURNING {COLUMNS}""",
            (body.product_id, body.action, body.replacement_id, body.reason, decision, source, caller.client,
             decided_by, decision != "pending", key, caller.client, request_hash)).fetchone()
        if row is None:  # même client, même clé : rejeu identique ou conflit
            existing = conn.execute(
                f"SELECT {COLUMNS}, request_hash FROM catalog.decisions "
                "WHERE recorded_by_client = %s AND idempotency_key = %s", (caller.client, key)).fetchone()
            if existing.pop("request_hash") != request_hash:
                raise HTTPException(409, "Clé d'idempotence déjà utilisée pour une requête différente")
            replay = existing
        else:
            _event(conn, row["decision_id"], None, decision, decided_by or caller.client, caller.client)
            replay = None
    # Ici la transaction est validée : la réponse de succès ne peut plus être démentie par le COMMIT.
    if replay is not None:
        return JSONResponse(status_code=200, content=DecisionCreated(**replay, idempotent_replay=True)
                            .model_dump(mode="json"))
    return {**row, "idempotent_replay": False}


def _transition(conn, decision_id: int, target: Status, body: Transition, caller: Caller) -> dict:
    with conn.transaction():
        row = conn.execute(
            f"""UPDATE catalog.decisions SET status = %s, decided_by = %s,
                       decided_at = CASE WHEN %s IN ('approved', 'refused') THEN now() END, updated_at = now()
                WHERE decision_id = %s AND status = 'pending' RETURNING {COLUMNS}""",
            (target, body.decided_by, target, decision_id)).fetchone()
        if row:
            _event(conn, decision_id, "pending", target, body.decided_by, caller.client, body.note)
            result = {**row, "idempotent_replay": False}
        else:
            current = _get(conn, decision_id)
            if current["decision_status"] != target:
                raise HTTPException(409, f"Transition interdite : {current['decision_status']} → {target}")
            if current["decided_by"] != body.decided_by:
                raise HTTPException(409, f"Décision déjà « {target} » par un autre décideur déclaré")
            event = conn.execute("SELECT client FROM catalog.decision_events WHERE decision_id = %s "
                                 "AND from_status = 'pending' AND to_status = %s", (decision_id, target)).fetchone()
            if not event:
                raise HTTPException(409, "Décision tranchée dès sa création : aucune transition à rejouer")
            if event["client"] != caller.client:
                raise HTTPException(409, f"Décision déjà « {target} » par un autre client de service")
            result = {**current, "idempotent_replay": True}  # double clic : aucun nouvel événement
    return result


@router.post("/decisions/{decision_id}/approve", response_model=Decision)
def approve(caller: Authenticated, conn: Conn, decision_id: Annotated[int, Path(ge=1)], body: Transition):
    return _transition(conn, decision_id, "approved", body, caller)


@router.post("/decisions/{decision_id}/refuse", response_model=Decision)
def refuse(caller: Authenticated, conn: Conn, decision_id: Annotated[int, Path(ge=1)], body: Transition):
    return _transition(conn, decision_id, "refused", body, caller)


@router.post("/decisions/{decision_id}/cancel", response_model=Decision)
def cancel(caller: Authenticated, conn: Conn, decision_id: Annotated[int, Path(ge=1)], body: Transition):
    return _transition(conn, decision_id, "cancelled", body, caller)


@router.get("/decisions", response_model=DecisionList)
def list_decisions(conn: Conn, status: Status | None = None,
                   product_id: Annotated[str | None, Query(pattern=r"^P\d{3,5}$")] = None,
                   limit: Annotated[int, Query(ge=1, le=500)] = 100):
    """Historique des décisions, des plus récentes aux plus anciennes."""
    params = {"st": status, "p": product_id, "lim": limit}
    where = ("WHERE (%(st)s::text IS NULL OR status = %(st)s::text) "
             "AND (%(p)s::text IS NULL OR product_id = %(p)s::text)")
    total = conn.execute(f"SELECT count(*) AS n FROM catalog.decisions {where}", params).fetchone()["n"]
    rows = conn.execute(f"SELECT {COLUMNS} FROM catalog.decisions {where} "
                        "ORDER BY created_at DESC, decision_id DESC LIMIT %(lim)s", params).fetchall()
    return {"items": rows, "total": total}


@router.get("/decisions/{decision_id}", response_model=DecisionDetail)
def get_decision(conn: Conn, decision_id: Annotated[int, Path(ge=1)]):
    # Décision et journal lus dans le MÊME instantané (transaction courte en REPEATABLE READ) :
    # jamais un statut qui ne correspond pas aux événements renvoyés.
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = _get(conn, decision_id)
        events = conn.execute("SELECT from_status, to_status, actor, client, at, note FROM catalog.decision_events "
                              "WHERE decision_id = %s ORDER BY event_id", (decision_id,)).fetchall()
    return {**row, "events": events}
