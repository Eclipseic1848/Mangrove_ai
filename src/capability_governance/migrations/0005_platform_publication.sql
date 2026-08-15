-- AC-07 #12：平台验证运行与 Lease 仅新增；发布事件复用 capability_governance_events
-- 的 payload_json 模式，事件表零 ALTER。
CREATE TABLE IF NOT EXISTS capability_platform_validation_runs (
    run_id TEXT NOT NULL PRIMARY KEY,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (pack_id, version, digest, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_capability_platform_validation_target
ON capability_platform_validation_runs(pack_id, version, digest, created_at);

-- 平台验证 digest Lease：并发 worker 不得重复执行外部步骤（Trivy/Syft/探针）。
CREATE TABLE IF NOT EXISTS capability_platform_validation_leases (
    digest TEXT NOT NULL PRIMARY KEY,
    run_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES capability_platform_validation_runs(run_id)
);
