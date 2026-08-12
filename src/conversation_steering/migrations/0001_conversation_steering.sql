-- AC-00 前向迁移草案：仅新增可空/独立表，不删除或改写现有数据。
CREATE TABLE IF NOT EXISTS conversation_raw_turns (
    turn_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    text TEXT NOT NULL,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (owner_id, task_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_crt_owner_task_revision
ON conversation_raw_turns(owner_id, task_id, revision, created_at);

CREATE TABLE IF NOT EXISTS conversation_context_deltas (
    delta_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (owner_id, turn_id)
);
CREATE INDEX IF NOT EXISTS idx_ccd_owner_task
ON conversation_context_deltas(owner_id, task_id, created_at);

CREATE TABLE IF NOT EXISTS conversation_revision_proposals (
    proposal_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crp_owner_task
ON conversation_revision_proposals(owner_id, task_id, created_at);

CREATE TABLE IF NOT EXISTS conversation_revision_decisions (
    decision_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (owner_id, proposal_id)
);
CREATE INDEX IF NOT EXISTS idx_crd_owner_task_status
ON conversation_revision_decisions(owner_id, task_id, status, updated_at);

CREATE TABLE IF NOT EXISTS conversation_steering_results (
    result_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (owner_id, turn_id)
);
CREATE INDEX IF NOT EXISTS idx_csr_owner_task
ON conversation_steering_results(owner_id, task_id, created_at);
