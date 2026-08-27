-- agentic_runtime
CREATE TABLE IF NOT EXISTS agentic_runtime_runs (
                    user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    runtime_version TEXT NOT NULL,
                    permission_profile TEXT NOT NULL,
                    model_connection_id TEXT,
                    model_connection_version TEXT,
                    model_connection_model TEXT,
                    external_api_confirmed INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    run_id TEXT,
                    container_name TEXT,
                    workspace_root TEXT,
                    session_file TEXT,
                    request_json TEXT,
                    candidates_json TEXT NOT NULL DEFAULT '[]',
                    verification_json TEXT,
                    verified_candidate_set_hash TEXT,
                    failure_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, task_id, revision)
                );

                CREATE TABLE IF NOT EXISTS agentic_runtime_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_agentic_runtime_events_owner
                ON agentic_runtime_events(user_id, task_id, revision, sequence);

                CREATE TABLE IF NOT EXISTS agentic_runtime_idempotency (
                    user_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS agentic_runtime_coverage (
                    user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    ledger_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, task_id, revision, run_id)
                );

-- model_connections
CREATE TABLE IF NOT EXISTS model_connections (
    connection_id   TEXT PRIMARY KEY,
    owner_scope     TEXT NOT NULL,
    owner_user_id   TEXT,
    preset_id       TEXT,
    preset_version  TEXT,
    display_name    TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    model           TEXT NOT NULL,
    api_format      TEXT NOT NULL,
    locality        TEXT NOT NULL,
    secret_id       TEXT,
    status          TEXT NOT NULL,
    key_hint        TEXT NOT NULL DEFAULT '',
    verified_at     TEXT,
    compatibility_slot TEXT,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_connections_owner
ON model_connections(owner_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS model_connection_secrets (
    secret_id       TEXT PRIMARY KEY,
    owner_user_id   TEXT,
    ciphertext      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_connection_models (
    connection_id    TEXT NOT NULL,
    model_id         TEXT NOT NULL,
    display_name     TEXT NOT NULL,
    catalog_role     TEXT NOT NULL,
    catalog_version  TEXT NOT NULL,
    catalog_order    INTEGER NOT NULL,
    status           TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 0,
    verified_at      TEXT,
    error_code       TEXT,
    usage_status     TEXT NOT NULL DEFAULT 'unknown',
    native_usage_json TEXT NOT NULL DEFAULT '{}',
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (connection_id, model_id)
);
CREATE INDEX IF NOT EXISTS idx_model_connection_models_status
ON model_connection_models(connection_id, status, enabled);

CREATE TABLE IF NOT EXISTS model_connection_grants (
    grant_id         TEXT PRIMARY KEY,
    token_hash       TEXT NOT NULL UNIQUE,
    owner_user_id    TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    revision         INTEGER NOT NULL,
    run_id           TEXT NOT NULL,
    connection_id    TEXT NOT NULL,
    secret_id        TEXT,
    purpose          TEXT NOT NULL,
    base_url         TEXT NOT NULL,
    model            TEXT NOT NULL,
    api_format       TEXT NOT NULL,
    locality         TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    revoked_at       TEXT,
    revoke_reason    TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_connection_grants_run
ON model_connection_grants(
    owner_user_id, task_id, revision, run_id, purpose
);

CREATE TABLE IF NOT EXISTS model_provider_usage (
    usage_id         TEXT PRIMARY KEY,
    grant_id         TEXT NOT NULL,
    owner_user_id    TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    revision         INTEGER NOT NULL,
    run_id           TEXT NOT NULL,
    connection_id    TEXT NOT NULL,
    purpose          TEXT NOT NULL,
    status           TEXT NOT NULL,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    total_tokens     INTEGER,
    request_count    INTEGER NOT NULL DEFAULT 1,
    native_json      TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_provider_usage_task
ON model_provider_usage(owner_user_id, task_id, revision, created_at);

CREATE TABLE IF NOT EXISTS model_usage_preferences (
    owner_user_id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_connection_imports (
    source_scope TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    connection_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_scope, source_key, source_fingerprint)
);

-- conversation_steering
CREATE TABLE IF NOT EXISTS conversation_raw_turns (
    turn_id          TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    revision         INTEGER NOT NULL,
    text              TEXT NOT NULL,
    idempotency_key   TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE (owner_id, task_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_crt_owner_task_revision
ON conversation_raw_turns(owner_id, task_id, revision, created_at);
CREATE TABLE IF NOT EXISTS conversation_context_deltas (
    delta_id          TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    turn_id          TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (owner_id, turn_id)
);
CREATE INDEX IF NOT EXISTS idx_ccd_owner_task
ON conversation_context_deltas(owner_id, task_id, created_at);
CREATE TABLE IF NOT EXISTS conversation_revision_proposals (
    proposal_id      TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crp_owner_task
ON conversation_revision_proposals(owner_id, task_id, created_at);
CREATE TABLE IF NOT EXISTS conversation_revision_decisions (
    decision_id      TEXT PRIMARY KEY,
    proposal_id      TEXT NOT NULL,
    owner_id         TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    base_revision    INTEGER NOT NULL,
    status           TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (owner_id, proposal_id)
);
CREATE INDEX IF NOT EXISTS idx_crd_owner_task_status
ON conversation_revision_decisions(owner_id, task_id, status, updated_at);
CREATE TABLE IF NOT EXISTS conversation_steering_results (
    result_id        TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    turn_id          TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (owner_id, turn_id)
);
CREATE INDEX IF NOT EXISTS idx_csr_owner_task
ON conversation_steering_results(owner_id, task_id, created_at);

-- delivery_publishing
CREATE TABLE IF NOT EXISTS delivery_publish_intents (
                    publication_key TEXT PRIMARY KEY,
                    command_hash TEXT NOT NULL,
                    request_idempotency_hash TEXT,
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_revision INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    commit_token TEXT,
                    staging_dir TEXT,
                    final_dir TEXT,
                    delivery_id TEXT,
                    manifest_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_dpi_owner_run
                ON delivery_publish_intents(owner_id, run_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS formal_delivery_runs (
                    delivery_id TEXT PRIMARY KEY,
                    publication_key TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_revision INTEGER NOT NULL,
                    candidate_set_hash TEXT NOT NULL,
                    verification_report_id TEXT NOT NULL,
                    verification_report_hash TEXT NOT NULL,
                    delivery_spec_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fdr_owner_run
                ON formal_delivery_runs(owner_id, run_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS formal_delivery_outputs (
                    output_id TEXT PRIMARY KEY,
                    delivery_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    format TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    qa_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (delivery_id) REFERENCES formal_delivery_runs(delivery_id)
                );
                CREATE INDEX IF NOT EXISTS idx_fdo_owner_run
                ON formal_delivery_outputs(owner_id, run_id, created_at DESC);

-- capability_catalog
-- AC-04 前向迁移：只新增能力目录与冻结选择，不改写 Legacy Skill、模板或历史任务。
CREATE TABLE IF NOT EXISTS capability_pack_versions (
    owner_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    deprecated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_key, pack_id, version)
);
CREATE INDEX IF NOT EXISTS idx_capability_pack_digest
ON capability_pack_versions(digest);

CREATE TABLE IF NOT EXISTS automation_procedure_versions (
    owner_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    procedure_id TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_key, procedure_id, version)
);
CREATE INDEX IF NOT EXISTS idx_automation_procedure_digest
ON automation_procedure_versions(digest);

CREATE TABLE IF NOT EXISTS capability_validations (
    owner_key TEXT NOT NULL,
    validation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_key, validation_id)
);

CREATE TABLE IF NOT EXISTS capability_components (
    owner_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    component_id TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_key, component_id, version)
);

CREATE TABLE IF NOT EXISTS capability_selections (
    owner_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    selection_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, task_id, revision),
    UNIQUE (selection_id)
);
