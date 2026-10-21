"""历史未知 Run 的不可变封存；停止证明不把未知结果改成成功。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from src.database_migrations import DatabaseTarget, inspect_database
from src.service_identity_window import ServiceIdentityWindow, validate_window_ref, verify_service_window


class RuntimeRunSealedError(PermissionError):
    """原 Run 已封存，禁止恢复、重发或改写原始事实。"""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _row(conn, sql, args=()):
    cursor = conn.execute(sql, args)
    value = cursor.fetchone()
    return dict(zip((item[0] for item in cursor.description), value)) if value is not None else None


def assert_run_not_sealed(conn, user_id: str, task_id: str, revision: int) -> None:
    from src.deployment_quarantine import assert_task_not_quarantined

    # 首次维护隔离不证明已停止；仍在同一执行门拒绝所有旧任务修订。
    assert_task_not_quarantined(conn, user_id, task_id)
    # 任一封存记录都先拒绝；损坏的证明只能继续阻断，不能恢复旧 Run。
    if conn.execute("SELECT 1 FROM runtime_run_seals WHERE owner_id=? AND task_id=? AND revision=?",
                    (user_id, task_id, revision)).fetchone():
        raise RuntimeRunSealedError("历史 Run 已封存，结果仍未知，禁止恢复或重发")


def _runtime_identity(conn, owner_id, task_id, revision):
    row = _row(conn, """SELECT user_id,task_id,revision,run_id,runtime_version,permission_profile,
        model_connection_id,model_connection_version,model_connection_model,external_api_confirmed,
        status,updated_at,substr(failure_json,1,65537) AS failure_json
        FROM agentic_runtime_runs WHERE user_id=? AND task_id=? AND revision=?""", (owner_id, task_id, revision))
    if row is None or not row["run_id"] or row["status"] not in {"failed", "needs_input"}:
        raise ValueError("不是可裁决的历史未知 Run")
    failure = row.pop("failure_json")
    if not isinstance(failure, str) or len(failure) > 65536 or not isinstance(json.loads(failure), dict) or json.loads(failure).get("error_code") != "MODEL_OUTCOME_UNKNOWN":
        raise ValueError("历史未知事实不完整")
    row["failure_sha256"] = hashlib.sha256(failure.encode("utf-8")).hexdigest()
    binding = _row(conn, """SELECT substr(details_json,1,131073) AS details_json FROM agentic_runtime_events
        WHERE user_id=? AND task_id=? AND revision=? AND event_type='kernel.binding.frozen'
        ORDER BY sequence LIMIT 1""", (owner_id, task_id, revision))
    if binding is None or len(binding["details_json"]) > 131072:
        raise ValueError("缺少原始 RuntimeBinding，不能用当前配置补造证明")
    from src.agentic_runtime.kernel import RuntimeBinding

    frozen = RuntimeBinding.model_validate(json.loads(binding["details_json"])["binding"])
    if (frozen.external_run_id != row["run_id"] or frozen.model_connection_id != row["model_connection_id"]
            or frozen.model_connection_version != row["model_connection_version"]
            or (row["model_connection_model"] and frozen.model != row["model_connection_model"])):
        raise ValueError("原 Run 与冻结 RuntimeBinding 不符")
    row["binding_sha256"] = hashlib.sha256(binding["details_json"].encode("utf-8")).hexdigest()
    row["event_watermark"] = tuple(conn.execute("""SELECT count(*),max(sequence) FROM agentic_runtime_events
        WHERE user_id=? AND task_id=? AND revision=?""", (owner_id, task_id, revision)).fetchone())
    return row


def snapshot_unknown_run(conn, owner_id: str, task_id: str, revision: int) -> dict:
    """生成精确的待审身份；本函数不提供停止证明。"""
    if not owner_id or not task_id or type(revision) is not int or revision < 1:
        raise ValueError("需要精确 Owner、任务及 revision")
    identity = _runtime_identity(conn, owner_id, task_id, revision)
    owner = _row(conn, "SELECT execution_generation,disabled,pending FROM users WHERE user_id=?", (owner_id,))
    binding = _row(conn, """SELECT generation,state,updated_at FROM account_execution_bindings
        WHERE owner_user_id=? AND resource_kind='workspace' AND resource_id=?""", (owner_id, task_id))
    workspace = _row(conn, """SELECT active_revision,cancel_generation,status,run_id FROM semantic_workspace_tasks
        WHERE user_id=? AND task_id=?""", (owner_id, task_id))
    # 旧工作台投影可为空；只使用已逐项吻合的原 Run/冻结绑定，不回填或接受非空冲突。
    if (owner is None or binding is None or workspace is None
            or binding["generation"] != owner["execution_generation"] or binding["state"] not in {"idle", "paused"}
            or workspace["active_revision"] != revision or (workspace["run_id"] is not None and workspace["run_id"] != identity["run_id"])
            or workspace["status"] not in {"failed", "needs_input"}):
        raise ValueError("账号代数或执行绑定仍待确认")
    return {"version": 1, "identity": identity, "owner": owner, "binding": binding, "workspace": workspace}


def _proof_valid(proof, snapshot, approval_reference):
    identity = snapshot["identity"]
    if (not isinstance(proof, dict) or type(proof.get("version")) is not int or proof.get("version") != 1
            or proof.get("owner_id") != identity["user_id"] or proof.get("task_id") != identity["task_id"]
            or type(proof.get("revision")) is not int or proof.get("revision") != identity["revision"] or proof.get("run_id") != identity["run_id"]
            or proof.get("runtime_identity_sha256") != _digest(identity)
            or proof.get("snapshot_sha256") != _digest(snapshot)
            or proof.get("approval_reference") != approval_reference
            or not re.fullmatch(r"[0-9a-f]{32}", str(proof.get("request_id", "")))):
        raise ValueError("停止证明的身份、审批或快照不符")
    observed = datetime.fromisoformat(proof["observed_at"])
    if observed.utcoffset() is None:
        raise ValueError("停止证明时间必须含时区")
    if "window_ref" in proof:
        validate_window_ref(proof["window_ref"])
        if proof["window_ref"]["approval_reference"] != approval_reference:
            raise ValueError("维护窗口与停止证明审批不符")
    checks = proof.get("checks")
    if not isinstance(checks, dict) or set(checks) != {"executor", "resources", "relay"}:
        raise ValueError("必须逐 Run 核对执行器、受管资源和 Relay")
    for kind, check in checks.items():
        if (not isinstance(check, dict) or check.get("state") != "stopped"
                or not re.fullmatch(r"[0-9a-f]{64}", str(check.get("sha256", "")))
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.json", str(check.get("file", "")))):
            raise ValueError("停止证据不完整；未知不能当作已停止")
    return observed


def _request_digest(row):
    return _digest({key: row[key] for key in ("owner_id", "task_id", "revision", "run_id", "runtime_identity_sha256",
        "snapshot_json", "stop_proof_json", "actor_id", "reason", "approval_reference", "idempotency_key")})


def is_valid_run_seal(conn, user_id: str, task_id: str, revision: int) -> bool:
    row = _row(conn, "SELECT * FROM runtime_run_seals WHERE owner_id=? AND task_id=? AND revision=?", (user_id, task_id, revision))
    if row is None:
        return False
    try:
        snapshot = json.loads(row["snapshot_json"])
        proof = json.loads(row["stop_proof_json"])
        observed = _proof_valid(proof, snapshot, row["approval_reference"])
        sealed_at = datetime.fromisoformat(row["sealed_at"])
        return (row["runtime_identity_sha256"] == _digest(snapshot["identity"]) == _digest(_runtime_identity(conn, user_id, task_id, revision))
                and row["run_id"] == snapshot["identity"]["run_id"] and row["request_sha256"] == _request_digest(row)
                and 0 <= (sealed_at - observed).total_seconds() <= 600
                and ("window_ref" not in proof or 0 <= (sealed_at - validate_window_ref(proof["window_ref"])).total_seconds() <= 600)
                and row["reason"] == "historical_unknown_stopped_run_sealed")
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _service_user_uid(user: str) -> int:
    import pwd

    return int(user) if user.isdecimal() else pwd.getpwnam(user).pw_uid


def _require_stopped_service(service: str, runtime_root: Path, *, window: ServiceIdentityWindow | None = None,
                             database: Path | None = None, approval_reference: str = "", target: dict | None = None):
    if sys.platform != "linux" or not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", service):
        raise ValueError("封存写入仅允许 Linux 已停止的维护窗口")
    result = subprocess.run(["systemctl", "show", "--all", "--property=MainPID,LoadState,ActiveState,SubState,UnitFileState,User,WorkingDirectory", "--", service],
                            check=True, capture_output=True, text=True, encoding="utf-8", timeout=10)
    state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    # 文件标记不能代替已加载状态；旧配置仍 loaded 时不能认作有效隔离。
    if (state.get("LoadState") != "masked" or state.get("MainPID") != "0" or state.get("ActiveState") != "inactive" or state.get("SubState") != "dead"
            or state.get("UnitFileState") not in {"masked", "masked-runtime"}):
        raise ValueError("服务须先停止并临时屏蔽自动启动")
    runtime_root = runtime_root.resolve(strict=True)
    if not runtime_root.is_dir() or "User" not in state or "WorkingDirectory" not in state:
        raise ValueError("服务身份缺失或真实源码根不匹配")
    # 只有两个字段明确为空才可使用受保护的原身份；缺字段、半空及非空冲突均不能降级。
    empty_identity = state["User"] == state["WorkingDirectory"] == ""
    if not empty_identity and (not state["User"] or not state["WorkingDirectory"]
            or _service_user_uid(state["User"]) != os.geteuid()
            or Path(state["WorkingDirectory"]).resolve(strict=True) != runtime_root):
        raise ValueError("维护账号、原服务账号或真实源码根不匹配")
    if window is not None:
        return verify_service_window(window, service=service, runtime_root=runtime_root, database=database,
                                     approval_reference=approval_reference, target=target)
    if empty_identity:
        raise ValueError("空身份必须由 root 保护的原身份窗口核验")
    # 防止在 systemd 之外另有同根 Python 实例；只读本账号进程，不读取命令行和环境。
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.geteuid():
                continue
            if (entry / "exe").resolve(strict=True).name.startswith("python") and (entry / "cwd").resolve(strict=True) == runtime_root:
                raise ValueError("仍有同根 Python 进程，不能封存")
        except FileNotFoundError:
            continue


def _proof_document(path: Path):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("停止证据不得重定向")
    if os.name == "posix" and (path.parent.stat().st_uid != os.geteuid() or path.parent.stat().st_mode & 0o077):
        raise ValueError("停止证据必须由当前维护账号私有持有")
    with path.open("rb") as handle:
        raw = handle.read(131073)
    if len(raw) > 131072:
        raise ValueError("停止证明过大")
    return json.loads(raw.decode("utf-8"))


def _read_proof(path: Path, snapshot: dict, approval_reference: str):
    proof = _proof_document(path)
    observed = _proof_valid(proof, snapshot, approval_reference)
    if not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 600:
        raise ValueError("停止证明已过期或来自未来")
    for kind, check in proof["checks"].items():
        source = path.parent / check["file"]
        if source.is_symlink():
            raise ValueError("停止证据不得重定向")
        with source.open("rb") as handle:
            payload = handle.read(131073)
        if len(payload) > 131072 or hashlib.sha256(payload).hexdigest() != check["sha256"]:
            raise ValueError("原始停止证据摘要不符")
        evidence = json.loads(payload.decode("utf-8"))
        expected = {"version": 1, "check": kind, "state": "stopped", "request_id": proof["request_id"],
                    "runtime_identity_sha256": proof["runtime_identity_sha256"], "observed_at": proof["observed_at"]}
        observations = evidence.get("observations")
        if (type(evidence.get("version")) is not int or any(evidence.get(key) != value for key, value in expected.items())
                or not isinstance(observations, list) or not observations
                or any(not isinstance(item, dict) or not item for item in observations)):
            raise ValueError("逐 Run 原始证据不完整；须先人工审阅实际观察")
    return proof


def seal_unknown_run(db_path: Path, *, owner_id: str, task_id: str, revision: int, run_id: str,
                     actor_id: str, approval_reference: str, idempotency_key: str, expected_snapshot_sha256: str,
                     proof_path: Path, service: str, runtime_root: Path, service_window: ServiceIdentityWindow | None = None) -> dict:
    """仅在已确认停机窗口应用逐 Run 人工裁决；不取消任务、不清除原错误。"""
    if not owner_id or not task_id or type(revision) is not int or revision < 1 or not run_id:
        raise ValueError("需要精确 Owner、任务、修订和原 Run")
    if not approval_reference.strip() or len(approval_reference) > 200 or not idempotency_key.strip() or len(idempotency_key) > 200:
        raise ValueError("需要明确审批引用和幂等键")
    inspect_database(DatabaseTarget(profile="webui", path=db_path)).require_current()
    if os.name == "posix" and db_path.stat().st_uid != os.geteuid():
        raise ValueError("封存工具必须由原数据库维护账号执行")
    entry_proof = _proof_document(proof_path) if service_window is not None else None

    def stopped_window():
        if service_window is None:
            return _require_stopped_service(service, runtime_root)
        target = {"owner_id": owner_id, "task_id": task_id, "revision": revision, "run_id": run_id,
                  "runtime_identity_sha256": entry_proof["runtime_identity_sha256"],
                  "snapshot_sha256": expected_snapshot_sha256, "request_id": entry_proof["request_id"]}
        return _require_stopped_service(service, runtime_root, window=service_window, database=db_path,
                                        approval_reference=approval_reference, target=target)

    current_window = stopped_window()
    from types import SimpleNamespace
    from src.api.execution import execution_lock

    with execution_lock(SimpleNamespace(db_path=str(db_path)), owner_id, "workspace", task_id), closing(sqlite3.connect(db_path, timeout=5)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = _row(conn, "SELECT role,disabled,pending FROM users WHERE user_id=?", (actor_id,))
        if actor is None or actor["role"] != "super_admin" or actor["disabled"] or actor["pending"]:
            raise PermissionError("本机封存裁决须记录有效超级管理员")
        existing = _row(conn, "SELECT * FROM runtime_run_seals WHERE (owner_id=? AND task_id=? AND revision=?) OR (actor_id=? AND idempotency_key=?)",
                        (owner_id, task_id, revision, actor_id, idempotency_key))
        if existing is not None:
            # 精确重放仅返回旧审计，不重新证明停止，也不因账号后续变化修改旧记录。
            expected = (owner_id, task_id, revision, run_id, actor_id, approval_reference, idempotency_key)
            actual = tuple(existing[key] for key in ("owner_id", "task_id", "revision", "run_id", "actor_id", "approval_reference", "idempotency_key"))
            if (actual != expected or _digest(json.loads(existing["snapshot_json"])) != expected_snapshot_sha256
                    or _json(entry_proof if entry_proof is not None else _proof_document(proof_path)) != existing["stop_proof_json"]
                    or not is_valid_run_seal(conn, owner_id, task_id, revision)):
                raise ValueError("封存幂等键或证明发生冲突")
            return {"sealed": True, "request_sha256": existing["request_sha256"], "sealed_at": existing["sealed_at"], "replayed": True}
        snapshot = snapshot_unknown_run(conn, owner_id, task_id, revision)
        if snapshot["identity"]["run_id"] != run_id or _digest(snapshot) != expected_snapshot_sha256:
            raise ValueError("待裁决 Run 身份或状态已变化")
        proof = _read_proof(proof_path, snapshot, approval_reference)
        if (entry_proof is not None and proof != entry_proof
                or current_window is not None and proof.get("window_ref") != current_window.window_ref
                or "window_ref" in proof and current_window is None):
            raise ValueError("逐 Run 停止证明与当前受保护窗口不符")
        row = {"owner_id": owner_id, "task_id": task_id, "revision": revision, "run_id": run_id,
               "runtime_identity_sha256": _digest(snapshot["identity"]), "snapshot_json": _json(snapshot),
               "stop_proof_json": _json(proof), "actor_id": actor_id, "reason": "historical_unknown_stopped_run_sealed",
               "approval_reference": approval_reference, "idempotency_key": idempotency_key}
        row["request_sha256"] = _request_digest(row)
        # 在同一写事务结束前再次核验停机；证明文件只绑定人工审批，不替代当前停机事实。
        final_window = stopped_window()
        sealed_at = datetime.now(timezone.utc)
        if not 0 <= (sealed_at - _proof_valid(proof, snapshot, approval_reference)).total_seconds() <= 600:
            raise ValueError("提交时停止证明已过期或来自未来")
        if current_window is not None:
            if final_window is None or final_window.window_ref != current_window.window_ref:
                raise ValueError("提交前维护窗口身份已变化")
            final_window.require_fresh(sealed_at)
        row["sealed_at"] = sealed_at.isoformat()
        conn.execute(f'INSERT INTO runtime_run_seals ({",".join(row)}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
        return {"sealed": True, "request_sha256": row["request_sha256"], "sealed_at": row["sealed_at"], "replayed": False}
