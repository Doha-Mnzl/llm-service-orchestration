"""Générateur de données fictives reproductible (Partie 3, LSO-6 / LSO-21).

Même graine + même date de référence ⇒ exactement les mêmes données (bibliothèque
standard uniquement, pas de Faker, pour éviter toute dérive entre versions).

Le stock est SIMULÉ jour par jour : les ventes sont plafonnées par le stock, les
commandes fournisseurs arrivent après un délai, et les ruptures sont déduites de la
simulation. Ventes (schéma sales) et mouvements de stock (schéma inventory) sont donc
cohérents par construction : Σ mouvements = stock physique.

    python -m data.generate --seed 42 --reference-date 2026-10-31 [--reset | --if-empty]

--if-empty : ne fait rien si la base contient déjà des données (démarrage et redémarrage sûrs).
--reset    : vide les tables ; REFUSÉ s'il existe des décisions humaines, sauf --allow-decision-loss.
    python -m data.verify
"""

import argparse
import math
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import psycopg
from psycopg.types.json import Jsonb

from data import migrate
from data.catalog_spec import (BLACK_FRIDAY_DISCOUNT, BRANDS, CATEGORIES, MOCK_PRODUCTS,
                               MOCK_SUPPLIER_PRODUCTS, MONTH_FACTOR, PRODUCTS_PER_CATEGORY,
                               RANDOM_PROFILES, SCENARIOS, SUPPLIER_PRODUCTS_TOTAL, SUPPLIERS,
                               TEMPLATES, VARIANTS, WEEKDAY_FACTOR)

DEFAULT_REFERENCE_DATE = date(2026, 10, 31)
HISTORY_MONTHS = 18
CENT = Decimal("0.01")

TABLES = ("catalog.decision_events", "catalog.decisions", "catalog.supplier_products", "catalog.suppliers",
          "catalog.products", "catalog.categories", "sales.sale_lines", "sales.product_ref",
          "inventory.stockouts", "inventory.stock_movements", "inventory.stock_levels", "inventory.product_ref")


class SeedError(RuntimeError):
    pass


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30,
            31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def money(x: float) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def black_friday(year: int) -> date:
    d = date(year, 11, 30)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


@dataclass
class Product:
    product_id: str
    name: str
    category: str
    brand: str
    attributes: dict
    price: Decimal
    cost: Decimal
    base_demand: float
    profile: str
    lead_time: int
    reorder_point: int = 0
    sales: list = field(default_factory=list)       # (jour, qté, prix, coût)
    movements: list = field(default_factory=list)   # (jour, type, qté signée)
    stockouts: list = field(default_factory=list)   # [début, fin|None]
    on_hand: int = 0


def profile_factor(profile: str, t: float, days_left: int) -> float:
    if profile == "growth":
        return 0.65 + 0.7 * t
    if profile == "mild_decline":
        return 1.25 - 0.45 * t
    if profile in ("decline_overstock", "decline_stockout"):
        return 1.5 if t < 0.55 else max(0.1, 1.5 - (t - 0.55) / 0.45 * 1.4)
    if profile == "dead":
        return 0.0 if days_left < 75 else 1.0 - 0.4 * t
    return 1.0


def _fill(template: str, attrs: dict, rng: random.Random) -> tuple[str, dict]:
    values = {k: rng.choice(v) for k, v in VARIANTS.items()}
    return template.format(**values), {k: str(v).format(**values) for k, v in attrs.items()}


def build_products(rng: random.Random) -> list[Product]:
    cats = {c[0]: c for c in CATEGORIES}
    products: list[Product] = []

    def make(pid, name, cat, brand, attrs, price, margin):
        _, _, _, (dmin, dmax) = cats[cat]
        profile = SCENARIOS.get(pid) or rng.choices([p for p, _ in RANDOM_PROFILES],
                                                     [w for _, w in RANDOM_PROFILES])[0]
        products.append(Product(pid, name, cat, brand, attrs, money(price), money(price * (1 - margin)),
                                round(rng.uniform(dmin, dmax), 2), profile, rng.randint(4, 12)))

    for pid, (name, cat, price, margin) in MOCK_PRODUCTS.items():
        make(pid, name, cat, rng.choice(BRANDS), {}, price, margin)
    counts = {c[0]: sum(1 for p in products if p.category == c[0]) for c in CATEGORIES}
    names = {p.name for p in products}
    next_id = len(products) + 1
    for cat, _, (pmin, pmax), _ in CATEGORIES:
        while counts[cat] < PRODUCTS_PER_CATEGORY:
            name, attrs = _fill(*rng.choice(TEMPLATES[cat]), rng)
            if name in names:
                continue
            names.add(name)
            make(f"P{next_id:03d}", name, cat, rng.choice(BRANDS), attrs,
                 round(rng.uniform(pmin, pmax)), rng.uniform(0.18, 0.45))
            counts[cat] += 1
            next_id += 1
    return products


def simulate(p: Product, start: date, end: date, rng: random.Random) -> None:
    """Simulation quotidienne : demande, ventes plafonnées par le stock, réassort, ruptures."""
    horizon = (end - start).days
    # seuil d'alerte exposé par Inventory = stock de sécurité (½ délai de demande),
    # distinct du déclencheur de réassort de la simulation (délai + 5 jours)
    p.reorder_point = math.ceil(p.base_demand * p.lead_time * 0.5)
    stock = math.ceil(p.base_demand * 1.5 * (p.lead_time + 30))
    p.movements.append((start, "initial", stock))
    pending: dict[date, int] = {}
    open_stockout = None
    bulk_done = False
    delayed = False
    bf_days = {black_friday(y) + timedelta(days=k) for y in (start.year, end.year) for k in range(-1, 4)}

    day = start
    while day <= end:
        t, days_left = (day - start).days / horizon, (end - day).days
        # 1. réceptions du jour
        if day in pending:
            qty = pending.pop(day)
            stock += qty
            p.movements.append((day, "receipt", qty))
            if open_stockout:
                p.stockouts.append([open_stockout, day])
                open_stockout = None
        # surstock : commande massive par erreur, puis plus aucun réassort
        if p.profile == "decline_overstock" and not bulk_done and t >= 0.6:
            qty = math.ceil(p.base_demand * 250)
            stock += qty
            p.movements.append((day, "receipt", qty))
            bulk_done = True
            if open_stockout:
                p.stockouts.append([open_stockout, day])
                open_stockout = None
        if p.profile == "dead" and days_left == 76:
            stock += 30
            p.movements.append((day, "receipt", 30))
            if open_stockout:
                p.stockouts.append([open_stockout, day])
                open_stockout = None

        # 2. demande et ventes
        factor = profile_factor(p.profile, t, days_left)
        season = MONTH_FACTOR[day.month] ** (2 if p.profile == "strong_seasonal" else 1)
        lam = p.base_demand * factor * season * WEEKDAY_FACTOR[day.weekday()]
        price = p.price
        if day in bf_days:
            lam *= 2
            price = money(float(p.price) * (1 - BLACK_FRIDAY_DISCOUNT))
        sold = min(stock, poisson(rng, lam))
        if sold:
            stock -= sold
            p.sales.append((day, sold, price, p.cost))
            p.movements.append((day, "sale", -sold))
        if stock > 0 and rng.random() < 0.004:  # casse / démarque inconnue
            stock -= 1
            p.movements.append((day, "adjustment", -1))

        # 3. réassort (politique point de commande)
        expected = p.base_demand * max(factor, 0.0)
        no_reorder = (p.profile == "decline_overstock" and t >= 0.6) or \
                     (p.profile == "decline_stockout" and days_left < 120) or \
                     (p.profile == "dead" and days_left < 90)
        if not no_reorder and not pending and expected > 0 and stock <= math.ceil(expected * (p.lead_time + 5)):
            lead = p.lead_time
            if p.profile == "supply_issue" and not delayed and t >= 0.45:
                lead += 25  # retard fournisseur ⇒ rupture passée
                delayed = True
            qty = max(1, math.ceil(expected * (p.lead_time + 30)) - stock)
            pending[day + timedelta(days=lead)] = qty

        # 4. rupture
        if stock == 0 and open_stockout is None:
            open_stockout = day
        day += timedelta(days=1)

    if open_stockout:
        p.stockouts.append([open_stockout, None])
    p.on_hand = stock


def build_supplier_products(rng: random.Random) -> list[tuple]:
    cats = {c[0]: c for c in CATEGORIES}
    per_cat = SUPPLIER_PRODUCTS_TOTAL // len(CATEGORIES)
    rows, names = [], set()

    def add(sid, name, cat, price, availability=None):
        avail = availability or rng.choices(["in_stock", "limited", "out_of_stock"], [0.7, 0.2, 0.1])[0]
        stock = {"in_stock": rng.randint(20, 500), "limited": rng.randint(1, 19), "out_of_stock": 0}[avail]
        rows.append((sid, rng.choice(SUPPLIERS)[0], name, cat, rng.choice(BRANDS), Jsonb({}),
                     money(price * rng.uniform(0.45, 0.7)), money(price), avail, stock, rng.randint(2, 30)))
        names.add(name)

    for sid, (name, cat, price) in MOCK_SUPPLIER_PRODUCTS.items():
        add(sid, name, cat, price, "in_stock")
    next_id = 101 + len(rows)
    for cat, _, (pmin, pmax), _ in CATEGORIES:
        while sum(1 for r in rows if r[3] == cat) < per_cat:
            name, attrs = _fill(*rng.choice(TEMPLATES[cat]), rng)
            name = f"{name} {rng.choice(BRANDS)}"
            if name in names:
                continue
            add(f"S{next_id}", name, cat, round(rng.uniform(pmin, pmax) * rng.uniform(0.9, 1.25)))
            rows[-1] = rows[-1][:5] + (Jsonb(attrs),) + rows[-1][6:]
            next_id += 1
    # au moins 3 candidats disponibles par catégorie (garantie pour Analysis)
    for cat in cats:
        avail = [i for i, r in enumerate(rows) if r[3] == cat and r[8] == "in_stock"]
        for i in [i for i, r in enumerate(rows) if r[3] == cat and r[8] != "in_stock"][:max(0, 3 - len(avail))]:
            rows[i] = rows[i][:8] + ("in_stock", rng.randint(20, 500)) + rows[i][10:]
    return rows


def run(database_url: str, seed: int = 42, reference_date: date = DEFAULT_REFERENCE_DATE,
        reset: bool = False, if_empty: bool = False, allow_decision_loss: bool = False) -> dict:
    """Migre si besoin puis remplit la base. Renvoie un résumé des volumes.

    Base non vide : `if_empty` ⇒ rien n'est modifié ({"skipped": True}) ; `reset` ⇒ tables vidées,
    sauf s'il existe des décisions humaines et que `allow_decision_loss` est faux (SeedError).
    """
    migrate.migrate(database_url)
    with psycopg.connect(database_url) as conn:
        populated = conn.execute("SELECT count(*) FROM catalog.products").fetchone()[0] > 0
        human = migrate.human_decisions(conn)
    if populated and if_empty and not reset:
        return {"skipped": True}
    if populated and not reset:
        raise SeedError("la base contient déjà des données : utiliser --if-empty (ne rien faire) "
                        "ou --reset (régénérer)")
    if reset and human and not allow_decision_loss:
        raise SeedError(f"{human} décision(s) humaine(s) seraient effacées : régénération refusée "
                        "(option --allow-decision-loss pour forcer, après sauvegarde)")

    rng = random.Random(seed)
    start = add_months(reference_date, -HISTORY_MONTHS) + timedelta(days=1)
    products = build_products(rng)
    for p in products:
        simulate(p, start, reference_date, rng)
    supplier_rows = build_supplier_products(rng)

    with psycopg.connect(database_url) as conn:
        # Deux démarrages peuvent calculer le seed en parallèle. Un seul publie ses lignes.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (migrate.MIGRATION_LOCK,))
        if not reset and conn.execute("SELECT EXISTS (SELECT FROM catalog.products)").fetchone()[0]:
            if if_empty:
                return {"skipped": True}
            raise SeedError("la base contient déjà des données : utiliser --if-empty ou --reset")
        if reset:  # même transaction que le remplissage : tout ou rien
            # verrou exclusif AVANT le comptage : aucune décision ne peut être validée entre les deux
            conn.execute("LOCK TABLE catalog.decisions IN ACCESS EXCLUSIVE MODE")
            if migrate.human_decisions(conn) and not allow_decision_loss:
                raise SeedError("des décisions humaines ont été ajoutées entre-temps : régénération annulée")
            conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")

        cur = conn.cursor()
        cur.executemany("INSERT INTO catalog.categories VALUES (%s, %s)", [(c[0], c[1]) for c in CATEGORIES])
        cur.executemany("INSERT INTO catalog.suppliers VALUES (%s, %s, %s, %s)", SUPPLIERS)
        cur.executemany(
            "INSERT INTO catalog.products (product_id, name, category, brand, attributes, unit_price, unit_cost, "
            "created_on) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(p.product_id, p.name, p.category, p.brand, Jsonb(p.attributes), p.price, p.cost, start)
             for p in products])
        cur.executemany(
            "INSERT INTO catalog.supplier_products (supplier_product_id, supplier_id, name, category, brand, "
            "attributes, cost, price, availability, stock_available, lead_time_days) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", supplier_rows)
        cur.executemany("INSERT INTO sales.product_ref VALUES (%s, %s, %s)",
                        [(p.product_id, p.name, p.category) for p in products])
        cur.executemany("INSERT INTO inventory.product_ref VALUES (%s, %s, %s, %s)",
                        [(p.product_id, p.name, p.category, p.reorder_point) for p in products])

        with cur.copy("COPY sales.sale_lines (product_id, sold_on, quantity, unit_price, unit_cost) FROM STDIN") as cp:
            for p in products:
                for day, qty, price, cost in p.sales:
                    cp.write_row((p.product_id, day, qty, price, cost))
        with cur.copy("COPY inventory.stock_movements (product_id, moved_on, kind, quantity) FROM STDIN") as cp:
            for p in products:
                for day, kind, qty in p.movements:
                    cp.write_row((p.product_id, day, kind, qty))
        cur.executemany("INSERT INTO inventory.stock_levels (product_id, on_hand, reserved, updated_on) "
                        "VALUES (%s, %s, %s, %s)",
                        [(p.product_id, p.on_hand, rng.randint(0, p.on_hand // 10) if p.on_hand else 0,
                          reference_date) for p in products])
        cur.executemany("INSERT INTO inventory.stockouts (product_id, started_on, ended_on) VALUES (%s, %s, %s)",
                        [(p.product_id, s, e) for p in products for s, e in p.stockouts])
        _demo_decisions(cur, products, supplier_rows, reference_date)
    return {"products": len(products), "supplier_products": len(supplier_rows),
            "sale_lines": sum(len(p.sales) for p in products),
            "stock_movements": sum(len(p.movements) for p in products),
            "stockouts": sum(len(p.stockouts) for p in products)}


def _demo_decisions(cur, products, supplier_rows, ref: date) -> None:
    """Deux décisions historiques de démonstration (une validée, une refusée), avec audit."""
    by_id = {p.product_id: p for p in products}
    replacement = next(r[0] for r in supplier_rows if r[3] == by_id["P042"].category and r[8] == "in_stock")
    demo = [("P042", "replace", replacement, "Ventes en forte baisse et stock élevé", "approved", 20),
            ("P013", "remove", None, "Plus aucune vente depuis deux mois", "refused", 10)]
    for i, (pid, action, rep, reason, status, days_ago) in enumerate(demo, 1):
        proposed = datetime.combine(ref - timedelta(days=days_ago), time(9, 0), timezone.utc)
        decided = proposed + timedelta(hours=1)
        did = cur.execute(
            "INSERT INTO catalog.decisions (product_id, action, replacement_id, reason, status, source, proposed_by, "
            "decided_by, decided_at, idempotency_key, created_at, updated_at, recorded_by_client) "
            "VALUES (%s, %s, %s, %s, %s, 'seed', 'orchestrateur', 'manal', %s, %s, %s, %s, 'seed') "
            "RETURNING decision_id",
            (pid, action, rep, reason, status, decided, f"seed-{i}", proposed, decided)).fetchone()[0]
        cur.executemany("INSERT INTO catalog.decision_events (decision_id, from_status, to_status, actor, at, note, "
                        "client) VALUES (%s, %s, %s, %s, %s, %s, 'seed')",
                        [(did, None, "pending", "orchestrateur", proposed, "proposition de démonstration"),
                         (did, "pending", status, "manal", decided, None)])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL", migrate.DEFAULT_URL))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--reference-date", type=date.fromisoformat, default=DEFAULT_REFERENCE_DATE)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--reset", action="store_true", help="vider les tables de la Partie 3 avant de générer")
    mode.add_argument("--if-empty", action="store_true", help="ne rien faire si la base contient déjà des données")
    p.add_argument("--allow-decision-loss", action="store_true",
                   help="autoriser --reset à effacer des décisions humaines (irréversible)")
    a = p.parse_args(argv)
    try:
        summary = run(a.database_url, a.seed, a.reference_date, a.reset, a.if_empty, a.allow_decision_loss)
    except (psycopg.Error, SeedError, migrate.MigrationError) as e:
        print(f"ÉCHEC : {e}", file=sys.stderr)
        return 1
    if summary.get("skipped"):
        print("Base déjà remplie : aucune donnée modifiée (--if-empty).")
        return 0
    print(f"Données générées (graine {a.seed}, référence {a.reference_date}) : "
          + ", ".join(f"{k}={v}" for k, v in summary.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
