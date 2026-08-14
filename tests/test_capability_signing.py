# -*- coding: utf-8 -*-
"""AC-07 #9：标准 OCI 签名事务必须返回脱敏证据并清理临时 Registry。"""
from __future__ import annotations

import json
import subprocess
import stat
import sys
import time
from pathlib import Path

import pytest

from src.capability_governance.oci_signing import (
    CancellableCommandRunner,
    LockedOciSigningToolchain,
    LockedCliOciSigningRuntime,
    OciSigningCancelled,
    OciSigningRequest,
    OciSigningTransaction,
    RegistryLease,
    SigningStepResult,
)


def test_cancellable_command_runner_terminates_running_process() -> None:
    started = time.monotonic()

    with pytest.raises(OciSigningCancelled, match="已取消"):
        CancellableCommandRunner().run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            environment=None,
            cancel_requested=lambda: time.monotonic() - started >= 0.2,
        )

    assert time.monotonic() - started < 5


def test_cancellable_command_runner_reaps_process_when_callback_raises(
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    pid_path = tmp_path / "child.pid"

    def broken_callback() -> bool:
        if not pid_path.exists():
            return False
        raise RuntimeError("cancel probe failed")

    with pytest.raises(RuntimeError, match="cancel probe failed"):
        CancellableCommandRunner().run(
            [
                sys.executable,
                "-c",
                (
                    "import os,time,pathlib; "
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()), "
                    "encoding='utf-8'); time.sleep(30)"
                ),
            ],
            environment=None,
            cancel_requested=broken_callback,
        )

    assert time.monotonic() - started < 5
    child_pid = pid_path.read_text(encoding="utf-8")
    listed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {child_pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert child_pid not in listed.stdout


class RecordingRunner:
    """记录 Docker 边界，避免单元测试创建真实外部资源。"""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(tuple(command))
        if command[1] == "ps":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1] == "run":
            return subprocess.CompletedProcess(command, 0, stdout="container-id\n", stderr="")
        if command[1:3] == ["port", "container-id"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="127.0.0.1:49152\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class SigningToolRunner:
    """返回最小合法 ORAS/Cosign 输出并记录口令传递方式。"""

    def __init__(self, subject_digest: str) -> None:
        self.subject_digest = subject_digest
        self.calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs.get("env")
        self.calls.append((tuple(command), environment if isinstance(environment, dict) else None))
        if command[1:3] == ["manifest", "fetch"]:
            output = json.dumps({"digest": self.subject_digest})
        elif command[1] == "discover":
            output = json.dumps(
                {
                    "referrers": [
                        {
                            "digest": "sha256:" + "b" * 64,
                            "artifactType": (
                                "application/vnd.dev.sigstore.bundle.v0.3+json"
                            ),
                        }
                    ]
                }
            )
        elif command[1] == "verify":
            output = "[{\"critical\":{}}]"
        else:
            output = ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


class SuccessfulSigningRuntime:
    """Cosign、ORAS 与 Docker 系统边界夹具。"""

    def __init__(self) -> None:
        self.active_registry = False
        self.start_count = 0
        self.last_result: SigningStepResult | None = None

    def recover(self, transaction_id: str) -> None:
        assert transaction_id.startswith("signing-")

    def start_registry(self, transaction_id: str) -> RegistryLease:
        self.active_registry = True
        self.start_count += 1
        return RegistryLease(
            endpoint="127.0.0.1:49152",
            resource_id="registry-signing-python-v1",
        )

    def sign(
        self,
        request: OciSigningRequest,
        lease: RegistryLease,
        cancel_requested=lambda: False,
    ) -> SigningStepResult:
        request.output_layout.mkdir(parents=True)
        (request.output_layout / "oci-layout").write_text(
            '{"imageLayoutVersion":"1.0.0"}',
            encoding="utf-8",
        )
        self.last_result = SigningStepResult(
            subject_digest=request.subject_digest,
            signature_digest="sha256:" + "b" * 64,
            public_key_sha256="c" * 64,
            referrer_digests=("sha256:" + "b" * 64,),
        )
        return self.last_result

    def verify_local(
        self,
        request: OciSigningRequest,
        cancel_requested=lambda: False,
    ) -> SigningStepResult:
        if cancel_requested():
            raise OciSigningCancelled("OCI 签名事务已取消")
        assert self.last_result is not None
        return self.last_result

    def stop_registry(self, lease: RegistryLease) -> None:
        self.active_registry = False


class FailedSigningRuntime(SuccessfulSigningRuntime):
    def sign(
        self,
        request: OciSigningRequest,
        lease: RegistryLease,
        cancel_requested=lambda: False,
    ) -> SigningStepResult:
        request.output_layout.mkdir(parents=True)
        (request.output_layout / "partial").write_text("incomplete", encoding="utf-8")
        raise RuntimeError("signature mismatch")


class CorruptedCopiedLayoutRuntime(SuccessfulSigningRuntime):
    """模拟签名成功但独立 Layout 回写后损坏。"""

    def verify_local(
        self,
        request: OciSigningRequest,
        cancel_requested=lambda: False,
    ) -> SigningStepResult:
        raise RuntimeError("copied layout corrupted")


class CancellationDuringReopenRuntime(SuccessfulSigningRuntime):
    """只在独立 Layout 重验阶段发出取消。"""

    def __init__(self) -> None:
        super().__init__()
        self.reopen_started = False

    def verify_local(
        self,
        request: OciSigningRequest,
        cancel_requested=lambda: False,
    ) -> SigningStepResult:
        self.reopen_started = True
        readonly_blob = request.output_layout / "readonly-blob"
        readonly_blob.write_text("partial", encoding="utf-8")
        readonly_blob.chmod(stat.S_IREAD)
        return super().verify_local(request, cancel_requested)


def test_signing_transaction_returns_sanitized_evidence_and_cleans_registry(
    tmp_path: Path,
) -> None:
    runtime = SuccessfulSigningRuntime()
    source_layout = tmp_path / "source-layout"
    source_layout.mkdir()
    key_root = tmp_path.parent / "private-signing-key"
    key_root.mkdir()
    request = OciSigningRequest(
        transaction_id="signing-python-v1",
        source_layout=source_layout,
        source_reference="gray-python-table:1.0.0",
        output_layout=tmp_path / "signed-layout",
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest="sha256:" + "a" * 64,
        private_key_path=key_root / "cosign.key",
        public_key_path=key_root / "cosign.pub",
    )

    result = OciSigningTransaction(runtime).execute(request)

    assert result.status == "passed"
    assert result.subject_digest == "sha256:" + "a" * 64
    assert result.signature_digest == "sha256:" + "b" * 64
    assert result.public_key_sha256 == "c" * 64
    assert result.referrer_digests == ("sha256:" + "b" * 64,)
    assert runtime.active_registry is False
    serialized = result.model_dump_json()
    assert str(source_layout) not in serialized
    assert str(key_root) not in serialized


def test_completed_transaction_revalidates_layout_without_signing_again(
    tmp_path: Path,
) -> None:
    runtime = SuccessfulSigningRuntime()
    source_layout = tmp_path / "source-layout"
    source_layout.mkdir()
    key_root = tmp_path.parent / "idempotent-signing-key"
    key_root.mkdir(exist_ok=True)
    request = OciSigningRequest(
        transaction_id="signing-idempotent-v1",
        source_layout=source_layout,
        source_reference="gray-python-table:1.0.0",
        output_layout=tmp_path / "signed-layout",
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest="sha256:" + "a" * 64,
        private_key_path=key_root / "cosign.key",
        public_key_path=key_root / "cosign.pub",
    )
    transaction = OciSigningTransaction(runtime)

    first = transaction.execute(request)
    repeated = transaction.execute(request)

    assert repeated == first
    assert runtime.start_count == 1
    assert runtime.active_registry is False


def test_cancelled_transaction_does_not_start_registry(tmp_path: Path) -> None:
    runtime = SuccessfulSigningRuntime()
    source_layout = tmp_path / "source-layout"
    source_layout.mkdir()
    key_root = tmp_path.parent / "cancelled-signing-key"
    key_root.mkdir(exist_ok=True)
    request = OciSigningRequest(
        transaction_id="signing-cancelled-v1",
        source_layout=source_layout,
        source_reference="gray-python-table:1.0.0",
        output_layout=tmp_path / "signed-layout",
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest="sha256:" + "a" * 64,
        private_key_path=key_root / "cosign.key",
        public_key_path=key_root / "cosign.pub",
    )

    with pytest.raises(OciSigningCancelled, match="已取消"):
        OciSigningTransaction(runtime).execute(
            request,
            cancel_requested=lambda: True,
        )

    assert runtime.start_count == 0
    assert runtime.active_registry is False


def test_cancel_during_first_reopen_removes_layout_and_keeps_no_registry(
    tmp_path: Path,
) -> None:
    runtime = CancellationDuringReopenRuntime()
    source_layout = tmp_path / "source-layout"
    source_layout.mkdir()
    key_root = tmp_path.parent / "reopen-cancel-signing-key"
    key_root.mkdir(exist_ok=True)
    request = OciSigningRequest(
        transaction_id="signing-reopen-cancel-v1",
        source_layout=source_layout,
        source_reference="gray-python-table:1.0.0",
        output_layout=tmp_path / "signed-layout",
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest="sha256:" + "a" * 64,
        private_key_path=key_root / "cosign.key",
        public_key_path=key_root / "cosign.pub",
    )

    with pytest.raises(OciSigningCancelled, match="已取消"):
        OciSigningTransaction(runtime).execute(
            request,
            cancel_requested=lambda: runtime.reopen_started,
        )

    assert request.output_layout.exists() is False
    assert runtime.active_registry is False


def test_signing_toolchain_requires_verified_hashes_and_registry_digest(
    tmp_path: Path,
) -> None:
    tool_root = tmp_path / "tools"
    tool_root.mkdir()
    (tool_root / "cosign.exe").write_bytes(b"cosign-3.0.6")
    (tool_root / "oras.exe").write_bytes(b"oras-1.3.2")
    lock_path = tmp_path / "tools.lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "cosign": {
                    "version": "3.0.6",
                    "executable": "cosign.exe",
                    "executable_sha256": (
                        "279df631de00dd4e976d09967eccb913448910e4210267334998ac58e1414f9d"
                    ),
                    "source_verification": {
                        "method": "github_release_asset_digest",
                        "verified": True,
                    },
                },
                "oras": {
                    "version": "1.3.2",
                    "executable": "oras.exe",
                    "executable_sha256": (
                        "db73ba1759732767e1517d2645272ea37ed84f2f666e9c22d712cb8a8ebce813"
                    ),
                    "source_verification": {
                        "method": "openpgp_signed_checksums_and_archive",
                        "fingerprint": "2DA461D13B0C27845EDFA77FE462A3894CBAAA47",
                        "verified": True,
                    },
                },
                "registry": {
                    "name": "zot",
                    "version": "2.1.20",
                    "image": "ghcr.io/project-zot/zot-linux-amd64:v2.1.20@sha256:"
                    + "d" * 64,
                    "source_verification": {
                        "method": "official_ghcr_digest_and_release_commit",
                        "release_commit": "3b5796d834e8661ea661a5fcc47add8d4405aebf",
                        "verified": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    toolchain = LockedOciSigningToolchain.load(
        tool_root=tool_root,
        lock_path=lock_path,
    )

    assert toolchain.cosign_version == "3.0.6"
    assert toolchain.oras_version == "1.3.2"
    assert toolchain.registry_image.endswith("@sha256:" + "d" * 64)

    tampered = json.loads(lock_path.read_text(encoding="utf-8"))
    tampered["oras"]["executable_sha256"] = "0" * 64
    lock_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(RuntimeError, match="digest"):
        LockedOciSigningToolchain.load(
            tool_root=tool_root,
            lock_path=lock_path,
        )

    tampered["oras"]["executable_sha256"] = (
        "db73ba1759732767e1517d2645272ea37ed84f2f666e9c22d712cb8a8ebce813"
    )
    tampered["registry"]["image"] = (
        "ghcr.io/project-zot/zot-linux-amd64:v9.9.9@sha256:" + "d" * 64
    )
    lock_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Registry.*冻结"):
        LockedOciSigningToolchain.load(tool_root=tool_root, lock_path=lock_path)


def test_failed_signing_removes_new_partial_layout_and_cleans_registry(
    tmp_path: Path,
) -> None:
    runtime = FailedSigningRuntime()
    source_layout = tmp_path / "source-layout"
    source_layout.mkdir()
    key_root = tmp_path.parent / "failed-signing-key"
    key_root.mkdir(exist_ok=True)
    request = OciSigningRequest(
        transaction_id="signing-failed-v1",
        source_layout=source_layout,
        source_reference="gray-python-table:1.0.0",
        output_layout=tmp_path / "signed-layout",
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest="sha256:" + "a" * 64,
        private_key_path=key_root / "cosign.key",
        public_key_path=key_root / "cosign.pub",
    )

    with pytest.raises(RuntimeError, match="signature mismatch"):
        OciSigningTransaction(runtime).execute(request)

    assert request.output_layout.exists() is False
    assert runtime.active_registry is False


def test_first_execution_reopens_layout_before_persisting_passed_evidence(
    tmp_path: Path,
) -> None:
    runtime = CorruptedCopiedLayoutRuntime()
    source_layout = tmp_path / "source-layout"
    source_layout.mkdir()
    key_root = tmp_path.parent / "corrupted-copy-signing-key"
    key_root.mkdir(exist_ok=True)
    request = OciSigningRequest(
        transaction_id="signing-corrupted-copy-v1",
        source_layout=source_layout,
        source_reference="gray-python-table:1.0.0",
        output_layout=tmp_path / "signed-layout",
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest="sha256:" + "a" * 64,
        private_key_path=key_root / "cosign.key",
        public_key_path=key_root / "cosign.pub",
    )

    with pytest.raises(RuntimeError, match="copied layout corrupted"):
        OciSigningTransaction(runtime).execute(request)

    evidence_path = request.output_layout.with_name(
        f"{request.output_layout.name}.{request.transaction_id}.signing.json"
    )
    assert evidence_path.exists() is False
    assert request.output_layout.exists() is False
    assert runtime.active_registry is False


def test_cli_runtime_binds_registry_to_loopback_and_recovers_by_exact_label(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    runtime = LockedCliOciSigningRuntime(
        toolchain=LockedOciSigningToolchain(
            cosign_executable=tmp_path / "cosign.exe",
            oras_executable=tmp_path / "oras.exe",
            registry_image="ghcr.io/project-zot/zot-linux-amd64:v2.1.20@sha256:"
            + "d" * 64,
        ),
        work_root=tmp_path / "runtime",
        project_root=tmp_path,
        protected_key_roots=(tmp_path / "protected",),
        docker_executable="docker",
        runner=runner,
        health_probe=lambda endpoint: endpoint == "127.0.0.1:49152",
    )

    runtime.recover("signing-runtime-v1")
    lease = runtime.start_registry("signing-runtime-v1")
    runtime.stop_registry(lease)

    run_command = next(command for command in runner.commands if command[1] == "run")
    recovery_command = next(command for command in runner.commands if command[1] == "ps")
    assert "127.0.0.1::5000" in run_command
    assert "mangrove.ac07.signing.transaction=signing-runtime-v1" in run_command
    assert "--network" not in run_command
    assert "--all" in recovery_command
    assert lease.endpoint == "127.0.0.1:49152"
    assert any(command[1:3] == ("rm", "--force") for command in runner.commands)
    assert (tmp_path / "runtime").exists() is False


def test_cli_runtime_rejects_recovery_path_escape_before_docker(tmp_path: Path) -> None:
    runner = RecordingRunner()
    sentinel = tmp_path / "outside" / "sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep", encoding="utf-8")
    runtime = LockedCliOciSigningRuntime(
        toolchain=LockedOciSigningToolchain(
            cosign_executable=tmp_path / "cosign.exe",
            oras_executable=tmp_path / "oras.exe",
            registry_image="ghcr.io/project-zot/zot-linux-amd64:v2.1.20@sha256:"
            + "d" * 64,
        ),
        work_root=tmp_path / "runtime",
        project_root=tmp_path,
        protected_key_roots=(tmp_path / "protected",),
        runner=runner,
    )

    with pytest.raises(ValueError, match="事务标识"):
        runtime.recover("../outside")

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert runner.commands == []


def test_cli_runtime_signs_digest_recursively_and_keeps_password_out_of_argv(
    tmp_path: Path,
) -> None:
    subject_digest = "sha256:" + "a" * 64
    tool_runner = SigningToolRunner(subject_digest)
    source_layout = tmp_path / "source"
    source_layout.mkdir()
    output_layout = tmp_path / "output"
    key_root = tmp_path.parent / "external-keys"
    key_root.mkdir(exist_ok=True)
    private_key = key_root / "cosign.key"
    public_key = key_root / "cosign.pub"
    private_key.write_text(
        "-----BEGIN ENCRYPTED SIGSTORE PRIVATE KEY-----\nfixture\n",
        encoding="utf-8",
    )
    public_key.write_text("public", encoding="utf-8")
    runtime = LockedCliOciSigningRuntime(
        toolchain=LockedOciSigningToolchain(
            cosign_executable=tmp_path / "cosign.exe",
            oras_executable=tmp_path / "oras.exe",
            registry_image="ghcr.io/project-zot/zot-linux-amd64:v2.1.20@sha256:"
            + "d" * 64,
        ),
        work_root=tmp_path / "runtime",
        project_root=tmp_path,
        protected_key_roots=(tmp_path / "protected",),
        tool_runner=tool_runner,
        password_provider=lambda: "not-in-command",
    )
    request = OciSigningRequest(
        transaction_id="signing-cli-v1",
        source_layout=source_layout,
        source_reference="gray-python-table:1.0.0",
        output_layout=output_layout,
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest=subject_digest,
        private_key_path=private_key,
        public_key_path=public_key,
    )

    result = runtime.sign(
        request,
        RegistryLease(endpoint="127.0.0.1:49152", resource_id="container-id"),
    )

    commands = [call[0] for call in tool_runner.calls]
    cosign_call, cosign_environment = next(
        call for call in tool_runner.calls if call[0][1] == "sign"
    )
    assert result.subject_digest == subject_digest
    assert result.signature_digest == "sha256:" + "b" * 64
    assert "not-in-command" not in " ".join(cosign_call)
    assert cosign_environment is not None
    assert cosign_environment["COSIGN_PASSWORD"] == "not-in-command"
    assert "--use-signing-config=false" in cosign_call
    assert any("--recursive" in command for command in commands)
    assert any("--to-oci-layout-path" in command for command in commands)


def test_cli_runtime_rejects_signing_keys_inside_project(tmp_path: Path) -> None:
    subject_digest = "sha256:" + "a" * 64
    source_layout = tmp_path / "source"
    source_layout.mkdir()
    key_root = tmp_path / "keys"
    key_root.mkdir()
    private_key = key_root / "cosign.key"
    public_key = key_root / "cosign.pub"
    private_key.write_text("encrypted", encoding="utf-8")
    public_key.write_text("public", encoding="utf-8")
    runtime = LockedCliOciSigningRuntime(
        toolchain=LockedOciSigningToolchain(
            cosign_executable=tmp_path / "cosign.exe",
            oras_executable=tmp_path / "oras.exe",
            registry_image="ghcr.io/project-zot/zot-linux-amd64:v2.1.20@sha256:"
            + "d" * 64,
        ),
        work_root=tmp_path / "runtime",
        project_root=tmp_path,
        protected_key_roots=(tmp_path / "protected",),
        tool_runner=SigningToolRunner(subject_digest),
        password_provider=lambda: "not-in-command",
    )
    request = OciSigningRequest(
        transaction_id="signing-key-boundary-v1",
        source_layout=source_layout,
        source_reference="gray-python-table:1.0.0",
        output_layout=tmp_path / "output",
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest=subject_digest,
        private_key_path=private_key,
        public_key_path=public_key,
    )

    with pytest.raises(ValueError, match="项目或受保护目录"):
        runtime.sign(
            request,
            RegistryLease(endpoint="127.0.0.1:49152", resource_id="container-id"),
        )


def test_cli_runtime_rejects_plaintext_or_protected_external_private_key(
    tmp_path: Path,
) -> None:
    subject_digest = "sha256:" + "a" * 64
    source_layout = tmp_path / "source"
    source_layout.mkdir()
    protected_root = tmp_path.parent / "external-task-data"
    protected_root.mkdir(exist_ok=True)
    private_key = protected_root / "cosign.key"
    public_key = protected_root / "cosign.pub"
    private_key.write_text("plaintext", encoding="utf-8")
    public_key.write_text("public", encoding="utf-8")
    runtime = LockedCliOciSigningRuntime(
        toolchain=LockedOciSigningToolchain(
            cosign_executable=tmp_path / "cosign.exe",
            oras_executable=tmp_path / "oras.exe",
            registry_image="ghcr.io/project-zot/zot-linux-amd64:v2.1.20@sha256:"
            + "d" * 64,
        ),
        work_root=tmp_path / "runtime",
        project_root=tmp_path,
        protected_key_roots=(protected_root,),
        tool_runner=SigningToolRunner(subject_digest),
        password_provider=lambda: "not-in-command",
    )
    request = OciSigningRequest(
        transaction_id="signing-protected-key-v1",
        source_layout=source_layout,
        source_reference="gray-python-table:1.0.0",
        output_layout=tmp_path / "output",
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest=subject_digest,
        private_key_path=private_key,
        public_key_path=public_key,
    )

    with pytest.raises(ValueError, match="受保护目录"):
        runtime.sign(
            request,
            RegistryLease(endpoint="127.0.0.1:49152", resource_id="container-id"),
        )

    external_key_root = tmp_path.parent / "external-plaintext-key"
    external_key_root.mkdir(exist_ok=True)
    external_private_key = external_key_root / "cosign.key"
    external_public_key = external_key_root / "cosign.pub"
    external_private_key.write_text("plaintext", encoding="utf-8")
    external_public_key.write_text("public", encoding="utf-8")
    with pytest.raises(ValueError, match="加密 Sigstore"):
        runtime.sign(
            request.model_copy(
                update={
                    "private_key_path": external_private_key,
                    "public_key_path": external_public_key,
                }
            ),
            RegistryLease(endpoint="127.0.0.1:49152", resource_id="container-id"),
        )


def test_cli_runtime_reopens_output_layout_and_cryptographically_verifies_it(
    tmp_path: Path,
) -> None:
    subject_digest = "sha256:" + "a" * 64
    docker_runner = RecordingRunner()
    tool_runner = SigningToolRunner(subject_digest)
    output_layout = tmp_path / "output"
    output_layout.mkdir()
    public_key = tmp_path.parent / "verify-keys" / "cosign.pub"
    public_key.parent.mkdir(exist_ok=True)
    public_key.write_text("public", encoding="utf-8")
    request = OciSigningRequest(
        transaction_id="signing-reopen-v1",
        source_layout=tmp_path / "unused-source",
        source_reference="gray-python-table:1.0.0",
        output_layout=output_layout,
        output_reference="gray-python-table:1.0.0",
        registry_repository="mangrove/ac07-python-table",
        subject_digest=subject_digest,
        private_key_path=public_key.parent / "unused.key",
        public_key_path=public_key,
    )
    runtime = LockedCliOciSigningRuntime(
        toolchain=LockedOciSigningToolchain(
            cosign_executable=tmp_path / "cosign.exe",
            oras_executable=tmp_path / "oras.exe",
            registry_image="ghcr.io/project-zot/zot-linux-amd64:v2.1.20@sha256:"
            + "d" * 64,
        ),
        work_root=tmp_path / "runtime",
        project_root=tmp_path,
        protected_key_roots=(tmp_path / "protected",),
        runner=docker_runner,
        tool_runner=tool_runner,
        health_probe=lambda endpoint: endpoint == "127.0.0.1:49152",
    )

    result = runtime.verify_local(request)

    commands = [call[0] for call in tool_runner.calls]
    assert result.subject_digest == subject_digest
    assert any(
        command[1] == "cp"
        and "--from-oci-layout-path" in command
        and "--recursive" in command
        for command in commands
    )
    assert any(command[1] == "verify" for command in commands)
    assert any(command[1:3] == ("rm", "--force") for command in docker_runner.commands)
