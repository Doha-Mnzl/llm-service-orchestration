"""Authentification des clients de service pour les ÉCRITURES de décisions (correctif H2).

- `Authorization: Bearer <jeton>` comparé en temps constant aux jetons de CATALOG_SERVICE_TOKENS ;
  l'identité retenue est le NOM DU CLIENT associé au jeton, jamais un champ du corps
  (`decided_by` reste une déclaration de l'appelant, enregistrée comme telle).
- Sans jeton : refus (401), sauf si DECISIONS_LEGACY_ORCHESTRATOR=true (mode de compatibilité
  explicite avec l'orchestrateur actuel, tracé comme client « legacy-unauthenticated »).
- Un jeton présenté mais invalide est TOUJOURS refusé, même en mode legacy.
- Le mode legacy ne couvre que POST /decisions : les transitions exigent un jeton valide.
Les lectures (GET) restent ouvertes au réseau interne (risque résiduel documenté).
"""

import hmac
import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from services.catalog.config import settings

log = logging.getLogger(__name__)
LEGACY_CLIENT = "legacy-unauthenticated"


@dataclass(frozen=True)
class Caller:
    client: str
    authenticated: bool


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def require_service_client(authorization: Annotated[str | None, Header()] = None) -> Caller:
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise _unauthorized("en-tête Authorization invalide (attendu : Bearer <jeton>)")
        matched = None
        for client, expected in settings.service_tokens.items():  # parcours complet : temps constant
            if hmac.compare_digest(token.encode(), expected.encode()):
                matched = client
        if matched is None:
            raise _unauthorized("jeton de service invalide")
        return Caller(matched, True)
    if settings.legacy_orchestrator:
        log.warning("écriture de décision NON authentifiée acceptée (DECISIONS_LEGACY_ORCHESTRATOR=true)")
        return Caller(LEGACY_CLIENT, False)
    raise _unauthorized("authentification de service requise (Authorization: Bearer <jeton>)")


def require_authenticated_client(caller: Annotated[Caller, Depends(require_service_client)]) -> Caller:
    """Transitions (approve/refuse/cancel) : jamais accessibles en mode legacy non authentifié."""
    if not caller.authenticated:
        raise _unauthorized("authentification de service requise pour modifier une décision existante")
    return caller
