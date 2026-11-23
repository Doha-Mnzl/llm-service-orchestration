"""Formats d'affichage (français) et libellés des statuts : jamais la couleur seule."""

CURRENCY_LABELS = {"MAD": "DH", "EUR": "€", "USD": "$"}

COVERAGE = {"stockout": "⛔ Rupture", "critical": "⚠️ Critique", "overstock": "📦 Surstock",
            "no_demand": "💤 Sans demande", "ok": "✅ Normal", "missing_data": "❔ Donnée manquante"}
TRACE = {"ok": "✅ Réussi", "failed": "⛔ Échec", "rejected": "🚫 Refusé par le contrôle",
         "pending_validation": "⏳ En attente de validation"}
DECISION = {"approved": "✅ Validée", "refused": "✖️ Refusée", "pending": "⏳ En attente", "cancelled": "↩️ Annulée"}
ACTION = {"replace": "Remplacer", "remove": "Retirer", "keep": "Conserver"}
AVAILABILITY = {"in_stock": "en stock", "limited": "stock limité", "out_of_stock": "indisponible"}
TOOLS = {"get_sales_summary": "Synthèse des ventes", "get_sales_trend": "Évolution des ventes",
         "get_stock_levels": "Niveaux de stock", "get_stock_alerts": "Alertes de stock",
         "find_underperforming_products": "Produits peu performants", "find_replacements": "Remplacements",
         "search_supplier_catalog": "Catalogue fournisseurs", "record_decision": "Enregistrer une décision"}


def currency_label(code: str | None) -> str:
    return CURRENCY_LABELS.get(code or "", code or "")


def number(value: float | int | None, decimals: int = 0) -> str:
    if value is None:
        return "—"
    s = f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return s.replace(" ", " ")


def money(value: float | None, currency: str | None, decimals: int = 2) -> str:
    return "—" if value is None else f"{number(value, decimals)} {currency_label(currency)}"


def percent(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{number(value * 100, 1)} %"
