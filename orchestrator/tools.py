"""Liste blanche des outils que le LLM a le droit de demander.

Chaque outil est relié à un service et à une route. Les arguments sont validés
par un modèle pydantic avant tout appel : le LLM ne touche jamais la base.

- critical=True  : si le service tombe, on arrête l'analyse (ventes, analyse).
- critical=False : si le service tombe, réponse partielle avec avertissement (stocks, catalogue).
- requires_validation=True : l'action n'est exécutée qu'après validation humaine.
"""

from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Args(BaseModel):
    # Refuse tout argument inconnu inventé par le LLM.
    model_config = ConfigDict(extra="forbid")


# ---------- Arguments de chaque outil ----------

class SalesSummaryArgs(_Args):
    period_months: int = Field(1, ge=1, le=24, description="Période analysée en mois")
    category: Optional[str] = Field(None, max_length=50, description="Catégorie (optionnel)")


class SalesTrendArgs(_Args):
    product_id: str = Field(..., pattern=r"^P\d{3,5}$", description="Identifiant produit, ex. P012")
    months: int = Field(6, ge=1, le=24)


class StockLevelsArgs(_Args):
    product_ids: Optional[list[str]] = Field(None, max_length=50)
    category: Optional[str] = Field(None, max_length=50)


class StockAlertsArgs(_Args):
    kind: Literal["overstock", "stockout", "all"] = "all"


class UnderperformingArgs(_Args):
    period_months: int = Field(1, ge=1, le=24)
    top_n: int = Field(5, ge=1, le=50)


class ReplacementsArgs(_Args):
    product_id: str = Field(..., pattern=r"^P\d{3,5}$")
    max_results: int = Field(3, ge=1, le=10)


class CatalogSearchArgs(_Args):
    category: str = Field(..., max_length=50)
    query: Optional[str] = Field(None, max_length=100)
    max_price: Optional[float] = Field(None, gt=0)


class RecordDecisionArgs(_Args):
    product_id: str = Field(..., pattern=r"^P\d{3,5}$")
    action: Literal["remove", "replace", "keep"]
    replacement_id: Optional[str] = Field(None, pattern=r"^S\d{3,5}$")
    reason: str = Field(..., min_length=3, max_length=300)


# ---------- Registre ----------

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    service: Literal["sales", "inventory", "catalog", "analysis"]
    method: Literal["GET", "POST", "QUEUE"]
    path: str
    args_model: type[BaseModel]
    critical: bool
    requires_validation: bool = False


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool("get_sales_summary", "Ventes, chiffre d'affaires et marge par produit sur une période.",
             "sales", "GET", "/sales/summary", SalesSummaryArgs, critical=True),
        Tool("get_sales_trend", "Évolution mensuelle des ventes d'un produit.",
             "sales", "GET", "/sales/trend/{product_id}", SalesTrendArgs, critical=True),
        Tool("get_stock_levels", "Stock disponible et jours de couverture.",
             "inventory", "GET", "/stocks", StockLevelsArgs, critical=False),
        Tool("get_stock_alerts", "Produits en surstock ou en rupture.",
             "inventory", "GET", "/stocks/alerts", StockAlertsArgs, critical=False),
        Tool("find_underperforming_products", "Produits peu performants (analyse des ventes).",
             "analysis", "QUEUE", "analysis.underperforming", UnderperformingArgs, critical=True),
        Tool("find_replacements", "Produits fournisseurs pouvant remplacer un produit.",
             "analysis", "QUEUE", "analysis.replacements", ReplacementsArgs, critical=True),
        Tool("search_supplier_catalog", "Recherche dans le catalogue fournisseurs.",
             "catalog", "GET", "/catalog/search", CatalogSearchArgs, critical=False),
        Tool("record_decision", "Enregistre une décision (retrait/remplacement). Exige une validation humaine.",
             "catalog", "POST", "/decisions", RecordDecisionArgs, critical=False,
             requires_validation=True),
    ]
}


def to_openai_schema() -> list[dict]:
    """Format « tools » attendu par Groq et Ollama (API compatible OpenAI)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.args_model.model_json_schema(),
            },
        }
        for t in TOOLS.values()
    ]
