-- Sécurité des décisions (audit Codex, H2 et H4).
-- recorded_by_client : identité du client AUTHENTIFIÉ (jeton de service), jamais un champ du corps.
-- request_hash       : empreinte SHA-256 des champs significatifs, pour comparer les rejeux.
-- La clé d'idempotence devient propre à chaque client (deux clients ne peuvent pas se bloquer).
-- Les lignes existantes reçoivent 'seed' (démonstration) ou 'avant-002' : avant cette migration,
-- aucune identité n'était authentifiée. Aucune donnée supprimée ; le journal d'audit (en ajout
-- seul) garde 'avant-002'. Les clés d'idempotence antérieures ne sont pas rejouables : aucune
-- n'a été émise par l'orchestrateur avant 002 (il n'en envoie pas).

ALTER TABLE catalog.decisions
    ADD COLUMN recorded_by_client TEXT NOT NULL DEFAULT 'avant-002'
        CHECK (length(recorded_by_client) BETWEEN 1 AND 50),
    ADD COLUMN request_hash TEXT CHECK (request_hash ~ '^[0-9a-f]{64}$');
UPDATE catalog.decisions SET recorded_by_client = 'seed' WHERE source = 'seed';
ALTER TABLE catalog.decisions ALTER COLUMN recorded_by_client DROP DEFAULT;

ALTER TABLE catalog.decisions DROP CONSTRAINT decisions_idempotency_key_key;
ALTER TABLE catalog.decisions
    ADD CONSTRAINT decisions_client_idempotency_key UNIQUE (recorded_by_client, idempotency_key);

ALTER TABLE catalog.decision_events
    ADD COLUMN client TEXT NOT NULL DEFAULT 'avant-002' CHECK (length(client) BETWEEN 1 AND 50);
ALTER TABLE catalog.decision_events ALTER COLUMN client DROP DEFAULT;
