DROP TRIGGER candidate_verification_previous_attempt_guard;
DROP TRIGGER candidate_verification_identity_no_update;
DROP TRIGGER candidate_verification_status_transition_guard;
DROP TRIGGER candidate_verification_state_shape_guard;
DROP TRIGGER candidate_verification_terminal_no_update;
DROP TRIGGER candidate_verification_no_delete;
DROP INDEX idx_candidate_verification_owner_candidate;
DROP INDEX uq_candidate_verification_active_candidate;

ALTER TABLE candidate_verification_attempts
RENAME TO candidate_verification_attempts_before_0004;

CREATE TABLE candidate_verification_attempts (
    attempt_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    run_id TEXT NOT NULL,
    previous_attempt_id TEXT,
    reason_code TEXT NOT NULL CHECK (
        reason_code IN (
            'initial', 'semantic_inconclusive', 'ruleset_changed',
            'legacy_rebaseline', 'provider_outcome_recovery'
        )
    ),
    candidate_set_hash TEXT NOT NULL,
    manifest_hash TEXT,
    goal_contract_hash TEXT,
    delivery_spec_hash TEXT,
    ruleset_identity_status TEXT NOT NULL CHECK (
        ruleset_identity_status IN ('versioned', 'legacy_unversioned')
    ),
    verifier_ruleset_hash TEXT,
    verifier_code_commit TEXT,
    verifier_source_hash TEXT,
    verifier_execution_identity_hash TEXT,
    verifier_ruleset_manifest_json TEXT,
    actor_id TEXT NOT NULL,
    connection_id TEXT,
    connection_version TEXT,
    model_id TEXT,
    egress_confirmed_at TEXT,
    provider_attempt_id TEXT,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    rebaseline_authorization_json TEXT,
    rebaseline_authorization_hash TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'requested', 'running', 'passed', 'failed', 'inconclusive',
            'outcome_unknown', 'cancelled'
        )
    ),
    report_json TEXT,
    report_hash TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (owner_id, idempotency_key),
    FOREIGN KEY (previous_attempt_id)
        REFERENCES candidate_verification_attempts(attempt_id),
    CHECK (
        (
            reason_code = 'legacy_rebaseline'
            AND rebaseline_authorization_json IS NOT NULL
            AND rebaseline_authorization_hash IS NOT NULL
        )
        OR
        (
            reason_code <> 'legacy_rebaseline'
            AND rebaseline_authorization_json IS NULL
            AND rebaseline_authorization_hash IS NULL
        )
    )
);

INSERT INTO candidate_verification_attempts (
    attempt_id, owner_id, task_id, revision, run_id, previous_attempt_id,
    reason_code, candidate_set_hash, manifest_hash, goal_contract_hash,
    delivery_spec_hash, ruleset_identity_status, verifier_ruleset_hash,
    verifier_code_commit, verifier_source_hash,
    verifier_execution_identity_hash, verifier_ruleset_manifest_json,
    actor_id, connection_id, connection_version, model_id,
    egress_confirmed_at, provider_attempt_id, idempotency_key, request_hash,
    rebaseline_authorization_json, rebaseline_authorization_hash,
    status, report_json, report_hash, created_at, started_at, finished_at
)
SELECT
    attempt_id, owner_id, task_id, revision, run_id, previous_attempt_id,
    reason_code, candidate_set_hash, manifest_hash, goal_contract_hash,
    delivery_spec_hash, ruleset_identity_status, verifier_ruleset_hash,
    verifier_code_commit, verifier_source_hash,
    verifier_execution_identity_hash, verifier_ruleset_manifest_json,
    actor_id, connection_id, connection_version, model_id,
    egress_confirmed_at, provider_attempt_id, idempotency_key, request_hash,
    NULL, NULL,
    status, report_json, report_hash, created_at, started_at, finished_at
FROM candidate_verification_attempts_before_0004;

DROP TABLE candidate_verification_attempts_before_0004;

CREATE INDEX idx_candidate_verification_owner_candidate
ON candidate_verification_attempts(owner_id, candidate_set_hash, created_at);

CREATE UNIQUE INDEX uq_candidate_verification_active_candidate
ON candidate_verification_attempts(
    owner_id, task_id, revision, run_id, candidate_set_hash
)
WHERE status IN ('requested', 'running');

CREATE TRIGGER candidate_verification_previous_attempt_guard
BEFORE INSERT ON candidate_verification_attempts
WHEN NEW.previous_attempt_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM candidate_verification_attempts AS previous
    WHERE previous.attempt_id = NEW.previous_attempt_id
      AND previous.owner_id = NEW.owner_id
      AND previous.task_id = NEW.task_id
      AND previous.revision = NEW.revision
      AND previous.run_id = NEW.run_id
      AND previous.candidate_set_hash = NEW.candidate_set_hash
      AND previous.status IN (
          'passed', 'failed', 'inconclusive', 'outcome_unknown', 'cancelled'
      )
)
BEGIN
    -- 全局外键不足以隔离 Owner；后继只能连接同一冻结 Candidate 的终态事实。
    SELECT RAISE(ABORT, '候选验证前序 Attempt 身份不一致');
END;

CREATE TRIGGER candidate_verification_identity_no_update
BEFORE UPDATE ON candidate_verification_attempts
WHEN
    NEW.attempt_id IS NOT OLD.attempt_id
    OR NEW.owner_id IS NOT OLD.owner_id
    OR NEW.task_id IS NOT OLD.task_id
    OR NEW.revision IS NOT OLD.revision
    OR NEW.run_id IS NOT OLD.run_id
    OR NEW.previous_attempt_id IS NOT OLD.previous_attempt_id
    OR NEW.reason_code IS NOT OLD.reason_code
    OR NEW.candidate_set_hash IS NOT OLD.candidate_set_hash
    OR NEW.manifest_hash IS NOT OLD.manifest_hash
    OR NEW.goal_contract_hash IS NOT OLD.goal_contract_hash
    OR NEW.delivery_spec_hash IS NOT OLD.delivery_spec_hash
    OR NEW.ruleset_identity_status IS NOT OLD.ruleset_identity_status
    OR NEW.verifier_ruleset_hash IS NOT OLD.verifier_ruleset_hash
    OR NEW.verifier_code_commit IS NOT OLD.verifier_code_commit
    OR NEW.verifier_source_hash IS NOT OLD.verifier_source_hash
    OR NEW.verifier_execution_identity_hash
        IS NOT OLD.verifier_execution_identity_hash
    OR NEW.verifier_ruleset_manifest_json
        IS NOT OLD.verifier_ruleset_manifest_json
    OR NEW.actor_id IS NOT OLD.actor_id
    OR NEW.connection_id IS NOT OLD.connection_id
    OR NEW.connection_version IS NOT OLD.connection_version
    OR NEW.model_id IS NOT OLD.model_id
    OR NEW.egress_confirmed_at IS NOT OLD.egress_confirmed_at
    OR NEW.provider_attempt_id IS NOT OLD.provider_attempt_id
    OR NEW.idempotency_key IS NOT OLD.idempotency_key
    OR NEW.request_hash IS NOT OLD.request_hash
    OR NEW.rebaseline_authorization_json
        IS NOT OLD.rebaseline_authorization_json
    OR NEW.rebaseline_authorization_hash
        IS NOT OLD.rebaseline_authorization_hash
    OR NEW.created_at IS NOT OLD.created_at
BEGIN
    -- 授权证据属于冻结身份，状态推进时不得顺带改写。
    SELECT RAISE(ABORT, '候选验证 Attempt 冻结身份不可改写');
END;

CREATE TRIGGER candidate_verification_status_transition_guard
BEFORE UPDATE ON candidate_verification_attempts
WHEN OLD.status NOT IN (
    'passed', 'failed', 'inconclusive', 'outcome_unknown', 'cancelled'
) AND NOT (
    (OLD.status = 'requested' AND NEW.status = 'running')
    OR
    (OLD.status = 'running' AND NEW.status IN (
        'passed', 'failed', 'inconclusive', 'outcome_unknown', 'cancelled'
    ))
)
BEGIN
    SELECT RAISE(ABORT, '候选验证 Attempt 非法状态转换');
END;

CREATE TRIGGER candidate_verification_state_shape_guard
BEFORE UPDATE ON candidate_verification_attempts
WHEN (
    (OLD.status = 'requested' AND NEW.status = 'running')
    OR
    (OLD.status = 'running' AND NEW.status IN (
        'passed', 'failed', 'inconclusive', 'outcome_unknown', 'cancelled'
    ))
) AND NOT (
    (
        NEW.status = 'running'
        AND NEW.started_at IS NOT NULL
        AND NEW.finished_at IS NULL
        AND NEW.report_json IS NULL
        AND NEW.report_hash IS NULL
    )
    OR
    (
        NEW.status IN ('passed', 'failed', 'inconclusive')
        AND NEW.started_at IS NOT NULL
        AND NEW.finished_at IS NOT NULL
        AND NEW.report_json IS NOT NULL
        AND NEW.report_hash IS NOT NULL
    )
    OR
    (
        NEW.status IN ('outcome_unknown', 'cancelled')
        AND NEW.started_at IS NOT NULL
        AND NEW.finished_at IS NOT NULL
        AND NEW.report_json IS NULL
        AND NEW.report_hash IS NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, '候选验证 Attempt 状态字段不一致');
END;

CREATE TRIGGER candidate_verification_terminal_no_update
BEFORE UPDATE ON candidate_verification_attempts
WHEN OLD.status IN (
    'passed', 'failed', 'inconclusive', 'outcome_unknown', 'cancelled'
)
BEGIN
    SELECT RAISE(ABORT, '候选验证 Attempt 终态不可改写');
END;

CREATE TRIGGER candidate_verification_no_delete
BEFORE DELETE ON candidate_verification_attempts
BEGIN
    SELECT RAISE(ABORT, '候选验证 Attempt 不可删除');
END;
