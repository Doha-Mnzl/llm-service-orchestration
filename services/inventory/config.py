"""Configuration du service stocks (variables d'environnement)."""

import os


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("INVENTORY_DATABASE_URL") or os.getenv(
            "DATABASE_URL", "postgresql://lso:lso@localhost:5432/lso")
        self.db_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "3"))
        # Date d'analyse : AAAA-MM-JJ ; vide = date du dernier niveau de stock enregistré.
        self.as_of = os.getenv("AS_OF_DATE") or None
        # Hypothèses D-4 (non validées) : fenêtre de demande et seuil de surstock (repris des mocks).
        self.demand_window_days = int(os.getenv("DEMAND_WINDOW_DAYS", "30"))
        self.overstock_days = int(os.getenv("OVERSTOCK_DAYS", "180"))


settings = Settings()
