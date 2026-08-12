-- AC-05 前向迁移草案：只新增能力获取记录，不执行生产迁移。
CREATE TABLE IF NOT EXISTS capability_acquisition_runs (
    acquisition_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capability_acquisition_owner_status
ON capability_acquisition_runs(owner_id, status);
