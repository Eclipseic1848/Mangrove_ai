"""首次切换的固定历史隔离；风险接受不证明旧执行自然完成。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

SETTING_KEY = "deployment.first_cutover_quarantine"
ACCEPTED_RISK = "first_cutover_unknown_interruption"
CHAT_SETTING_KEY = "deployment.supplemental_chat_quarantine"
CHAT_ACCEPTED_RISK = "legacy_chat_unknown_interruption"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
EMPTY_MANIFEST = {
    "version": 1, "targets": [], "records": [], "approval_reference": None,
    "actor_id": None, "accepted_risk": None,
}

# 只支持现有明确主键或唯一键；登记清单不能变成任意表读取接口。
RECORD_KEYS = {
    ("webui", "account_execution_bindings"): ("owner_user_id", "resource_kind", "resource_id"),
    ("webui", "account_execution_holds"): ("operation_id",),
    ("webui", "source_read_uses"): ("use_id",),
    ("webui", "source_deletion_operations"): ("operation_id",),
    ("webui", "source_deletions"): ("owner_id", "source_key"),
    ("webui", "semantic_workspace_tasks"): ("task_id",),
    ("webui", "semantic_workspace_revisions"): ("task_id", "revision"),
    ("webui", "semantic_workspace_events"): ("event_id",),
    ("webui", "agentic_runtime_runs"): ("user_id", "task_id", "revision"),
    ("webui", "agentic_runtime_events"): ("event_id",),
    ("webui", "candidate_verification_attempts"): ("attempt_id",),
    ("webui", "candidate_gap_actions"): ("owner_id", "task_id", "idempotency_key"),
    ("webui", "capability_validation_runs"): ("run_id",),
    ("webui", "capability_platform_validation_runs"): ("run_id",),
    ("webui", "capability_validation_leases"): ("digest",),
    ("webui", "capability_platform_validation_leases"): ("digest",),
    ("webui", "source_acquisition_attempts"): ("owner_id", "attempt_id"),
    ("webui", "source_refresh_intents"): ("owner_id", "task_id", "idempotency_key"),
    ("webui", "delivery_publish_intents"): ("publication_key",),
    ("webui", "library_learning_receipts"): ("owner_id", "task_id", "revision", "run_id", "kind"),
    ("webui", "semantic_harness_runs"): ("run_id",),
    ("webui", "data_prep_tasks"): ("task_id",),
    ("webui", "model_connection_grants"): ("grant_id",),
    ("webui", "model_provider_usage"): ("usage_id",),
    ("scheduler", "scheduled_tasks"): ("task_id",),
    ("scheduler", "scheduled_task_runs"): ("run_id",),
}

# 固定白名单只冻结身份与生命周期元数据，不读取正文、Secret 或 Token 哈希。
_METADATA_FIELDS = frozenset("""owner_user_id owner_id user_id task_id resource_kind resource_id generation
    state status updated_at created_at started_at finished_at ended_at run_at next_run_at last_run_at
    operation_id actor_user_id reason reconciliation_complete error_code idempotency_key request_hash
    source_key sha256 snapshot_id deleted_at revision active_revision cancel_generation run_id runtime_version
    model_connection_id model_connection_version model_connection_model permission_profile external_api_confirmed
    event_id sequence event_type attempt_id provider_attempt_id source_revision target_revision worker_id digest
    expires_at publication_key kind grant_id purpose connection_id use_id operation""".split())


def _record_metadata(conn, profile, table, key):
    columns = RECORD_KEYS.get((profile, table))
    if columns is None or set(key) != set(columns):
        raise ValueError("隔离原件身份不在固定白名单")
    available = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    selected = sorted((set(columns) | _METADATA_FIELDS) & available)
    if table == "model_provider_usage":
        # 用量可迟到确认；只绑定原请求身份，不冻结或忽略后续真实计量。
        selected = sorted(set(selected) & {"usage_id", "owner_user_id", "task_id", "revision", "run_id", "grant_id", "purpose", "created_at"})
    if not set(columns) <= set(selected):
        raise ValueError("隔离原件结构不可核对")
    cursor = conn.execute(f'SELECT {",".join(selected)} FROM "{table}" WHERE '
                          + " AND ".join(f'"{column}"=?' for column in columns), tuple(key[column] for column in columns))
    row = cursor.fetchone()
    if row is None or cursor.fetchone() is not None:
        raise ValueError("隔离原件不存在或身份不唯一")
    metadata = dict(zip(selected, row))
    if table == "capability_platform_validation_runs":
        metadata["owner_id"] = conn.execute("SELECT json_extract(payload_json,'$.actor_id') FROM capability_platform_validation_runs WHERE run_id=?", (key["run_id"],)).fetchone()[0]
        if not _text(metadata["owner_id"]):
            raise ValueError("平台验证原件缺少明确 Owner")
    elif table == "scheduled_task_runs":
        parent = _record_metadata(conn, profile, "scheduled_tasks", {"task_id": metadata["task_id"]})
        if not _text(parent.get("owner_user_id")):
            raise ValueError("调度运行缺少原计划 Owner")
        # 子表没有 Owner；将精确原关联的身份纳入摘要，不能默认为无人所有。
        metadata["owner_user_id"] = parent["owner_user_id"]
    elif table in {"capability_validation_leases", "capability_platform_validation_leases"}:
        parent_table = "capability_validation_runs" if table == "capability_validation_leases" else "capability_platform_validation_runs"
        parent = _record_metadata(conn, profile, parent_table, {"run_id": metadata["run_id"]})
        if not _text(parent.get("owner_id")) or parent.get("digest") != metadata["digest"]:
            raise ValueError("验证租约缺少匹配的原 Run 身份")
        metadata["owner_id"] = parent["owner_id"]
    return metadata


def snapshot_record(conn, profile, table, key) -> dict:
    """只读固定身份元数据；摘要不是停止证明。"""
    metadata = _record_metadata(conn, profile, table, key)
    return {"profile": profile, "table": table, "key": dict(key),
            "sha256": hashlib.sha256(_json(metadata).encode("utf-8")).hexdigest()}


def verify_quarantine_records(conn, manifest, *, scheduler_connection=None) -> None:
    """同一读/写快照内核原件；遗漏或变化不能被历史例外掩盖。"""
    manifest = _validate(manifest)
    for record in manifest["records"]:
        source = conn if record["profile"] == "webui" else scheduler_connection
        if source is None or snapshot_record(source, record["profile"], record["table"], record["key"]) != record:
            raise ValueError("隔离原件缺失或已变化")
    _require_target_records(conn, manifest, scheduler_connection)


def _require_target_records(conn, manifest, scheduler_connection):
    originals = []
    for record in manifest["records"]:
        source = conn if record["profile"] == "webui" else scheduler_connection
        originals.append((record["table"], _record_metadata(source, record["profile"], record["table"], record["key"])))
    for target in manifest["targets"]:
        owner, kind, resource = (target[name] for name in ("owner_id", "kind", "resource_id"))
        matches = []
        for table, row in originals:
            if row.get("owner_user_id", row.get("owner_id", row.get("user_id"))) != owner:
                continue
            if kind == "task":
                matched = row.get("task_id") == resource
            elif kind == "source_read":
                matched = table == "source_read_uses" and row.get("use_id") == resource
            elif kind == "deletion":
                matched = table == "source_deletion_operations" and row.get("operation_id") == resource
            else:
                matched = (table == "account_execution_bindings" and row.get("resource_kind") == kind
                           and row.get("resource_id") == resource and row.get("generation") == target["generation"])
            if matched:
                matches.append(row)
        if not matches:
            raise ValueError("隔离目标缺少匹配原件")
    identities = {(item["owner_id"], item["kind"], item["resource_id"]) for item in manifest["targets"]}
    resource_tables = {"capability_validation_runs": "validation", "capability_platform_validation_runs": "validation",
                       "candidate_verification_attempts": "candidate", "source_acquisition_attempts": "source",
                       "semantic_harness_runs": "harness", "scheduled_tasks": "schedule"}
    for table, row in originals:
        owner = row.get("owner_user_id", row.get("owner_id", row.get("user_id")))
        task_covered = row.get("task_id") is not None and (owner, "task", row["task_id"]) in identities
        if table == "account_execution_bindings":
            covered = (owner, row["resource_kind"], row["resource_id"]) in identities or (owner, "task", row["resource_id"]) in identities
        elif table == "source_read_uses":
            covered = task_covered or (owner, "source_read", row["use_id"]) in identities
        elif table in {"source_deletion_operations", "source_deletions"}:
            covered = task_covered or (owner, "deletion", row["operation_id"]) in identities
        elif table == "account_execution_holds":
            covered = all((owner, kind, resource) in identities or (owner, "task", resource) in identities
                          for kind, resource in conn.execute("SELECT resource_kind,resource_id FROM account_execution_bindings WHERE owner_user_id=? AND generation<?", (owner, row["generation"])))
        elif table in resource_tables:
            resource = row.get("attempt_id", row.get("run_id", row.get("task_id")))
            if table == "capability_platform_validation_runs":
                resource = "platform:" + resource
            covered = task_covered or (owner, resource_tables[table], resource) in identities
        elif table in {"capability_validation_leases", "capability_platform_validation_leases"}:
            resource = row.get("run_id")
            if table == "capability_platform_validation_leases" and resource:
                resource = "platform:" + resource
            covered = (owner, "validation", resource) in identities
        elif table == "scheduled_task_runs":
            covered = task_covered or (owner, "schedule", row["task_id"]) in identities
        else:
            covered = task_covered
        if not covered:
            raise ValueError("隔离原件缺少匹配的执行及恢复阻断")


def quarantine_summary(conn) -> dict:
    """明确历史仍未知，不把可执行域 quiet 冒充历史全部自然完成。"""
    manifest = load_quarantine(conn)
    result = {"accepted_risk": manifest["accepted_risk"], "approval_reference": manifest["approval_reference"],
            "targets": len(manifest["targets"]), "records": len(manifest["records"]),
            "manifest_sha256": hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest(),
            "historical_stop_proven": False}
    chat = load_chat_quarantine(conn)
    result["supplemental_chat_quarantine"] = {
        "accepted_risk": chat["accepted_risk"], "approval_reference": chat["approval_reference"],
        "targets": len(chat["targets"]), "records": len(chat["records"]),
        "manifest_sha256": hashlib.sha256(_json(chat).encode("utf-8")).hexdigest(),
        "historical_stop_proven": False,
    }
    return result


class QuarantinedExecutionError(PermissionError):
    """历史身份隔离或隔离权威不可核验，禁止执行和恢复。"""


def _text(value):
    return isinstance(value, str) and value == value.strip() and 0 < len(value) <= 512 and re.search(r"[\x00-\x1f\x7f]", value) is None


def _kinds():
    # 执行原语也会使用本模块，延迟读取既有枚举以避免模块循环初始化。
    from src.account_execution import RESOURCE_KINDS

    return RESOURCE_KINDS | {"task", "source_read", "deletion"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("隔离清单包含重复字段")
        result[key] = value
    return result


def _validate(manifest, *, accepted_risk=ACCEPTED_RISK):
    if (not isinstance(manifest, dict) or set(manifest) != set(EMPTY_MANIFEST)
            or type(manifest["version"]) is not int or manifest["version"] != 1
            or not isinstance(manifest["targets"], list) or not isinstance(manifest["records"], list)
            or len(manifest["targets"]) > 10000 or len(manifest["records"]) > 10000):
        raise ValueError("隔离清单结构无效")
    if not manifest["targets"] and not manifest["records"]:
        if manifest != EMPTY_MANIFEST:
            raise ValueError("空隔离清单不能携带虚构审批")
    elif (not _text(manifest["approval_reference"]) or not _text(manifest["actor_id"])
          or manifest["accepted_risk"] != accepted_risk):
        raise ValueError("隔离清单缺少固定风险接受与审批身份")
    targets = set()
    for target in manifest["targets"]:
        if (not isinstance(target, dict) or set(target) != {"owner_id", "kind", "resource_id", "generation"}
                or not _text(target["owner_id"]) or not _text(target["resource_id"]) or target["kind"] not in _kinds()
                or (target["generation"] is not None and (type(target["generation"]) is not int or target["generation"] < 0))):
            raise ValueError("隔离目标结构无效")
        identity = (target["owner_id"], target["kind"], target["resource_id"])
        if identity in targets:
            raise ValueError("隔离目标身份重复")
        targets.add(identity)
    records = set()
    for record in manifest["records"]:
        if not isinstance(record, dict) or set(record) != {"profile", "table", "key", "sha256"}:
            raise ValueError("隔离原件结构无效")
        columns = RECORD_KEYS.get((record["profile"], record["table"]))
        if (columns is None or not isinstance(record["key"], dict) or set(record["key"]) != set(columns)
                or not isinstance(record["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None):
            raise ValueError("隔离原件表、主键或摘要无效")
        for column, value in record["key"].items():
            numeric = column in {"revision", "sequence"} or (record["profile"] == "scheduler" and record["table"] == "scheduled_task_runs" and column == "run_id")
            if (numeric and (type(value) is not int or not 0 < value < 2**63)) or (not numeric and not _text(value)):
                raise ValueError("隔离原件主键类型无效")
        identity = (record["profile"], record["table"], _json(record["key"]))
        if identity in records:
            raise ValueError("隔离原件身份重复")
        records.add(identity)
    if len(_json(manifest).encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ValueError("隔离清单超出大小限制")
    return manifest


def _raw_manifest(conn, setting_key=SETTING_KEY):
    try:
        row = conn.execute("SELECT length(CAST(value AS BLOB)),substr(CAST(value AS BLOB),1,?) FROM app_settings WHERE key=?",
                           (MAX_MANIFEST_BYTES + 1, setting_key)).fetchone()
    except sqlite3.Error:
        raise QuarantinedExecutionError("隔离权威不可读取，禁止执行") from None
    if row is None or row[0] is None or not 0 < row[0] <= MAX_MANIFEST_BYTES:
        raise QuarantinedExecutionError("隔离权威缺失或无效，禁止执行")
    return row[1]


def load_quarantine(conn) -> dict:
    try:
        return _validate(json.loads(_raw_manifest(conn).decode("utf-8"), object_pairs_hook=_unique_object))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        # 缺失或损坏不能等同空清单；旧身份不能因配置异常重新放行。
        raise QuarantinedExecutionError("隔离权威无效，禁止执行") from None


def _validate_chat_manifest(manifest):
    manifest = _validate(manifest, accepted_risk=CHAT_ACCEPTED_RISK)
    if manifest == EMPTY_MANIFEST:
        return manifest
    if (len(manifest["targets"]) != 2 or len(manifest["records"]) != 2
            or any(target["kind"] != "chat" or type(target["generation"]) is not int
                   for target in manifest["targets"])
            or any(record["profile"] != "webui" or record["table"] != "account_execution_bindings"
                   or record["key"]["resource_kind"] != "chat" for record in manifest["records"])):
        raise ValueError("补充隔离仅接受两条明确代数的旧 Chat 绑定")
    targets = {(target["owner_id"], target["resource_id"]) for target in manifest["targets"]}
    records = {(record["key"]["owner_user_id"], record["key"]["resource_id"]) for record in manifest["records"]}
    if targets != records:
        raise ValueError("补充 Chat 目标与原件身份不一致")
    return manifest


def load_chat_quarantine(conn) -> dict:
    try:
        return _validate_chat_manifest(json.loads(
            _raw_manifest(conn, CHAT_SETTING_KEY).decode("utf-8"), object_pairs_hook=_unique_object))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise QuarantinedExecutionError("补充 Chat 隔离权威无效，禁止执行") from None


def quarantine_targets(conn) -> tuple[dict, ...]:
    # 合并执行目标，不合并审批或改写首次切换的原始固定清单。
    return tuple(load_quarantine(conn)["targets"] + load_chat_quarantine(conn)["targets"])


def verify_chat_quarantine_records(conn, manifest) -> None:
    manifest = _validate_chat_manifest(manifest)
    generations = {(target["owner_id"], target["resource_id"]): target["generation"]
                   for target in manifest["targets"]}
    for record in manifest["records"]:
        row = _record_metadata(conn, "webui", "account_execution_bindings", record["key"])
        if (snapshot_record(conn, "webui", "account_execution_bindings", record["key"]) != record
                or row["state"] != "cleanup_failed"
                or row["generation"] != generations[(row["owner_user_id"], row["resource_id"])]):
            raise ValueError("补充 Chat 原件、代数或清理失败状态已变化")


def register_chat_quarantine(conn, manifest) -> dict:
    """仅在显式维护事务固定两条旧 Chat；原未知和首次隔离原件不改。"""
    if not conn.in_transaction:
        raise ValueError("补充 Chat 隔离登记必须持有显式事务")
    manifest = _validate_chat_manifest(manifest)
    previous = load_chat_quarantine(conn)
    if previous == manifest:
        verify_chat_quarantine_records(conn, manifest)
        return previous
    if previous != EMPTY_MANIFEST or manifest == EMPTY_MANIFEST:
        raise ValueError("补充 Chat 隔离清单已固定，不能替换或清空")
    verify_chat_quarantine_records(conn, manifest)
    original_targets = {(target["owner_id"], target["kind"], target["resource_id"])
                        for target in load_quarantine(conn)["targets"]}
    if any((target["owner_id"], "chat", target["resource_id"]) in original_targets
           for target in manifest["targets"]):
        raise ValueError("补充 Chat 隔离不能重复首次隔离目标")
    actor = conn.execute("SELECT role,disabled,pending FROM users WHERE user_id=?", (manifest["actor_id"],)).fetchone()
    if actor is None or tuple(actor) != ("super_admin", 0, 0):
        raise PermissionError("补充 Chat 隔离须记录有效超级管理员")
    raw = _raw_manifest(conn, CHAT_SETTING_KEY)
    changed = conn.execute("UPDATE app_settings SET value=? WHERE key=? AND CAST(value AS BLOB)=?",
                           (_json(manifest), CHAT_SETTING_KEY, raw)).rowcount
    if changed != 1:
        raise ValueError("补充 Chat 隔离权威已变化，拒绝登记")
    return manifest


def is_quarantined(conn, owner_id, kind, resource_id) -> bool:
    if not _text(owner_id) or not _text(resource_id) or kind not in _kinds():
        raise QuarantinedExecutionError("执行身份不完整，禁止执行")
    return any(target["owner_id"] == owner_id and target["kind"] == kind and target["resource_id"] == resource_id
               for target in quarantine_targets(conn))


def assert_not_quarantined(conn, owner_id, kind, resource_id) -> None:
    # 代数只记录原身份，不能靠换代数或改原状态重新激活同一个旧资源。
    if is_quarantined(conn, owner_id, kind, resource_id):
        raise QuarantinedExecutionError("首次切换隔离的历史身份禁止执行或恢复；原结果仍未知")


def assert_task_not_quarantined(conn, owner_id, task_id) -> None:
    assert_not_quarantined(conn, owner_id, "task", task_id)


def register_quarantine(conn, manifest, *, scheduler_connection=None) -> dict:
    """在调用者显式事务内一次登记；本函数不提交或改写历史业务记录。"""
    if not conn.in_transaction:
        raise ValueError("隔离登记必须由调用者持有显式事务")
    manifest = _validate(manifest)
    previous = load_quarantine(conn)
    if previous == manifest:
        return previous
    if previous != EMPTY_MANIFEST or manifest == EMPTY_MANIFEST:
        raise ValueError("隔离清单已固定，不能替换或清空")
    verify_quarantine_records(conn, manifest, scheduler_connection=scheduler_connection)
    actor = conn.execute("SELECT role,disabled,pending FROM users WHERE user_id=?", (manifest["actor_id"],)).fetchone()
    if actor is None or tuple(actor) != ("super_admin", 0, 0):
        raise PermissionError("首次隔离登记须记录有效超级管理员")
    raw = _raw_manifest(conn)
    changed = conn.execute("UPDATE app_settings SET value=? WHERE key=? AND CAST(value AS BLOB)=?",
                           (_json(manifest), SETTING_KEY, raw)).rowcount
    if changed != 1:
        raise ValueError("隔离权威已变化，拒绝登记")
    return manifest
