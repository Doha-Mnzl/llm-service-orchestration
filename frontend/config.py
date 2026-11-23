"""Configuration de l'interface (variables d'environnement).

L'interface ne connaît PAS le fournisseur LLM : elle ne parle qu'à l'orchestrateur
pour le chat et les validations. Les services métier ne sont lus qu'en GET, pour les
tableaux de bord (hypothèse D-5, désactivable avec DASHBOARD_DIRECT_READ=false).
"""

import os


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "oui")


class Settings:
    def __init__(self) -> None:
        self.orchestrator_url = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")
        self.service_urls = {
            "sales": os.getenv("SALES_URL", "http://localhost:8001"),
            "inventory": os.getenv("INVENTORY_URL", "http://localhost:8002"),
            "catalog": os.getenv("CATALOG_URL", "http://localhost:8003"),
        }
        self.direct_read = _bool("DASHBOARD_DIRECT_READ", "true")
        self.http_timeout = float(os.getenv("UI_HTTP_TIMEOUT", "5"))
        self.chat_timeout = float(os.getenv("UI_CHAT_TIMEOUT", "90"))  # boucle LLM + reprises
        # Validation : l'orchestrateur peut réessayer l'écriture (3 tentatives × 5 s + attentes).
        # Un délai plus court produirait de faux « résultat inconnu ».
        self.decision_timeout = float(os.getenv("UI_DECISION_TIMEOUT", "30"))
        self.store_name = os.getenv("STORE_NAME", "SmartRetail — accessoires informatiques")


settings = Settings()
