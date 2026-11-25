"""Configuration de l'orchestrateur, lue depuis les variables d'environnement.

Tout se règle sans toucher au code :
    LLM_PROVIDER=fake|groq|ollama
    USE_MOCK_SERVICES=true|false
    FAIL_SERVICES=inventory,catalog   (simule des pannes en mode mock)
"""

import os


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "oui")


def _list(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {x.strip() for x in raw.split(",") if x.strip()}


class Settings:
    def __init__(self) -> None:
        # --- LLM ---
        self.llm_provider = os.getenv("LLM_PROVIDER", "fake").lower()
        self.llm_timeout = float(os.getenv("LLM_TIMEOUT", "30"))
        self.llm_retries = int(os.getenv("LLM_RETRIES", "2"))
        self.groq_api_key = os.getenv("GROQ_API_KEY", "")
        self.groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")  # llama-3.3-70b retiré par Groq (août 2026)
        self.groq_url = os.getenv("GROQ_URL", "https://api.groq.com/openai/v1")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1")

        # --- Services ---
        self.use_mock_services = _bool("USE_MOCK_SERVICES", "true")
        self.fail_services = _list("FAIL_SERVICES")
        self.service_urls = {
            "sales": os.getenv("SALES_URL", "http://localhost:8001"),
            "inventory": os.getenv("INVENTORY_URL", "http://localhost:8002"),
            "catalog": os.getenv("CATALOG_URL", "http://localhost:8003"),
        }
        self.rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
        # Jeton de service pour écrire les décisions dans le catalogue (Authorization: Bearer).
        # Doit correspondre à l'entrée « orchestrator:<jeton> » de CATALOG_SERVICE_TOKENS côté catalogue.
        self.catalog_token = os.getenv("CATALOG_TOKEN", "")

        # --- Contrôle et fiabilité ---
        self.http_timeout = float(os.getenv("HTTP_TIMEOUT", "5"))
        self.max_retries = int(os.getenv("MAX_RETRIES", "2"))
        self.max_tool_calls = int(os.getenv("MAX_TOOL_CALLS", "15"))
        self.max_llm_steps = int(os.getenv("MAX_LLM_STEPS", "15"))


settings = Settings()
