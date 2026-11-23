"""Jeu de référence minimal, aux résultats calculés À LA MAIN (date d'analyse : 2026-06-30).

Ventes P101 (prix 100, coût 60 sauf promo du 20/06 à 80) :
    janv. 20 · févr. 20 · mars 20 · avr. 10 · mai 5 · juin 10 + 2 (promo)
    juin : unités 12, CA 1000 + 160 = 1160, marge 400 + 40 = 440, taux 440/1160 = 0,3793
    mai  : CA 500  ⇒ évolution du CA = (1160 − 500) / 500 = 1,32
    tendance 6 mois = (10+5+12 − (20+20+20)) / 60 = −0,55
Ventes P102 : juin 3 × 50 (coût 45) ⇒ CA 150, marge 15, taux 0,10 ; rien avant ⇒ tendance nulle.
Ventes P105 : 15/06 6 × 30 (coût 20) ⇒ CA 180, marge 60.
P103, P104 : aucune vente.

Stocks (fenêtre de demande : 30 jours, du 01/06 au 30/06) :
    P101 physique 120, réservé 20, dispo 100 ; sorties 12 ⇒ 0,4/j ⇒ 250 j ⇒ surstock
    P102 physique 0 ⇒ rupture en cours depuis le 05/06 (+ rupture passée 01/02→10/02)
    P103 physique 15, aucune sortie ⇒ demande nulle, couverture non définie
    P104 aucun niveau de stock enregistré ⇒ donnée manquante
    P105 physique 6, réservé 1, dispo 5 ; sorties 6 ⇒ 0,2/j ⇒ 25 j ; seuil 10 ⇒ critique
"""

import psycopg

from data import generate

AS_OF = "2026-06-30"

PRODUCTS = [  # id, nom, catégorie, marque, prix, coût, seuil
    ("P101", "Câble USB-C 1 m", "câbles", "Voltix", 100, 60, 30),
    ("P102", "Souris filaire", "souris", "Kebo", 50, 45, 5),
    ("P103", "Souris sans fil Pro", "souris", "Kebo", 150, 90, 20),
    ("P104", "Câble HDMI 2 m", "câbles", "Atlaz", 80, 40, 0),
    ("P105", "Câble Lightning 1 m", "câbles", "Voltix", 30, 20, 10),
]
SUPPLIER_PRODUCTS = [  # id, fournisseur, nom, catégorie, coût, prix, dispo, stock, délai
    ("S201", "F01", "Câble USB-C tressé 2 m", "câbles", 30, 69, "in_stock", 50, 5),
    ("S202", "F02", "Souris silencieuse", "souris", 50, 120, "out_of_stock", 0, 10),
    ("S203", "F01", "Souris ergonomique", "souris", 60, 150, "limited", 5, 7),
    ("S204", "F02", "Câble HDMI tressé", "câbles", 25, 59, "in_stock", 100, 3),
]
SALES = [  # produit, date, qté, prix, coût
    ("P101", "2026-01-10", 20, 100, 60), ("P101", "2026-02-10", 20, 100, 60), ("P101", "2026-03-10", 20, 100, 60),
    ("P101", "2026-04-10", 10, 100, 60), ("P101", "2026-05-15", 5, 100, 60), ("P101", "2026-06-10", 10, 100, 60),
    ("P101", "2026-06-20", 2, 80, 60), ("P102", "2026-06-05", 3, 50, 45), ("P105", "2026-06-15", 6, 30, 20),
]
STOCK_LEVELS = [("P101", 120, 20), ("P102", 0, 0), ("P103", 15, 0), ("P105", 6, 1)]
INITIAL_STOCK = {"P101": 207, "P102": 3, "P103": 15, "P105": 12}
STOCKOUTS = [("P102", "2026-02-01", "2026-02-10"), ("P102", "2026-06-05", None)]


def load(url: str) -> None:
    with psycopg.connect(url) as c:
        c.execute(f"TRUNCATE {', '.join(generate.TABLES)} RESTART IDENTITY CASCADE")
        c.execute("INSERT INTO catalog.categories VALUES ('câbles', 'Câbles'), ('souris', 'Souris')")
        c.execute("INSERT INTO catalog.suppliers VALUES ('F01', 'Atlas Distribution', 'Maroc', 0.95), "
                  "('F02', 'Rif Electronics', 'Maroc', 0.82)")
        for pid, name, cat, brand, price, cost, reorder in PRODUCTS:
            c.execute("INSERT INTO catalog.products (product_id, name, category, brand, attributes, unit_price, "
                      "unit_cost, created_on) VALUES (%s, %s, %s, %s, %s, %s, %s, '2025-01-01')",
                      (pid, name, cat, brand, '{"exemple": "oui"}', price, cost))
            c.execute("INSERT INTO sales.product_ref VALUES (%s, %s, %s)", (pid, name, cat))
            c.execute("INSERT INTO inventory.product_ref VALUES (%s, %s, %s, %s)", (pid, name, cat, reorder))
        for row in SUPPLIER_PRODUCTS:
            c.execute("INSERT INTO catalog.supplier_products (supplier_product_id, supplier_id, name, category, "
                      "brand, cost, price, availability, stock_available, lead_time_days) "
                      "VALUES (%s, %s, %s, %s, 'Pixa', %s, %s, %s, %s, %s)", row)
        for pid, day, qty, price, cost in SALES:
            c.execute("INSERT INTO sales.sale_lines (product_id, sold_on, quantity, unit_price, unit_cost) "
                      "VALUES (%s, %s, %s, %s, %s)", (pid, day, qty, price, cost))
            c.execute("INSERT INTO inventory.stock_movements (product_id, moved_on, kind, quantity) "
                      "VALUES (%s, %s, 'sale', %s)", (pid, day, -qty))
        for pid, qty in INITIAL_STOCK.items():
            c.execute("INSERT INTO inventory.stock_movements (product_id, moved_on, kind, quantity) "
                      "VALUES (%s, '2026-01-01', 'initial', %s)", (pid, qty))
        for pid, on_hand, reserved in STOCK_LEVELS:
            c.execute("INSERT INTO inventory.stock_levels (product_id, on_hand, reserved, updated_on) "
                      "VALUES (%s, %s, %s, %s)", (pid, on_hand, reserved, AS_OF))
        for row in STOCKOUTS:
            c.execute("INSERT INTO inventory.stockouts (product_id, started_on, ended_on) VALUES (%s, %s, %s)", row)
