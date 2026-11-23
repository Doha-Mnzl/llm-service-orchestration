-- Sales service — schéma `sales` (propriétaire : service ventes, Partie 3).
-- `product_ref` est une copie de référence des produits (id, nom, catégorie),
-- alimentée par le générateur : pas de clé étrangère vers un autre schéma.

CREATE SCHEMA IF NOT EXISTS sales;

CREATE TABLE sales.product_ref (
    product_id  VARCHAR(6) PRIMARY KEY CHECK (product_id ~ '^P[0-9]{3,5}$'),
    name        TEXT NOT NULL,
    category    TEXT NOT NULL
);

-- Une ligne = ventes d'un produit sur une journée, avec prix et coût unitaires
-- historisés (le prix peut changer : promotions, hausses fournisseur).
CREATE TABLE sales.sale_lines (
    sale_id     BIGSERIAL PRIMARY KEY,
    product_id  VARCHAR(6) NOT NULL REFERENCES sales.product_ref(product_id),
    sold_on     DATE NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    unit_cost   NUMERIC(12,2) NOT NULL CHECK (unit_cost >= 0),
    UNIQUE (product_id, sold_on)
);
CREATE INDEX sale_lines_date_idx ON sales.sale_lines (sold_on);
