"""私有账号证明只保存摘要；不能代替登录状态或业务操作权限。"""
import hashlib
import json
import math
from pathlib import Path
import re
import time
from contextlib import contextmanager
from filelock import FileLock

from src.config.settings import settings
from src.config.cookie_probe_binding import cookie_probe_binding
from src.data_prep.artifact_store import ArtifactStore

_unavailable = set()


@contextmanager
def identity_verification(key, credential, environment):
    binding = cookie_probe_binding(key, "identity-v1", credential, environment=environment)
    store = identity_store()
    store.root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(store.root / (binding + ".lock")), timeout=0):
        # 探针开始前撤销，取消或写失败均不能留下历史有效证明。
        _unavailable.add(binding)
        store.write_json(binding, "verification-pending.json", {"pending": True})
        (store.task_dir(binding) / "identity.json").unlink(missing_ok=True)
        yield


def identity_store():
    return ArtifactStore(str(Path(settings.webui_db_path).resolve().parent / ".cookie-identities"))


def record_identity(key, credential, environment, authentication):
    binding = cookie_probe_binding(key, "identity-v1", credential, environment=environment)
    valid = (authentication.get("status") == "valid" and authentication.get("account_ref_kind") == "account_id"
             and isinstance(authentication.get("account_ref"), str)
             and re.fullmatch(r"[0-9a-f]{64}", authentication["account_ref"]) is not None)
    # 无效或不足的最新验证覆盖旧证明，不能保留历史绿标来批准恢复。
    record = {"binding": binding, "checked_at": time.time(),
              "account_ref": authentication["account_ref"] if valid else None,
              "environment": hashlib.sha256(json.dumps(environment, sort_keys=True).encode("utf-8")).hexdigest()}
    identity_store().write_json(binding, "identity.json", record)
    (identity_store().task_dir(binding) / "verification-pending.json").unlink(missing_ok=True)
    _unavailable.discard(binding)


def read_identity(key, credential, environment, *, max_age=None):
    binding = cookie_probe_binding(key, "identity-v1", credential, environment=environment)
    if binding in _unavailable:
        return None
    try:
        store = identity_store()
        with FileLock(str(store.root / (binding + ".lock")), timeout=0):
            if (store.task_dir(binding) / "verification-pending.json").exists():
                return None
            path = store.task_dir(binding) / "identity.json"
            if not path.is_file() or path.stat().st_size > 2048:
                return None
            record = store.read_json_if_exists(binding, "identity.json")
            age = time.time() - record["checked_at"]
            if (record.get("binding") != binding or not isinstance(record.get("account_ref"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", record["account_ref"])
                    or not math.isfinite(age) or age < 0 or (max_age is not None and age > max_age)):
                return None
            return record
    except (OSError, ValueError, TypeError, KeyError):
        # 缺失/损坏只使恢复证明不可用，不妨碍原有新任务使用凭证。
        return None
