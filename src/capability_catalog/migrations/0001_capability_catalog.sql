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
