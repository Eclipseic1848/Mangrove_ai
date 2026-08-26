CREATE TABLE candidate_reverification_authorities (
    authority_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    run_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (
        purpose = 'semantic_inconclusive_reverification'
    ),
    candidate_set_hash TEXT NOT NULL,
    evidence_manifest_json TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (owner_id, idempotency_key),
    UNIQUE (
        owner_id,
        task_id,
        revision,
        run_id,
        candidate_set_hash,
        purpose
    )
);

CREATE TRIGGER candidate_reverification_authorities_insert_guard
BEFORE INSERT ON candidate_reverification_authorities
WHEN historical_authority_write_allowed() != 1
BEGIN
    SELECT RAISE(ABORT, '历史重验权威只能由受控 Repository 追加');
END;

CREATE TRIGGER candidate_reverification_authorities_no_update
BEFORE UPDATE ON candidate_reverification_authorities
BEGIN
    SELECT RAISE(ABORT, '历史重验权威不可改写');
END;

CREATE TRIGGER candidate_reverification_authorities_no_delete
BEFORE DELETE ON candidate_reverification_authorities
BEGIN
    SELECT RAISE(ABORT, '历史重验权威不可删除');
END;

ALTER TABLE candidate_verification_migrations ADD COLUMN ddl_sha256 TEXT;

CREATE TRIGGER candidate_verification_migrations_no_update
BEFORE UPDATE ON candidate_verification_migrations
BEGIN
    SELECT RAISE(ABORT, '候选验证迁移记录不可改写');
END;

CREATE TRIGGER candidate_verification_migrations_no_delete
BEFORE DELETE ON candidate_verification_migrations
BEGIN
    SELECT RAISE(ABORT, '候选验证迁移记录不可删除');
END;
