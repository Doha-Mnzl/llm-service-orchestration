"""Calculs métier du service ventes : fonctions pures, testables sans base.

Définitions (hypothèse D-4, à valider avec Analysis) :
- période de N mois = N mois glissants se terminant à la date d'analyse (incluse) ;
- chiffre d'affaires = Σ quantité × prix unitaire ;
- marge brute = Σ quantité × (prix unitaire − coût unitaire) ;
- taux de marge = marge brute / CA (indéfini si CA = 0) ;
- évolution = (valeur courante − valeur précédente) / valeur précédente (indéfinie si précédente = 0) ;
- trend_6m = évolution des unités des 3 derniers mois par rapport aux 3 mois précédents.
"""

import unicodedata
from datetime import date, timedelta


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30,
            31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def _month_end(d: date) -> bool:
    return (d + timedelta(days=1)).day == 1


def months_before(as_of: date, n: int) -> date:
    """Date située n mois avant as_of, en restant aligné sur les fins de mois."""
    d = add_months(as_of, -n)
    if _month_end(as_of):  # 30/06 − 1 mois = 31/05, pas 30/05
        d = add_months(d.replace(day=1), 1) - timedelta(days=1)
    return d


def period_bounds(as_of: date, months: int) -> tuple[date, date]:
    """Bornes incluses des `months` mois glissants se terminant à as_of."""
    return months_before(as_of, months) + timedelta(days=1), as_of


def previous_period_bounds(as_of: date, months: int) -> tuple[date, date]:
    """Période précédente de même durée, bornes dérivées DIRECTEMENT de as_of (jamais en chaîne),
    donc identiques aux fenêtres mensuelles (revue Codex : 30/03 ⇒ 31/01–28/02 partout)."""
    return months_before(as_of, 2 * months) + timedelta(days=1), months_before(as_of, months)


def monthly_windows(as_of: date, months: int) -> list[tuple[date, date]]:
    """Fenêtres mensuelles successives, de la plus ancienne à la plus récente."""
    return [(months_before(as_of, k) + timedelta(days=1), months_before(as_of, k - 1))
            for k in range(months, 0, -1)]


def ratio(numerator: float, denominator: float) -> float | None:
    return None if not denominator else round(numerator / denominator, 4)


def change(current: float, previous: float) -> float | None:
    return None if not previous else round((current - previous) / previous, 4)


def trend(monthly_units: list[int]) -> float | None:
    """Évolution des 3 derniers mois par rapport aux 3 précédents (liste de 6 mois)."""
    return change(sum(monthly_units[-3:]), sum(monthly_units[-6:-3]))


def normalize(text: str) -> str:
    """Minuscules sans accents : « Câbles » → « cables »."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


ACCENTS_FROM = "àâäáãéèêëîïíìôöóòõùûüúçñÀÂÄÁÃÉÈÊËÎÏÍÌÔÖÓÒÕÙÛÜÚÇÑ"
ACCENTS_TO = "aaaaaeeeeiiiiooooouuuucnaaaaaeeeeiiiiooooouuuucn"


def sql_normalized(column: str) -> str:
    """Expression SQL équivalente à normalize() (sans extension unaccent)."""
    return f"lower(translate({column}, '{ACCENTS_FROM}', '{ACCENTS_TO}'))"
