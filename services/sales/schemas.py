"""Schémas de réponse du service ventes (documentés dans OpenAPI : /docs).

Les champs du contrat proposé dans docs/api.md (PR #1) sont conservés à l'identique ;
les autres champs sont des AJOUTS rétrocompatibles.
"""

from datetime import date

from pydantic import BaseModel, Field


class SalesItem(BaseModel):
    product_id: str = Field(examples=["P003"])
    name: str
    category: str
    units_sold: int
    revenue: float = Field(description="Chiffre d'affaires de la période")
    gross_margin: float = Field(description="Marge brute de la période")
    margin_rate: float | None = Field(description="Marge / CA ; null si CA nul")
    trend_6m: float | None = Field(description="Évolution des unités : 3 derniers mois vs 3 précédents")
    previous_revenue: float = Field(description="CA de la période précédente de même durée")
    revenue_change: float | None = Field(description="Évolution du CA ; null si CA précédent nul")


class SalesSummary(BaseModel):
    period_months: int
    as_of: date = Field(description="Date d'analyse : toutes les bornes en dérivent")
    start: date
    end: date
    previous_start: date
    previous_end: date
    currency: str
    items: list[SalesItem]


class Period(BaseModel):
    start: date
    end: date


class SalesTrend(BaseModel):
    product_id: str
    months: int
    monthly_units: list[int] = Field(description="Du mois le plus ancien au plus récent")
    monthly_revenue: list[float]
    periods: list[Period]
    currency: str


class PeriodStats(BaseModel):
    start: date
    end: date
    units: int
    revenue: float
    gross_margin: float
    margin_rate: float | None


class Changes(BaseModel):
    units: float | None
    revenue: float | None
    gross_margin: float | None


class ProductSales(BaseModel):
    product_id: str
    name: str
    category: str
    period_months: int
    current: PeriodStats
    previous: PeriodStats
    changes: Changes
    trend_6m: float | None
    currency: str


class MonthTotal(BaseModel):
    month: str = Field(examples=["2026-10"])
    start: date
    end: date
    units: int
    revenue: float
    gross_margin: float


class MonthlyTotals(BaseModel):
    currency: str
    category: str | None
    product_id: str | None
    items: list[MonthTotal]


class Health(BaseModel):
    status: str
    service: str
    database: str
