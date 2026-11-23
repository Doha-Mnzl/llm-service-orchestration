-- Mode de compatibilité legacy (revue finale) : les écritures NON authentifiées reçoivent une
-- source dédiée ('legacy'), distincte des écritures du client authentifié « orchestrator ».
-- Index d'appui pour la déduplication des reprises legacy (même empreinte, fenêtre courte).

ALTER TABLE catalog.decisions DROP CONSTRAINT decisions_source_check;
ALTER TABLE catalog.decisions
    ADD CONSTRAINT decisions_source_check CHECK (source IN ('orchestrator', 'ui', 'seed', 'api', 'legacy'));
CREATE INDEX decisions_legacy_dedup_idx ON catalog.decisions (request_hash, created_at)
    WHERE source = 'legacy';
