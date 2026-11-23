"""Données partagées par les tests des décisions."""

import uuid

TOKEN = "tok-orchestrateur-0123456789abcdef"      # jeton de test uniquement
OTHER_TOKEN = "tok-autre-client-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
OTHER_AUTH = {"Authorization": f"Bearer {OTHER_TOKEN}"}


def auth(token: str = TOKEN, key: str | None = None) -> dict:
    """En-têtes d'un client authentifié, avec une clé d'idempotence (unique si non fournie)."""
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key or f"test-{uuid.uuid4().hex}"}


def decision_body(**overrides) -> dict:
    """Décision explicite et complète ; une valeur None retire le champ."""
    body = {"product_id": "P101", "action": "replace", "replacement_id": "S201",
            "reason": "Ventes en forte baisse et stock élevé", "decision": "approved", "decided_by": "manal"}
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}
