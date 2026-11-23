"""Configuration du service ventes (variables d'environnement)."""

import os


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("SALES_DATABASE_URL") or os.getenv(
            "DATABASE_URL", "postgresql://lso:lso@localhost:5432/lso")
        self.db_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "3"))
        # Hypothèse D-1 (non validée par l'équipe) : dirham marocain. Modifiable sans code.
        self.currency = os.getenv("CURRENCY", "MAD")
        # Date d'analyse : AAAA-MM-JJ ; vide = dernière date de vente présente en base.
        self.as_of = os.getenv("AS_OF_DATE") or None


settings = Settings()
