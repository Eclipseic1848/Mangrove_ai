-- AC-07：只新增能力治理事实，不改写能力目录、冻结选择或历史任务。
CREATE TABLE IF NOT EXISTS capability_governance_events (
    event_id TEXT NOT NULL PRIMARY KEY,
    owner_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    UNIQUE (owner_key, pack_id, version, digest, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_capability_governance_target
ON capability_governance_events(owner_key, pack_id, version, digest, occurred_at);
