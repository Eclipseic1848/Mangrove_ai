-- AC-07 #34：验证运行和 Lease 仅新增，不改写既有治理事实。
CREATE TABLE IF NOT EXISTS capability_validation_runs (
    run_id TEXT NOT NULL PRIMARY KEY,
    owner_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (owner_id, digest, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_capability_validation_owner_target
ON capability_validation_runs(owner_id, pack_id, version, digest, created_at);

-- 活动运行合并后，新请求的幂等键也必须永久指向原 Run；否则响应丢失后的重试会重复执行。
CREATE TABLE IF NOT EXISTS capability_validation_idempotency (
    owner_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    PRIMARY KEY (owner_id, digest, idempotency_key),
    FOREIGN KEY (run_id) REFERENCES capability_validation_runs(run_id)
);

CREATE TABLE IF NOT EXISTS capability_validation_leases (
    digest TEXT NOT NULL PRIMARY KEY,
    run_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES capability_validation_runs(run_id)
);
