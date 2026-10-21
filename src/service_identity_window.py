"""调用固定 root 核验器；历史窗口引用只做自包含校验。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


HELPER_PATH = Path("/usr/local/lib/mangrove/runtime_service_identity.py")
# 安装原件须与审核过的 LF 源码完全一致。
HELPER_SHA256 = "218ad78cee03d82f45bd179dc5ff59c152ec0f99aa67e4aed57c08f27a3012b4"


def validate_window_ref(value: dict) -> datetime:
    if (not isinstance(value, dict) or set(value) != {"version", "nonce", "receipt_sha256", "manifest_sha256", "approval_reference", "captured_at"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["approval_reference"], str) or not value["approval_reference"].strip() or len(value["approval_reference"]) > 200
            or not re.fullmatch(r"[0-9a-f]{32}", str(value["nonce"]))
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(value[key])) for key in ("receipt_sha256", "manifest_sha256"))):
        raise ValueError("维护窗口引用不完整")
    captured = datetime.fromisoformat(value["captured_at"])
    if captured.utcoffset() is None:
        raise ValueError("维护窗口时间必须含时区")
    return captured


@dataclass(frozen=True)
class ServiceIdentityWindow:
    manifest: Path
    manifest_sha256: str
    nonce: str


@dataclass(frozen=True)
class VerifiedServiceWindow:
    window_ref: dict
    expires_monotonic_ns: int

    def require_fresh(self, sealed_at: datetime) -> None:
        captured = validate_window_ref(self.window_ref)
        if (not 0 <= (sealed_at - captured).total_seconds() <= 600
                or time.monotonic_ns() > self.expires_monotonic_ns):
            raise ValueError("维护窗口已过期或来自未来")


def _root_owned_path(path: Path, *, allow_symlinks: bool = False) -> None:
    def check(current, depth):
        if depth > 40:
            raise ValueError("系统解释器链接过深")
        for member in (*reversed(current.parents), current):
            info = member.lstat()
            linked = stat.S_ISLNK(info.st_mode)
            if info.st_uid != 0 or (linked and not allow_symlinks) or (not linked and info.st_mode & 0o022):
                raise ValueError("特权工具及解释器必须由 root 保护")
            if linked:
                # 不跳过中间链接；最终目标安全不能证明整条解析链不可写。
                target = member.readlink()
                check(target if target.is_absolute() else member.parent / target, depth + 1)
        resolved = current.resolve(strict=True)
        if resolved != current:
            check(resolved, depth + 1)

    check(path, 0)
    if not path.is_file():
        raise ValueError("特权工具或解释器不是普通文件")


def _trusted_helper() -> None:
    _root_owned_path(HELPER_PATH)
    _root_owned_path(Path("/usr/bin/sudo"))
    _root_owned_path(Path("/usr/bin/python3"), allow_symlinks=True)
    with HELPER_PATH.open("rb") as source:
        raw = source.read(262145)
    if len(raw) > 262144 or hashlib.sha256(raw).hexdigest() != HELPER_SHA256:
        raise ValueError("特权核验器源码摘要不符")


def verify_service_window(window: ServiceIdentityWindow, *, service: str, runtime_root: Path, database: Path,
                          approval_reference: str, target: dict) -> VerifiedServiceWindow:
    if (sys.platform != "linux" or not window.manifest.is_absolute() or ".." in window.manifest.parts
            or not re.fullmatch(r"[0-9a-f]{64}", window.manifest_sha256)
            or not re.fullmatch(r"[0-9a-f]{32}", window.nonce)):
        raise ValueError("需要固定 Linux 维护窗口及批准清单")
    _trusted_helper()
    command = ["/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-B", str(HELPER_PATH), "verify",
               "--manifest", str(window.manifest), "--manifest-sha256", window.manifest_sha256,
               "--approval-reference", approval_reference, "--nonce", window.nonce]
    for key in ("owner_id", "task_id", "revision", "run_id", "runtime_identity_sha256", "snapshot_sha256", "request_id"):
        command.extend(("--" + key.replace("_", "-"), str(target[key])))
    completed = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", timeout=10)
    if len(completed.stdout.encode("utf-8")) > 16384:
        raise ValueError("特权核验响应过大")
    result = json.loads(completed.stdout)
    if not isinstance(result, dict) or result.get("ok") is not True or type(result.get("version")) is not int or result["version"] != 1:
        raise ValueError("特权维护窗口核验失败")
    reference = result["window_ref"]
    validate_window_ref(reference)
    database = database.resolve(strict=True)
    info = database.stat()
    identity = {"canonical_path": str(database), "dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid, "mode": info.st_mode}
    root = runtime_root.resolve(strict=True)
    candidate = Path.cwd().resolve(strict=True)
    verified = datetime.fromisoformat(result["verified_at"])
    if (reference["nonce"] != window.nonce or reference["manifest_sha256"] != window.manifest_sha256
            or reference["approval_reference"] != approval_reference or result.get("service") != service
            or result.get("runtime_root") != str(root) or result.get("candidate_root") != str(candidate)
            or candidate == root or candidate.is_relative_to(root) or not root.is_dir()
            or type(result.get("service_uid")) is not int or result["service_uid"] != os.geteuid() or info.st_uid != os.geteuid()
            or not stat.S_ISREG(info.st_mode) or result.get("database_identity") != identity
            or result.get("current_state") != "stopped_masked" or not isinstance(result.get("boot_id"), str) or not result["boot_id"]
            or verified.utcoffset() is None or not 0 <= (datetime.now(timezone.utc) - verified).total_seconds() <= 10
            or type(result.get("expires_monotonic_ns")) is not int):
        raise ValueError("当前维护窗口与封存目标不符")
    window = VerifiedServiceWindow(reference, result["expires_monotonic_ns"])
    window.require_fresh(datetime.now(timezone.utc))
    return window
