CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'user',   -- admin | user（RBAC 双角色）
    disabled      INTEGER NOT NULL DEFAULT 0,      -- 1=禁用，禁止登录
    pending       INTEGER NOT NULL DEFAULT 0,      -- 1=待管理员审批，审批前禁止登录
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS user_ui_state (
    user_id   TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS conversations (
    conv_id       TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '新会话',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id       TEXT NOT NULL,
    role          TEXT NOT NULL,          -- user | assistant
    content       TEXT NOT NULL,
    task_id       TEXT,                   -- 关联的 conductor 任务 id（产出文件按此定位）
    meta_json     TEXT,                   -- 富信息 JSON：files/grade/collector/token_usage 等（供重载会话重建展示）
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    conv_id     TEXT NOT NULL,            -- 冗余，便于按会话查反馈
    user_id     TEXT NOT NULL,
    rating      TEXT NOT NULL,            -- 'up' | 'down'
    reasons     TEXT,                     -- JSON 数组（点踩原因）
    comment     TEXT,                     -- 自由描述
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/resolved/ignored（管理员处理状态）
    admin_note  TEXT,                             -- 管理员处理备注
    UNIQUE(message_id, user_id)           -- 一人一消息一反馈（覆盖更新）
);
CREATE TABLE IF NOT EXISTS runtime_config (
    scope      TEXT NOT NULL,               -- 'global' 或 user_id（按用户隔离的凭证覆盖）
    key        TEXT NOT NULL,               -- settings 字段名（白名单见 runtime_config.REGISTRY）
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS cookie_health (
    key        TEXT PRIMARY KEY,            -- 配置键，如 mc_cookie_xhs / jd_cookie
    status     TEXT NOT NULL,               -- valid | invalid | unknown
    message    TEXT,                        -- 人话原因；失效/无法判断时给出线索
    checked_at TEXT NOT NULL,
    checked_by TEXT NOT NULL                -- manual | scheduled
);
CREATE TABLE IF NOT EXISTS user_memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,               -- 归属用户，按用户隔离（区别于全局共享的 memory/user-preferences.md）
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS library_dedup_scan_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at               TEXT NOT NULL,
    templates_scanned    INTEGER NOT NULL,
    templates_merged     INTEGER NOT NULL,
    lessons_scanned      INTEGER NOT NULL,
    lessons_merged       INTEGER NOT NULL,
    stale_drafts_deleted INTEGER NOT NULL,
    details              TEXT NOT NULL DEFAULT ''   -- 该轮每步操作（合并/清理）的 JSON 明细数组
);
CREATE TABLE IF NOT EXISTS memory_hit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    hit_at        TEXT NOT NULL,
    hit_type      TEXT NOT NULL,    -- lesson / template / skill
    slug          TEXT NOT NULL,    -- 命中的 slug；未命中为空串
    threshold     REAL NOT NULL,    -- 命中阈值（rerank 分数/余弦值/0=关键词兜底或未命中）
    degrade_path  TEXT NOT NULL,    -- semantic / keyword / none（none=无候选，semantic=召回过但被筛空）
    task_id       TEXT,
    hit           INTEGER NOT NULL DEFAULT 1  -- 1=命中 0=未命中（E3：分母也要记，才能算命中率）
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id);
CREATE INDEX IF NOT EXISTS idx_mem_user ON user_memory(user_id);
CREATE TABLE IF NOT EXISTS data_prep_tasks (
    task_id       TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    unit_id       TEXT,
    spec_json     TEXT NOT NULL,               -- DataPrepTaskSpec 序列化
    status        TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING/SUCCEEDED/SUCCEEDED_WITH_WARNINGS/FAILED
    record_counts TEXT,                        -- JSON 账本
    quality_json  TEXT,                        -- QualityReport 序列化
    manifest_path TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dpt_user ON data_prep_tasks(user_id);
CREATE TABLE IF NOT EXISTS data_prep_task_uploads (
    task_id       TEXT NOT NULL,
    upload_id     TEXT NOT NULL,
    ordinal       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, upload_id),
    FOREIGN KEY (task_id) REFERENCES data_prep_tasks(task_id)
);
CREATE INDEX IF NOT EXISTS idx_dptu_upload ON data_prep_task_uploads(upload_id);
CREATE TABLE IF NOT EXISTS document_task_units (
    unit_id       TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    unit_type     TEXT NOT NULL,                -- single_file | file_set
    name          TEXT NOT NULL,
    business_type TEXT NOT NULL DEFAULT '',
    archived_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dtu_user ON document_task_units(user_id, updated_at);
CREATE TABLE IF NOT EXISTS document_task_unit_members (
    unit_id       TEXT NOT NULL,
    upload_id     TEXT NOT NULL,
    ordinal       INTEGER NOT NULL DEFAULT 0,
    added_at      TEXT NOT NULL,
    PRIMARY KEY (unit_id, upload_id),
    FOREIGN KEY (unit_id) REFERENCES document_task_units(unit_id)
);
CREATE INDEX IF NOT EXISTS idx_dtum_upload ON document_task_unit_members(upload_id);
CREATE TABLE IF NOT EXISTS document_workspaces (
    user_id            TEXT PRIMARY KEY,
    upload_ids_json    TEXT NOT NULL DEFAULT '[]',
    checked_upload_ids_json TEXT NOT NULL DEFAULT '[]',
    active_unit_id     TEXT,
    active_task_id     TEXT,
    selected_upload_id TEXT,
    updated_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS db_connections (
    connection_id  TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    name           TEXT NOT NULL,
    dialect        TEXT NOT NULL,              -- sqlite | mysql | postgresql
    host           TEXT NOT NULL DEFAULT '',
    port           INTEGER NOT NULL DEFAULT 0,
    database_name  TEXT NOT NULL DEFAULT '',
    username       TEXT NOT NULL DEFAULT '',
    password_enc   TEXT NOT NULL DEFAULT '',   -- Fernet 加密；空字符串=无密码
    sqlite_relpath TEXT NOT NULL DEFAULT '',   -- sqlite 方言使用
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dbc_user ON db_connections(user_id);
CREATE TABLE IF NOT EXISTS semantic_plan_revisions (
    plan_id             TEXT NOT NULL,
    revision            INTEGER NOT NULL,
    task_id             TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    status              TEXT NOT NULL,
    request_json        TEXT NOT NULL,
    plan_json           TEXT,
    summary             TEXT NOT NULL DEFAULT '',
    diagnostics_json    TEXT NOT NULL DEFAULT '[]',
    clarification_json  TEXT,
    provenance_json     TEXT NOT NULL,
    plan_hash           TEXT,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (plan_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_spr_user_plan
ON semantic_plan_revisions(user_id, plan_id, revision DESC);
CREATE TABLE IF NOT EXISTS source_inspection_reports (
    inspection_id       TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    plan_id             TEXT NOT NULL,
    logical_revision    INTEGER NOT NULL,
    artifact_id         TEXT NOT NULL,
    artifact_sha256     TEXT NOT NULL,
    inspector_version   TEXT NOT NULL,
    report_hash         TEXT NOT NULL,
    report_json         TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sir_user_artifact
ON source_inspection_reports(
    user_id, artifact_sha256, inspector_version, created_at DESC
);
CREATE TABLE IF NOT EXISTS semantic_binding_revisions (
    plan_id             TEXT NOT NULL,
    binding_revision    INTEGER NOT NULL,
    logical_revision    INTEGER NOT NULL,
    user_id             TEXT NOT NULL,
    status              TEXT NOT NULL,
    reports_json        TEXT NOT NULL,
    result_json         TEXT NOT NULL,
    bound_plan_json     TEXT,
    bound_plan_hash     TEXT,
    resolutions_json    TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    PRIMARY KEY (plan_id, binding_revision)
);
CREATE INDEX IF NOT EXISTS idx_sbr_user_plan
ON semantic_binding_revisions(user_id, plan_id, binding_revision DESC);
CREATE TABLE IF NOT EXISTS physical_plan_revisions (
    physical_plan_id      TEXT PRIMARY KEY,
    plan_id               TEXT NOT NULL,
    logical_revision      INTEGER NOT NULL,
    binding_revision      INTEGER NOT NULL,
    user_id               TEXT NOT NULL,
    status                TEXT NOT NULL,
    physical_plan_hash    TEXT NOT NULL,
    physical_plan_json    TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ppr_user_plan
ON physical_plan_revisions(user_id, plan_id, created_at DESC);
CREATE TABLE IF NOT EXISTS table_execution_runs (
    run_id                TEXT PRIMARY KEY,
    user_id               TEXT NOT NULL,
    plan_id               TEXT NOT NULL,
    physical_plan_id      TEXT NOT NULL,
    status                TEXT NOT NULL,
    tool_result_json      TEXT NOT NULL,
    verification_json     TEXT NOT NULL,
    artifact_paths_json   TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    FOREIGN KEY (physical_plan_id) REFERENCES physical_plan_revisions(physical_plan_id)
);
CREATE INDEX IF NOT EXISTS idx_ter_user_plan
ON table_execution_runs(user_id, plan_id, created_at DESC);
CREATE TABLE IF NOT EXISTS document_execution_runs (
    run_id                TEXT PRIMARY KEY,
    user_id               TEXT NOT NULL,
    plan_id               TEXT NOT NULL,
    physical_plan_id      TEXT NOT NULL,
    status                TEXT NOT NULL,
    result_json           TEXT NOT NULL,
    tool_result_json      TEXT NOT NULL,
    verification_json     TEXT NOT NULL,
    artifact_paths_json   TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    FOREIGN KEY (physical_plan_id) REFERENCES physical_plan_revisions(physical_plan_id)
);
CREATE INDEX IF NOT EXISTS idx_der_user_plan
ON document_execution_runs(user_id, plan_id, created_at DESC);
CREATE TABLE IF NOT EXISTS semantic_harness_runs (
    run_id                TEXT PRIMARY KEY,
    user_id               TEXT NOT NULL,
    thread_id             TEXT NOT NULL,
    logical_plan_id       TEXT NOT NULL,
    logical_revision      INTEGER NOT NULL,
    logical_plan_hash     TEXT NOT NULL,
    binding_revision      INTEGER NOT NULL,
    binding_hash          TEXT NOT NULL,
    capability_id         TEXT NOT NULL,
    capability_version    TEXT NOT NULL,
    runtime_profile       TEXT NOT NULL,
    policy_json           TEXT NOT NULL,
    status                TEXT NOT NULL,
    current_node          TEXT NOT NULL,
    repair_rounds         INTEGER NOT NULL DEFAULT 0,
    semantic_replans      INTEGER NOT NULL DEFAULT 0,
    transient_retries     INTEGER NOT NULL DEFAULT 0,
    same_failure_count    INTEGER NOT NULL DEFAULT 0,
    last_failure_fingerprint TEXT,
    question_json         TEXT,
    final_verification_json TEXT,
    eligible_for_delivery INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shr_user_created
ON semantic_harness_runs(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS semantic_harness_attempts (
    attempt_id            TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    node                  TEXT NOT NULL,
    attempt_number        INTEGER NOT NULL,
    idempotency_key       TEXT NOT NULL UNIQUE,
    input_hash            TEXT NOT NULL,
    status                TEXT NOT NULL,
    failure_kind          TEXT,
    tool_result_json      TEXT,
    verification_json     TEXT,
    repair_decision_json  TEXT,
    artifact_paths_json   TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES semantic_harness_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_sha_user_run
ON semantic_harness_attempts(user_id, run_id, attempt_number);
CREATE TABLE IF NOT EXISTS semantic_harness_events (
    event_id              TEXT PRIMARY KEY,
    event_key             TEXT NOT NULL UNIQUE,
    run_id                TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    sequence              INTEGER NOT NULL,
    node                  TEXT NOT NULL,
    event_type            TEXT NOT NULL,
    summary               TEXT NOT NULL,
    details_json          TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES semantic_harness_runs(run_id),
    UNIQUE (run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_she_user_run
ON semantic_harness_events(user_id, run_id, sequence);
CREATE TABLE IF NOT EXISTS semantic_delivery_runs (
    delivery_id           TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    status                TEXT NOT NULL,
    manifest_json         TEXT NOT NULL,
    output_dir            TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES semantic_harness_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_sdr_user_run
ON semantic_delivery_runs(user_id, run_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sdr_run_unique
ON semantic_delivery_runs(run_id);
CREATE TABLE IF NOT EXISTS semantic_delivery_outputs (
    output_id             TEXT PRIMARY KEY,
    delivery_id           TEXT NOT NULL,
    run_id                TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    format                TEXT NOT NULL,
    filename              TEXT NOT NULL,
    media_type            TEXT NOT NULL,
    sha256                TEXT NOT NULL,
    size_bytes            INTEGER NOT NULL,
    file_path             TEXT NOT NULL,
    qa_json               TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    FOREIGN KEY (delivery_id) REFERENCES semantic_delivery_runs(delivery_id)
);
CREATE INDEX IF NOT EXISTS idx_sdo_user_run
ON semantic_delivery_outputs(user_id, run_id, created_at DESC);
CREATE TABLE IF NOT EXISTS semantic_workspace_tasks (
    task_id                 TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    title                   TEXT NOT NULL,
    objective_text          TEXT NOT NULL,
    upload_ids_json         TEXT NOT NULL DEFAULT '[]',
    source_refs_json        TEXT NOT NULL DEFAULT '[]',
    output_formats_json     TEXT NOT NULL DEFAULT '[]',
    table_output_contracts_json TEXT NOT NULL DEFAULT '[]',
    provider                TEXT NOT NULL DEFAULT 'local',
    model                   TEXT,
    external_api_confirmed  INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'queued',
    active_revision         INTEGER NOT NULL DEFAULT 1,
    plan_id                 TEXT,
    logical_revision        INTEGER,
    binding_revision        INTEGER,
    run_id                  TEXT,
    summary                 TEXT NOT NULL DEFAULT '',
    error                   TEXT,
    failure_json            TEXT,
    question_json           TEXT,
    cancel_requested        INTEGER NOT NULL DEFAULT 0,
    deleted_at              TEXT,
    purge_after             TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_swt_user_updated
ON semantic_workspace_tasks(user_id, deleted_at, updated_at DESC);
CREATE TABLE IF NOT EXISTS semantic_workspace_revisions (
    task_id                 TEXT NOT NULL,
    revision                INTEGER NOT NULL,
    user_id                 TEXT NOT NULL,
    objective_text          TEXT NOT NULL,
    output_formats_json     TEXT NOT NULL DEFAULT '[]',
    table_output_contracts_json TEXT NOT NULL DEFAULT '[]',
    plan_id                 TEXT,
    logical_revision        INTEGER,
    binding_revision        INTEGER,
    run_id                  TEXT,
    status                  TEXT NOT NULL,
    summary                 TEXT NOT NULL DEFAULT '',
    change_summary          TEXT NOT NULL DEFAULT '',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    PRIMARY KEY (task_id, revision),
    FOREIGN KEY (task_id) REFERENCES semantic_workspace_tasks(task_id)
);
CREATE INDEX IF NOT EXISTS idx_swr_user_task
ON semantic_workspace_revisions(user_id, task_id, revision DESC);
CREATE TABLE IF NOT EXISTS semantic_workspace_events (
    event_id                TEXT PRIMARY KEY,
    task_id                 TEXT NOT NULL,
    user_id                 TEXT NOT NULL,
    sequence                INTEGER NOT NULL,
    stage                   TEXT NOT NULL,
    event_type              TEXT NOT NULL,
    summary                 TEXT NOT NULL,
    details_json            TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES semantic_workspace_tasks(task_id),
    UNIQUE (task_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_swe_user_task
ON semantic_workspace_events(user_id, task_id, sequence);
CREATE TABLE IF NOT EXISTS semantic_workspace_audit_tombstones (
    task_id                 TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    objective_sha256        TEXT NOT NULL,
    source_refs_json        TEXT NOT NULL DEFAULT '[]',
    result_refs_json        TEXT NOT NULL DEFAULT '[]',
    requested_formats_json  TEXT NOT NULL DEFAULT '[]',
    terminal_status         TEXT NOT NULL,
    error_code              TEXT,
    task_created_at         TEXT NOT NULL,
    deleted_at              TEXT,
    purged_at               TEXT NOT NULL,
    purge_reason            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_swat_user_purged
ON semantic_workspace_audit_tombstones(user_id, purged_at DESC);
