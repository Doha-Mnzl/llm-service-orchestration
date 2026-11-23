-- Inventory service — schéma `inventory` (propriétaire : service stocks, Partie 3).
-- Stock physique (on_hand), réservé, disponible (= physique - réservé),
-- mouvements signés et historique des ruptures.

CREATE SCHEMA IF NOT EXISTS inventory;

CREATE TABLE inventory.product_ref (
    product_id    VARCHAR(6) PRIMARY KEY CHECK (product_id ~ '^P[0-9]{3,5}$'),
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    reorder_point INTEGER NOT NULL DEFAULT 0 CHECK (reorder_point >= 0)  -- seuil d'alerte
);

CREATE TABLE inventory.stock_levels (
    product_id  VARCHAR(6) PRIMARY KEY REFERENCES inventory.product_ref(product_id),
    on_hand     INTEGER NOT NULL CHECK (on_hand >= 0),
    reserved    INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    available   INTEGER GENERATED ALWAYS AS (on_hand - reserved) STORED,
    updated_on  DATE NOT NULL,
    CHECK (reserved <= on_hand)
);

-- Quantité signée : + entrée (initial, receipt, return), - sortie (sale, adjustment négatif).
CREATE TABLE inventory.stock_movements (
    movement_id BIGSERIAL PRIMARY KEY,
    product_id  VARCHAR(6) NOT NULL REFERENCES inventory.product_ref(product_id),
    moved_on    DATE NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('initial', 'receipt', 'sale', 'return', 'adjustment')),
    quantity    INTEGER NOT NULL CHECK (quantity <> 0),
    CHECK (kind <> 'sale' OR quantity < 0),
    CHECK (kind NOT IN ('initial', 'receipt', 'return') OR quantity > 0)
);
CREATE INDEX stock_movements_product_idx ON inventory.stock_movements (product_id, moved_on);

CREATE TABLE inventory.stockouts (
    stockout_id BIGSERIAL PRIMARY KEY,
    product_id  VARCHAR(6) NOT NULL REFERENCES inventory.product_ref(product_id),
    started_on  DATE NOT NULL,
    ended_on    DATE,
    CHECK (ended_on IS NULL OR ended_on >= started_on)
);
-- au plus une rupture en cours par produit
CREATE UNIQUE INDEX stockouts_one_open_idx ON inventory.stockouts (product_id) WHERE ended_on IS NULL;
