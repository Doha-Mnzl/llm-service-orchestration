"""Règles de couverture du service stocks : fonctions pures.

Hypothèses D-4 (à valider avec Analysis) :
- demande moyenne = sorties « sale » des N derniers jours / N (N = 30 par défaut) ;
- couverture (jours) = stock disponible / demande moyenne, arrondie à l'entier ;
- statut, par priorité :
    missing_data  aucun niveau de stock enregistré (valeur manquante) ;
    stockout      disponible ≤ 0 (couverture = 0) ;
    no_demand     demande nulle et stock > 0 : couverture non définie (null) ;
    critical      disponible ≤ seuil d'alerte (reorder_point) ;
    overstock     couverture > seuil de surstock (180 j, valeur reprise des mocks) ;
    ok            sinon.
"""


def average_daily_demand(units_out: int, window_days: int) -> float:
    return round(units_out / window_days, 4) if window_days > 0 else 0.0


def coverage(available: int | None, units_out: int, window_days: int, reorder_point: int,
             overstock_days: int) -> tuple[int | None, str]:
    """Couverture calculée sur la demande EXACTE (unités / jours), arrondie une seule fois à la fin
    (revue Codex : 20 unités, 1 sortie sur 30 j ⇒ 600 jours, pas 601)."""
    if available is None:
        return None, "missing_data"
    if available <= 0:
        return 0, "stockout"
    if units_out <= 0 or window_days <= 0:
        return None, "no_demand"
    days = (2 * available * window_days + units_out) // (2 * units_out)  # arrondi au plus proche (½ ⇒ haut)
    if available <= reorder_point:
        return days, "critical"
    if days > overstock_days:
        return days, "overstock"
    return days, "ok"
