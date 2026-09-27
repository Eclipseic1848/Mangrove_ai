"""显式采集恢复的冻结门；无旧账号证明时禁止追认身份。"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import time

from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore

requested_recovery = ContextVar("requested_collection_recovery", default=None)


def progress_location(owner, task_id):
    store = ArtifactStore(str(Path(settings.semantic_execution_root) / "collection-progress"))
    return store, hashlib.sha256(f"{owner}\0{task_id}".encode("utf-8")).hexdigest()


@contextmanager
def recovery_request(binding, attempt_id):
    token = requested_recovery.set({"binding": binding, "attempt_id": attempt_id})
    try:
        yield
    finally:
        requested_recovery.reset(token)


def authentication_refused(group):
    batch = group.get("batches", [])[-1] if group.get("batches") else {}
    return (group.get("stop_reason") == "collection_failed" and batch.get("phase") == "discovery"
            and batch.get("coverage", {}).get("failure", {}).get("reason") in {"login_required", "challenge_required"}
            and batch.get("returned_count") == 0 and batch.get("attempted", 0) > 0
            and not group.get("discovery_inflight"))


def has_pending(saved):
    return any(not group.get("done") or authentication_refused(group) for group in saved["groups"])


def adopt_recovery(saved, binding, digest, proof, source):
    request = requested_recovery.get()
    original = saved.get("binding_fields") or {}
    old_proof = saved.get("account_identity") or {}
    def contract(value):
        return json.dumps({k: v for k, v in value.items() if k != "credential_binding"}, sort_keys=True, ensure_ascii=False)
    if (not request or request["binding"] != saved.get("binding")
            or not original or hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest() != saved.get("binding")
            or contract(original) != contract(binding)):
        raise ValueError("恢复请求与冻结任务或进度不一致")
    if (not proof or not old_proof.get("account_ref") or proof.get("account_ref") != old_proof["account_ref"]
            or proof.get("environment") != old_proof.get("environment")
            or saved.get("credential_source") != source
            or not 0 <= time.time() - proof["checked_at"] <= 600):
        raise ValueError("无法证明同一账号和执行环境，请重新验证；原进度未修改")
    attempts = saved.get("recovery_attempts", [])
    if any(item["attempt_id"] == request["attempt_id"] for item in attempts):
        raise ValueError("该恢复请求已采用，不能重复执行")
    if not has_pending(saved):
        raise ValueError("原任务没有可恢复的未完成步骤")
    # 所有校验及来源完整性检查完成后才采用新摘要；未知步骤仍由原进度机拒绝重发。
    attempts = [*attempts, {"attempt_id": request["attempt_id"], "from_binding": saved["binding"],
                           "to_binding": digest, "identity_binding": proof["binding"], "adopted_at": time.time()}]
    saved.update(binding=digest, binding_fields=binding, recovery_attempts=attempts)
    for group in saved["groups"]:
        batch = group.get("batches", [])[-1] if group.get("batches") else {}
        if authentication_refused(group):
            # 明确认证拒绝且零来源时释放该页预留；未知调用、限流和其他失败绝不退还预算。
            group["candidate_attempted"] -= batch["attempted"]
            group.update(done=False, stop_reason="candidate_limit")


def load_recovery(owner, task_id):
    import re
    from src.conductor.task_spec import TaskSpec
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", task_id):
        raise ValueError("采集任务标识无效")
    store, key = progress_location(owner, task_id)
    saved = store.read_json_if_exists(key, "progress.json")
    fields = (saved or {}).get("binding_fields") or {}
    if not saved or fields.get("owner") != owner or fields.get("task") != task_id:
        raise ValueError("没有可恢复的本人任务记录")
    spec = TaskSpec.model_validate(fields.get("spec"))
    if not (spec.evidence_collection or spec.bank_benefits):
        raise ValueError("该任务不支持证据采集恢复")
    if not (saved.get("account_identity") or {}).get("account_ref"):
        raise ValueError("原任务没有冻结稳定账号身份，不能换凭证恢复")
    if not has_pending(saved):
        raise ValueError("原任务没有可恢复的未完成步骤")
    if (ArtifactStore().task_dir(task_id) / "workspace-import.json").exists():
        raise ValueError("任务已有工作台初稿，请在原初稿中明确补采需求")
    request = saved.get("request") or {}
    if not request.get("session_id"):
        raise ValueError("原任务缺少冻结会话，不能恢复")
    return {"task_id": task_id, "saved": saved, "state": {**request, "task_id": task_id, "task_spec": spec}}
