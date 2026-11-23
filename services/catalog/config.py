"""Configuration du service catalogue (variables d'environnement)."""

import logging
import os
import re

log = logging.getLogger(__name__)
MIN_TOKEN_LENGTH = 16
RESERVED_CLIENTS = {"seed", "avant-002", "legacy-unauthenticated"}  # identités internes
_CLIENT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")


def parse_service_tokens(raw: str) -> dict[str, str]:
    """« client:jeton,client2:jeton2 » → {client: jeton}. Les entrées invalides sont ignorées
    (et signalées sans jamais écrire le jeton dans les journaux)."""
    tokens: dict[str, str] = {}
    entries = [e.strip() for e in raw.split(",")]
    for position, entry in enumerate(entries, 1):
        if not entry:
            continue
        name, sep, token = entry.partition(":")
        name, token = name.strip(), token.strip()
        # Journal : position et cause seulement, JAMAIS la valeur (une entrée sans « : » peut être un jeton).
        cause = ("séparateur « : » absent" if not sep else "nom de client invalide" if not _CLIENT_NAME.match(name)
                 else f"jeton de moins de {MIN_TOKEN_LENGTH} caractères" if len(token) < MIN_TOKEN_LENGTH
                 else "nom de client réservé" if name in RESERVED_CLIENTS else None)
        if cause:
            log.warning("CATALOG_SERVICE_TOKENS : entrée %d ignorée (%s)", position, cause)
            continue
        tokens[name] = token
    shared = {t for t in tokens.values() if list(tokens.values()).count(t) > 1}
    if shared:  # un même jeton pour deux clients rendrait l'identité ambiguë : aucun n'est retenu
        log.warning("CATALOG_SERVICE_TOKENS : jeton partagé par plusieurs clients, entrées ignorées")
    return {name: token for name, token in tokens.items() if token not in shared}


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("CATALOG_DATABASE_URL") or os.getenv(
            "DATABASE_URL", "postgresql://lso:lso@localhost:5432/lso")
        self.db_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "3"))
        # Hypothèse D-1 (non validée par l'équipe) : dirham marocain.
        self.currency = os.getenv("CURRENCY", "MAD")
        # Écritures de décisions : jetons de service (Authorization: Bearer <jeton>). Aucun jeton ⇒ refus.
        self.service_tokens = parse_service_tokens(os.getenv("CATALOG_SERVICE_TOKENS", ""))
        # Compatibilité TEMPORAIRE avec l'orchestrateur de la PR #1, qui n'envoie ni jeton ni `decision` :
        # désactivée par défaut, réservée à la démonstration locale (voir docs/partie3/decisions-equipe.md).
        self.legacy_orchestrator = os.getenv("DECISIONS_LEGACY_ORCHESTRATOR", "false").strip().lower() in (
            "1", "true", "yes", "oui")
        self.legacy_decided_by = (os.getenv("LEGACY_DECIDED_BY") or "").strip()[:100] or \
            "inconnu (orchestrateur non authentifié)"
        # Reprises legacy (sans clé possible) : une décision identique dans cette fenêtre est un rejeu.
        self.legacy_dedup_seconds = int(os.getenv("LEGACY_DEDUP_SECONDS", "60"))


settings = Settings()
