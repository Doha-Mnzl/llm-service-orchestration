-- Catalog service — schéma `catalog` (propriétaire : service catalogue, Partie 3).
-- Domaines : produits (fiche maître), catégories, fournisseurs, produits fournisseurs,
-- décisions humaines et leur journal d'audit.
-- Aucun autre service ne lit ni n'écrit ce schéma : ils passent par l'API REST.

CREATE SCHEMA IF NOT EXISTS catalog;

CREATE TABLE catalog.categories (
    name        TEXT PRIMARY KEY CHECK (length(name) BETWEEN 2 AND 50),
    label       TEXT NOT NULL
);

CREATE TABLE catalog.products (
    product_id  VARCHAR(6) PRIMARY KEY CHECK (product_id ~ '^P[0-9]{3,5}$'),
    name        TEXT NOT NULL CHECK (length(name) BETWEEN 2 AND 120),
    category    TEXT NOT NULL REFERENCES catalog.categories(name),
    brand       TEXT NOT NULL,
    attributes  JSONB NOT NULL DEFAULT '{}'::jsonb,
    unit_price  NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    unit_cost   NUMERIC(12,2) NOT NULL CHECK (unit_cost >= 0),
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'discontinued')),
    created_on  DATE NOT NULL
);
CREATE INDEX products_category_idx ON catalog.products (category);

CREATE TABLE catalog.suppliers (
    supplier_id TEXT PRIMARY KEY CHECK (supplier_id ~ '^F[0-9]{2,4}$'),
    name        TEXT NOT NULL UNIQUE,
    country     TEXT NOT NULL,
    reliability NUMERIC(3,2) NOT NULL CHECK (reliability BETWEEN 0 AND 1)
);

CREATE TABLE catalog.supplier_products (
    supplier_product_id VARCHAR(6) PRIMARY KEY CHECK (supplier_product_id ~ '^S[0-9]{3,5}$'),
    supplier_id     TEXT NOT NULL REFERENCES catalog.suppliers(supplier_id),
    name            TEXT NOT NULL CHECK (length(name) BETWEEN 2 AND 120),
    category        TEXT NOT NULL REFERENCES catalog.categories(name),
    brand           TEXT NOT NULL,
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb,
    cost            NUMERIC(12,2) NOT NULL CHECK (cost >= 0),          -- prix d'achat
    price           NUMERIC(12,2) NOT NULL CHECK (price >= 0),         -- prix de vente estimé
    availability    TEXT NOT NULL CHECK (availability IN ('in_stock', 'limited', 'out_of_stock')),
    stock_available INTEGER NOT NULL CHECK (stock_available >= 0),
    lead_time_days  INTEGER NOT NULL CHECK (lead_time_days BETWEEN 1 AND 365),
    CHECK ((availability = 'out_of_stock') = (stock_available = 0))
);
CREATE INDEX supplier_products_category_idx ON catalog.supplier_products (category, price);

-- Décisions humaines. Une décision n'est jamais appliquée automatiquement :
-- elle est enregistrée (approved/refused) uniquement après validation explicite.
CREATE TABLE catalog.decisions (
    decision_id     BIGSERIAL PRIMARY KEY,
    product_id      VARCHAR(6) NOT NULL REFERENCES catalog.products(product_id),
    action          TEXT NOT NULL CHECK (action IN ('remove', 'replace', 'keep')),
    replacement_id  VARCHAR(6) REFERENCES catalog.supplier_products(supplier_product_id),
    reason          TEXT NOT NULL CHECK (length(reason) BETWEEN 3 AND 300),
    status          TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'refused', 'cancelled')),
    source          TEXT NOT NULL DEFAULT 'orchestrator' CHECK (source IN ('orchestrator', 'ui', 'seed', 'api')),
    proposed_by     TEXT,
    decided_by      TEXT,
    decided_at      TIMESTAMPTZ,
    idempotency_key TEXT UNIQUE CHECK (idempotency_key IS NULL OR length(idempotency_key) BETWEEN 1 AND 100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((action = 'replace') = (replacement_id IS NOT NULL)),
    -- une décision tranchée a forcément un auteur et une date
    CHECK ((status IN ('approved', 'refused')) = (decided_at IS NOT NULL)),
    CHECK (status NOT IN ('approved', 'refused') OR decided_by IS NOT NULL)
);
CREATE INDEX decisions_product_idx ON catalog.decisions (product_id, created_at DESC);
CREATE INDEX decisions_status_idx ON catalog.decisions (status);

-- Journal d'audit en ajout seul.
CREATE TABLE catalog.decision_events (
    event_id    BIGSERIAL PRIMARY KEY,
    decision_id BIGINT NOT NULL REFERENCES catalog.decisions(decision_id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    actor       TEXT NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    note        TEXT
);
CREATE INDEX decision_events_decision_idx ON catalog.decision_events (decision_id, event_id);

CREATE FUNCTION catalog.forbid_event_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'decision_events est en ajout seul (audit)';
END $$;
CREATE TRIGGER decision_events_append_only
    BEFORE UPDATE OR DELETE ON catalog.decision_events
    FOR EACH ROW EXECUTE FUNCTION catalog.forbid_event_change();
