"""Accès PostgreSQL du service (psycopg 3, requêtes paramétrées).

Règle transactionnelle (correctif H3) : la connexion est en AUTOCOMMIT. Une instruction isolée
est donc validée avant que la route ne réponde ; une écriture en plusieurs instructions doit
être faite dans `with conn.transaction():` À L'INTÉRIEUR de la route, ce qui garantit
l'atomicité et un COMMIT effectué AVANT la réponse de succès. La dépendance ne commite jamais :
avec FastAPI ≥ 0.118, son code de sortie s'exécute après l'envoi de la réponse.

Erreurs : base injoignable ⇒ 503 (l'orchestrateur réessaie) ; autre erreur PostgreSQL ⇒ 500
sans détail interne (rien n'a été validé).
"""

import logging
from collections.abc import Iterator

import psycopg
from fastapi import Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

from services.catalog.config import settings

log = logging.getLogger(__name__)


def get_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True,
                           connect_timeout=settings.db_timeout)
    try:
        yield conn
    finally:
        conn.close()


def database_up() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=settings.db_timeout) as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False


async def database_unavailable(_: Request, exc: psycopg.OperationalError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "base de données indisponible"})


async def database_error(_: Request, exc: psycopg.Error) -> JSONResponse:
    log.error("erreur PostgreSQL, transaction annulée : %s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "erreur de base de données : opération annulée"})


# OperationalError d'abord : Starlette choisit le gestionnaire de la classe la plus proche.
EXCEPTION_HANDLERS = [(psycopg.OperationalError, database_unavailable), (psycopg.Error, database_error)]
