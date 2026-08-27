CREATE TABLE IF NOT EXISTS scheduled_tasks (
    task_id          TEXT PRIMARY KEY,
    user_input       TEXT NOT NULL,
    owner_user_id    TEXT,
    provider         TEXT,
    model            TEXT,
    trigger_type     TEXT NOT NULL,
    run_at           TEXT,
    cron_expr        TEXT,
    next_run_at      TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    last_run_at      TEXT,
    last_success     INTEGER,
    last_result      TEXT,
    last_error       TEXT,
    run_count        INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    name             TEXT,
    source           TEXT NOT NULL DEFAULT 'auto',
    interval_seconds INTEGER,
    start_date       TEXT,
    end_date         TEXT
);

CREATE TABLE IF NOT EXISTS scheduled_task_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    run_at      TEXT NOT NULL,
    success     INTEGER NOT NULL,
    summary     TEXT,
    report_path TEXT,
    json_path   TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_task
ON scheduled_task_runs(task_id, run_id);
