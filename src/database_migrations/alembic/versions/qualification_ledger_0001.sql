CREATE TABLE IF NOT EXISTS qualification_ledger_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version TEXT NOT NULL,
    ledger_id TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qualification_batches (
    batch_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    provider_set_sha256 TEXT NOT NULL,
    expected_commit TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    relay_base_url TEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL CHECK (timeout_seconds > 0 AND timeout_seconds <= 7200),
    authorized_by TEXT NOT NULL,
    authorization_reason TEXT NOT NULL,
    batch_kind TEXT NOT NULL CHECK (batch_kind IN ('initial', 'successor')),
    parent_batch_id TEXT REFERENCES qualification_batches(batch_id),
    previous_evidence_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('authorized', 'in_progress', 'passed', 'failed', 'outcome_unknown')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_g4_active_provider_set
ON qualification_batches(provider_set_sha256)
WHERE state IN ('authorized', 'in_progress');

CREATE TABLE IF NOT EXISTS qualification_batch_providers (
    batch_id TEXT NOT NULL REFERENCES qualification_batches(batch_id),
    provider_key TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    connection_version TEXT NOT NULL,
    preset_id TEXT NOT NULL,
    model TEXT NOT NULL,
    api_format TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('authorized', 'retry_authorized', 'in_progress', 'passed', 'failed_after_egress', 'outcome_unknown')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0 AND attempt_count <= 2),
    check_json TEXT,
    PRIMARY KEY (batch_id, provider_key)
);

CREATE TABLE IF NOT EXISTS qualification_provider_attempts (
    batch_id TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number IN (1, 2)),
    state TEXT NOT NULL CHECK (state IN ('in_progress', 'passed', 'failed_after_egress', 'outcome_unknown')),
    attempt_context_json TEXT NOT NULL,
    check_json TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (batch_id, provider_key, attempt_number),
    FOREIGN KEY (batch_id, provider_key) REFERENCES qualification_batch_providers(batch_id, provider_key)
);

CREATE TABLE IF NOT EXISTS qualification_retry_authorizations (
    batch_id TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    retry_number INTEGER NOT NULL CHECK (retry_number = 1),
    authorized_by TEXT NOT NULL,
    authorization_reason TEXT NOT NULL,
    user_confirmed_duplicate_request_and_cost INTEGER NOT NULL CHECK (user_confirmed_duplicate_request_and_cost = 1),
    previous_state TEXT NOT NULL CHECK (previous_state IN ('outcome_unknown', 'failed_after_egress')),
    authorized_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, provider_key, retry_number),
    FOREIGN KEY (batch_id, provider_key) REFERENCES qualification_batch_providers(batch_id, provider_key)
);

CREATE TABLE IF NOT EXISTS qualification_ledger_recoveries (
    recovery_id TEXT PRIMARY KEY,
    recovery_kind TEXT NOT NULL CHECK (recovery_kind IN ('pre_egress_anchor_sync_failed', 'stale_in_progress_outcome_unknown')),
    batch_id TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number IN (1, 2)),
    attempt_context_sha256 TEXT NOT NULL,
    anchor_revision INTEGER NOT NULL CHECK (anchor_revision >= 0),
    ledger_revision_before INTEGER NOT NULL,
    recovered_revision INTEGER NOT NULL,
    recovered_by TEXT NOT NULL,
    recovery_reason TEXT NOT NULL,
    recovered_at TEXT NOT NULL,
    FOREIGN KEY (batch_id, provider_key) REFERENCES qualification_batch_providers(batch_id, provider_key)
);
