# -*- coding: utf-8 -*-
"""对两个冻结能力包执行 AC-07 本地 OCI 标准签名验收。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.capability_governance.oci_signing import (
    LockedCliOciSigningRuntime,
    LockedOciSigningToolchain,
    OciSigningCancelled,
    OciSigningRequest,
    OciSigningTransaction,
)


SUBJECTS = (
    (
        "gray-python-table:1.0.0",
        "mangrove/ac07-python-table",
        "sha256:2a430aa8e714d318cdc1ba6ddc6363b1ae0e49212c2a207970153dda03acd902",
    ),
    (
        "gray-everything-mcp:2026.7.4",
        "mangrove/ac07-everything-mcp",
        "sha256:dce5be51c949cfe03b10ace23efe92ce192329d58ee9bf45ef2c0a89ed4cd8a8",
    ),
)


def _run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"验收外部命令失败: {Path(command[0]).stem}")


def _generate_key_pair(
    cosign: Path,
    key_root: Path,
    password: str,
    name: str,
) -> tuple[Path, Path]:
    prefix = key_root / name
    environment = os.environ.copy()
    environment["COSIGN_PASSWORD"] = password
    _run(
        [str(cosign), "generate-key-pair", "--output-key-prefix", str(prefix)],
        environment=environment,
    )
    private_key = prefix.with_suffix(".key")
    public_key = prefix.with_suffix(".pub")
    if b"BEGIN ENCRYPTED SIGSTORE PRIVATE KEY" not in private_key.read_bytes():
        raise RuntimeError("Cosign 私钥未使用加密格式")
    return private_key, public_key


def _load_toolchain(project_root: Path) -> LockedOciSigningToolchain:
    return LockedOciSigningToolchain.load(
        tool_root=project_root / "data/platform-tools/supply-chain",
        lock_path=project_root / "config/supply-chain-tools.lock.json",
    )


def _crash_worker(transaction_id: str) -> None:
    project_root = PROJECT_ROOT
    runtime = LockedCliOciSigningRuntime(
        toolchain=_load_toolchain(project_root),
        work_root=project_root / "data/capability-governance/signing-runtime",
        project_root=project_root,
        protected_key_roots=(project_root / "data",),
    )
    runtime.recover(transaction_id)
    runtime.start_registry(transaction_id)
    # 直接终止进程，确保恢复路径不依赖 finally 或进程内租约。
    os._exit(86)


def main() -> int:
    project_root = PROJECT_ROOT
    toolchain = _load_toolchain(project_root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    external_root = (
        Path(os.environ["LOCALAPPDATA"]) / "Mangrove/ac07-signing-poc" / run_id
    ).resolve()
    if external_root.is_relative_to(project_root):
        raise RuntimeError("签名私钥目录不得位于项目内")
    key_root = external_root / "keys"
    key_root.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(48)
    private_key, public_key = _generate_key_pair(
        toolchain.cosign_executable,
        key_root,
        password,
        "ac07-poc",
    )
    _, wrong_public_key = _generate_key_pair(
        toolchain.cosign_executable,
        key_root,
        password,
        "wrong-identity",
    )

    output_root = (
        project_root / "data/capability-governance/evidence/ac07-signing" / run_id
    )
    output_root.mkdir(parents=True, exist_ok=False)
    runtime_root = project_root / "data/capability-governance/signing-runtime"
    runtime = LockedCliOciSigningRuntime(
        toolchain=toolchain,
        work_root=runtime_root,
        project_root=project_root,
        protected_key_roots=(project_root / "data",),
        password_provider=lambda: password,
    )
    results: list[dict[str, object]] = []
    for reference, repository, digest in SUBJECTS:
        name = reference.split(":", 1)[0]
        request = OciSigningRequest(
            transaction_id=f"signing-{name}-{run_id.casefold()}",
            source_layout=project_root / "data/capabilities/oci",
            source_reference=reference,
            output_layout=output_root / name,
            output_reference=reference,
            registry_repository=repository,
            subject_digest=digest,
            private_key_path=private_key,
            public_key_path=public_key,
        )
        transaction = OciSigningTransaction(runtime)
        evidence = transaction.execute(request)
        repeated = transaction.execute(request)
        if repeated != evidence:
            raise RuntimeError("真实签名事务幂等重验不一致")

        mismatch = request.model_copy(update={"public_key_path": wrong_public_key})
        try:
            runtime.verify_local(mismatch)
        except RuntimeError:
            signature_mismatch = "rejected"
        else:
            raise RuntimeError("错误公钥未被签名验证拒绝")

        tampered_layout = output_root / f"{name}-tampered"
        shutil.copytree(request.output_layout, tampered_layout)
        subject_manifest = (
            tampered_layout
            / "blobs/sha256"
            / request.subject_digest.removeprefix("sha256:")
        )
        subject_manifest.chmod(subject_manifest.stat().st_mode | stat.S_IWRITE)
        subject_manifest.write_bytes(subject_manifest.read_bytes() + b"tampered")
        try:
            runtime.verify_local(
                request.model_copy(update={"output_layout": tampered_layout})
            )
        except RuntimeError:
            subject_tamper = "rejected"
        else:
            raise RuntimeError("主体被篡改的 OCI Layout 未被拒绝")
        finally:
            shutil.rmtree(tampered_layout, ignore_errors=True)

        results.append(
            {
                "subject_digest": evidence.subject_digest,
                "signature_digest": evidence.signature_digest,
                "public_key_sha256": evidence.public_key_sha256,
                "referrer_count": len(evidence.referrer_digests),
                "idempotence": "passed",
                "signature_mismatch": signature_mismatch,
                "subject_tamper": subject_tamper,
            }
        )

    cancelled = OciSigningRequest(
        transaction_id=f"signing-cancelled-{run_id.casefold()}",
        source_layout=project_root / "data/capabilities/oci",
        source_reference=SUBJECTS[0][0],
        output_layout=output_root / "cancelled",
        output_reference=SUBJECTS[0][0],
        registry_repository=SUBJECTS[0][1],
        subject_digest=SUBJECTS[0][2],
        private_key_path=private_key,
        public_key_path=public_key,
    )
    try:
        OciSigningTransaction(runtime).execute(cancelled, cancel_requested=lambda: True)
    except OciSigningCancelled:
        cancellation = "passed"
    else:
        raise RuntimeError("取消请求未失败关闭")

    running_cancel = cancelled.model_copy(
        update={
            "transaction_id": f"signing-running-cancel-{run_id.casefold()}",
            "output_layout": output_root / "running-cancelled",
        }
    )
    verification_seen_at: float | None = None
    last_registry_probe = 0.0

    def cancel_during_reopen() -> bool:
        nonlocal verification_seen_at, last_registry_probe
        now = time.monotonic()
        if verification_seen_at is not None:
            return now - verification_seen_at >= 0.2
        if now - last_registry_probe < 0.25:
            return False
        last_registry_probe = now
        active = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                "label=mangrove.ac07.signing.transaction",
                "--format",
                '{{.Label "mangrove.ac07.signing.transaction"}}',
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        if active.returncode != 0:
            raise RuntimeError("取消探针无法读取签名 Registry 标签")
        if any(line.startswith("verify-") for line in active.stdout.splitlines()):
            verification_seen_at = now
        return False

    running_cancel_started = time.monotonic()
    try:
        OciSigningTransaction(runtime).execute(
            running_cancel,
            cancel_requested=cancel_during_reopen,
        )
    except OciSigningCancelled:
        running_cancellation = "passed"
    else:
        raise RuntimeError("完整签名事务在独立 Layout 重验阶段未响应取消")
    if verification_seen_at is None:
        raise RuntimeError("取消探针未进入独立 Layout 重验阶段")
    if running_cancel.output_layout.exists():
        raise RuntimeError("取消后的独立 Layout 未清理")
    if time.monotonic() - running_cancel_started >= 60:
        raise RuntimeError("完整签名事务取消超时")

    crash_id = f"signing-crash-{run_id.casefold()}"
    runtime.recover(crash_id)
    recovery_runtime = LockedCliOciSigningRuntime(
        toolchain=toolchain,
        work_root=runtime_root,
        project_root=project_root,
        protected_key_roots=(project_root / "data",),
        password_provider=lambda: password,
    )
    try:
        crashed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--crash-worker", crash_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if crashed.returncode != 86:
            raise RuntimeError("崩溃探针未按预期终止")
        crashed_resources = subprocess.run(
            [
                "docker",
                "ps",
                "--all",
                "--filter",
                f"label=mangrove.ac07.signing.transaction={crash_id}",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if crashed_resources.returncode != 0 or not crashed_resources.stdout.strip():
            raise RuntimeError("崩溃探针没有留下可恢复的 Registry")
    finally:
        # 全新 Runtime 只依赖持久标签恢复，不使用已退出进程的内存状态。
        recovery_runtime.recover(crash_id)
    listed = subprocess.run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            "label=mangrove.ac07.signing.transaction",
            "--format",
            "{{.ID}}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if listed.returncode != 0 or listed.stdout.strip() or runtime_root.exists():
        raise RuntimeError("签名验收结束后仍有 Registry 或临时存储残留")
    print(
        json.dumps(
            {
                "status": "passed",
                "subjects": results,
                "prestart_cancellation": cancellation,
                "running_cancellation": running_cancellation,
                "crash_recovery": "passed",
                "residue": "none",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    password = ""
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--crash-worker":
        _crash_worker(sys.argv[2])
    raise SystemExit(main())
