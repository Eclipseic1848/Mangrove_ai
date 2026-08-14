# -*- coding: utf-8 -*-
"""标准 OCI 签名事务的窄公共接口。"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
from typing import Literal, Protocol
import uuid

from pydantic import BaseModel, ConfigDict, Field

from .tool_lock import load_locked_executable, sha256_file


class OciSigningCancelled(RuntimeError):
    """签名事务在创建新外部资源前或执行期间被取消。"""


def _remove_generated_layout(layout: Path) -> None:
    """删除本事务新建的 OCI Layout，包括 Windows 只读 blob。"""

    def make_writable_and_retry(function, path: str, _error: BaseException) -> None:
        Path(path).chmod(stat.S_IWRITE)
        function(path)

    # 目标必须已由事务确认是本次新建的独立输出；只读位不应让取消/失败留下半成品。
    shutil.rmtree(layout, onexc=make_writable_and_retry)


class CancellableCommandRunner:
    """运行可被协作取消的外部命令，并确保子进程退出。"""

    def run(
        self,
        command: list[str],
        *,
        environment: dict[str, str] | None,
        cancel_requested: Callable[[], bool],
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
        deadline = time.monotonic() + 900

        def terminate_and_reap() -> None:
            if process.poll() is not None:
                process.communicate()
                return
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()

        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                return subprocess.CompletedProcess(
                    command,
                    process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
            except subprocess.TimeoutExpired:
                try:
                    cancelled = cancel_requested()
                except BaseException:
                    # 探针异常也必须先回收外部工具，避免诊断错误留下失控进程或持有中的文件。
                    terminate_and_reap()
                    raise
                if cancelled:
                    terminate_and_reap()
                    raise OciSigningCancelled("OCI 签名事务已取消")
                if time.monotonic() >= deadline:
                    terminate_and_reap()
                    raise RuntimeError("OCI 签名工具执行超时")


class LockedOciSigningToolchain(BaseModel):
    """经来源和内容哈希锁定的签名 PoC 工具链。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cosign_executable: Path
    cosign_version: Literal["3.0.6"] = "3.0.6"
    oras_executable: Path
    oras_version: Literal["1.3.2"] = "1.3.2"
    registry_image: str = Field(
        pattern=r"^ghcr\.io/project-zot/zot-linux-amd64:v2\.1\.20@sha256:[0-9a-f]{64}$"
    )

    @classmethod
    def _load_executable(
        cls,
        *,
        lock: dict[str, object],
        name: str,
        expected_version: str,
        expected_method: str,
        tool_root: Path,
    ) -> Path:
        fingerprint = (
            "2DA461D13B0C27845EDFA77FE462A3894CBAAA47"
            if name == "oras"
            else None
        )
        try:
            return load_locked_executable(
                lock=lock,
                name=name,
                expected_version=expected_version,
                tool_root=tool_root,
                expected_method=expected_method,
                expected_fingerprint=fingerprint,
            )
        except ValueError as error:
            raise RuntimeError(f"签名工具锁校验失败: {error}") from error

    @classmethod
    def load(
        cls,
        *,
        tool_root: str | Path,
        lock_path: str | Path,
    ) -> "LockedOciSigningToolchain":
        root = Path(tool_root).resolve()
        try:
            lock = json.loads(Path(lock_path).resolve().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("签名工具锁不可用") from error
        if not isinstance(lock, dict):
            raise RuntimeError("签名工具锁格式无效")
        cosign = cls._load_executable(
            lock=lock,
            name="cosign",
            expected_version="3.0.6",
            expected_method="github_release_asset_digest",
            tool_root=root,
        )
        oras = cls._load_executable(
            lock=lock,
            name="oras",
            expected_version="1.3.2",
            expected_method="openpgp_signed_checksums_and_archive",
            tool_root=root,
        )
        registry = lock.get("registry")
        if not isinstance(registry, dict):
            raise RuntimeError("签名工具锁缺少临时 Registry")
        verification = registry.get("source_verification")
        if (
            not isinstance(verification, dict)
            or verification.get("verified") is not True
            or verification.get("method")
            != "official_ghcr_digest_and_release_commit"
            or verification.get("release_commit")
            != "3b5796d834e8661ea661a5fcc47add8d4405aebf"
        ):
            raise RuntimeError("临时 Registry 官方来源尚未验证")
        if registry.get("name") != "zot" or registry.get("version") != "2.1.20":
            raise RuntimeError("临时 Registry 版本不符合冻结要求")
        image = str(registry.get("image", ""))
        if not re.fullmatch(
            r"ghcr\.io/project-zot/zot-linux-amd64:v2\.1\.20@sha256:[0-9a-f]{64}",
            image,
        ):
            raise RuntimeError("临时 Registry 镜像未绑定冻结版本和 digest")
        return cls(
            cosign_executable=cosign,
            oras_executable=oras,
            registry_image=image,
        )


class RegistryLease(BaseModel):
    """一次签名事务独占的回环 Registry 租约。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str = Field(pattern=r"^127\.0\.0\.1:[1-9][0-9]{0,4}$")
    resource_id: str = Field(min_length=1, max_length=160)


class OciSigningRequest(BaseModel):
    """严格绑定冻结主体和独立输出 Layout 的签名请求。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,119}$")
    source_layout: Path
    source_reference: str = Field(min_length=1, max_length=240)
    output_layout: Path
    output_reference: str = Field(min_length=1, max_length=240)
    registry_repository: str = Field(
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+$"
    )
    subject_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    private_key_path: Path
    public_key_path: Path


class SigningStepResult(BaseModel):
    """外部签名运行时返回的受控结果。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signature_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    referrer_digests: tuple[str, ...]


class OciSigningEvidence(SigningStepResult):
    """可以持久化或展示的脱敏签名证据。"""

    transaction_id: str
    status: Literal["passed"] = "passed"


class OciSigningRuntime(Protocol):
    """Docker、ORAS 与 Cosign 的系统边界。"""

    def recover(self, transaction_id: str) -> None: ...

    def start_registry(self, transaction_id: str) -> RegistryLease: ...

    def sign(
        self,
        request: OciSigningRequest,
        lease: RegistryLease,
        cancel_requested: Callable[[], bool] = lambda: False,
    ) -> SigningStepResult: ...

    def verify_local(
        self,
        request: OciSigningRequest,
        cancel_requested: Callable[[], bool] = lambda: False,
    ) -> SigningStepResult: ...

    def stop_registry(self, lease: RegistryLease) -> None: ...


class LockedCliOciSigningRuntime:
    """只在回环地址启动按事务标记的临时 Zot Registry。"""

    _TRANSACTION_LABEL = "mangrove.ac07.signing.transaction"
    _COSIGN_SIGNATURE_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"

    def __init__(
        self,
        *,
        toolchain: LockedOciSigningToolchain,
        work_root: str | Path,
        project_root: str | Path,
        protected_key_roots: tuple[str | Path, ...],
        docker_executable: str = "docker",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        tool_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        command_runner: CancellableCommandRunner | None = None,
        password_provider: Callable[[], str] | None = None,
        health_probe: Callable[[str], bool] | None = None,
    ) -> None:
        self._toolchain = toolchain
        self._work_root = Path(work_root).resolve()
        self._project_root = Path(project_root).resolve()
        self._protected_key_roots = tuple(
            dict.fromkeys(
                (self._project_root,)
                + tuple(Path(root).resolve() for root in protected_key_roots)
            )
        )
        self._docker = docker_executable
        self._runner = runner
        self._tool_runner = tool_runner
        self._command_runner = command_runner or CancellableCommandRunner()
        self._password_provider = password_provider or (
            lambda: os.environ.get("COSIGN_PASSWORD", "")
        )
        self._health_probe = health_probe or self._probe_registry
        self._transactions_by_container: dict[str, str] = {}

    def _run_tool(
        self,
        command: list[str],
        *,
        environment: dict[str, str] | None = None,
        cancel_requested: Callable[[], bool] = lambda: False,
        failure_message: str,
    ) -> subprocess.CompletedProcess[str]:
        if cancel_requested():
            raise OciSigningCancelled("OCI 签名事务已取消")
        if self._tool_runner is None:
            completed = self._command_runner.run(
                command,
                environment=environment,
                cancel_requested=cancel_requested,
            )
        else:
            completed = self._tool_runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                check=False,
                env=environment,
            )
        if completed.returncode != 0:
            # 原始工具输出可能含宿主路径或 Registry 细节，不进入上层错误和证据。
            raise RuntimeError(f"{failure_message}: {Path(command[0]).stem}")
        return completed

    @staticmethod
    def _parse_descriptor_digest(payload: str) -> str:
        try:
            digest = json.loads(payload).get("digest")
        except (AttributeError, json.JSONDecodeError) as error:
            raise RuntimeError("ORAS 描述符不是有效 JSON") from error
        if not isinstance(digest, str):
            raise RuntimeError("ORAS 描述符缺少 digest")
        return digest

    @classmethod
    def _parse_referrers(cls, payload: str) -> tuple[tuple[str, ...], str]:
        try:
            referrers = json.loads(payload).get("referrers")
            referrer_digests = tuple(
                str(item["digest"])
                for item in referrers
                if isinstance(item, dict)
            )
            signature_digests = tuple(
                str(item["digest"])
                for item in referrers
                if isinstance(item, dict)
                and item.get("artifactType") == cls._COSIGN_SIGNATURE_TYPE
            )
        except (AttributeError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("ORAS Referrers 证据格式无效") from error
        if len(signature_digests) != 1 or any(
            not digest.startswith("sha256:") or len(digest) != 71
            for digest in referrer_digests
        ):
            raise RuntimeError("OCI 签名 Referrer digest 不可用")
        return referrer_digests, signature_digests[0]

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        completed = self._runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            # Docker 输出可能带宿主路径，只向上暴露稳定错误类别。
            raise RuntimeError(f"OCI 签名运行时命令失败: {Path(command[0]).stem}")
        return completed

    @staticmethod
    def _probe_registry(endpoint: str) -> bool:
        from urllib.error import URLError
        from urllib.request import urlopen

        try:
            with urlopen(f"http://{endpoint}/v2/", timeout=1) as response:
                return response.status == 200
        except (OSError, URLError):
            return False

    def _transaction_root(self, transaction_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,119}", transaction_id):
            raise ValueError("OCI 签名事务标识无效")
        transaction_root = (self._work_root / transaction_id).resolve()
        if (
            transaction_root == self._work_root
            or not transaction_root.is_relative_to(self._work_root)
        ):
            raise ValueError("OCI 签名事务标识越出运行目录")
        return transaction_root

    def _remove_empty_work_root(self) -> None:
        try:
            self._work_root.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            # 并发事务仍在使用根目录时保留，最后完成者会再次尝试清理。
            pass

    def _validate_key_location(self, key_path: Path) -> None:
        # 密钥根由调用方显式声明；同时固定包含项目根，避免 Secret 混入制品、业务数据或任务证据。
        if any(key_path.is_relative_to(root) for root in self._protected_key_roots):
            raise ValueError("OCI 签名密钥不得位于项目或受保护目录")

    def _validate_signing_keys(self, private_key: Path, public_key: Path) -> None:
        if not private_key.is_file() or not public_key.is_file():
            raise ValueError("OCI 签名密钥不可用")
        self._validate_key_location(private_key)
        self._validate_key_location(public_key)
        try:
            header = private_key.read_bytes()[:256]
        except OSError as error:
            raise ValueError("OCI 签名私钥不可读") from error
        if b"-----BEGIN ENCRYPTED SIGSTORE PRIVATE KEY-----" not in header:
            # 只接受 Cosign 加密容器，避免口令保护被调用方用任意明文文件静默绕过。
            raise ValueError("OCI 签名私钥必须使用加密 Sigstore 格式")

    @staticmethod
    def _registry_references(
        request: OciSigningRequest,
        lease: RegistryLease,
        tag: str,
    ) -> tuple[str, str]:
        base = f"{lease.endpoint}/{request.registry_repository}"
        return f"{base}:{tag}", f"{base}@{request.subject_digest}"

    def _verify_registry_signature(
        self,
        *,
        public_key: Path,
        registry_subject: str,
        failure_message: str,
        cancel_requested: Callable[[], bool] = lambda: False,
    ) -> None:
        # 公钥验证只允许回环 HTTP，并显式接受本 PoC 未上传透明日志的事实。
        verified = self._run_tool(
            [
                str(self._toolchain.cosign_executable),
                "verify",
                "--key",
                str(public_key),
                "--insecure-ignore-tlog",
                "--allow-http-registry",
                registry_subject,
            ],
            cancel_requested=cancel_requested,
            failure_message=failure_message,
        )
        if not verified.stdout.strip():
            raise RuntimeError("Cosign 未返回有效签名验证结果")

    def recover(self, transaction_id: str) -> None:
        transaction_root = self._transaction_root(transaction_id)
        label = f"{self._TRANSACTION_LABEL}={transaction_id}"
        listed = self._run(
            [
                self._docker,
                "ps",
                "--all",
                "--filter",
                f"label={label}",
                "--format",
                "{{.ID}}",
            ]
        )
        for container_id in listed.stdout.splitlines():
            if container_id.strip():
                self._run([self._docker, "rm", "--force", container_id.strip()])
        # 事务标识和解析后路径已校验，只删除专用 work_root 下的精确子目录。
        shutil.rmtree(transaction_root, ignore_errors=True)
        self._remove_empty_work_root()

    def start_registry(self, transaction_id: str) -> RegistryLease:
        transaction_root = self._transaction_root(transaction_id)
        storage = transaction_root / "registry-storage"
        storage.mkdir(parents=True, exist_ok=False)
        config = transaction_root / "zot-config.json"
        config.write_text(
            json.dumps(
                {
                    "distSpecVersion": "1.1.1",
                    "storage": {"rootDirectory": "/var/lib/registry"},
                    "http": {"address": "0.0.0.0", "port": "5000"},
                    "log": {"level": "error"},
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        label = f"{self._TRANSACTION_LABEL}={transaction_id}"
        container_name = f"mangrove-ac07-{transaction_id}-{uuid.uuid4().hex[:8]}"
        started = self._run(
            [
                self._docker,
                "run",
                "--detach",
                "--rm",
                "--name",
                container_name,
                "--label",
                label,
                "--publish",
                "127.0.0.1::5000",
                "--volume",
                f"{config}:/etc/zot/config.json:ro",
                "--volume",
                f"{storage}:/var/lib/registry",
                self._toolchain.registry_image,
            ]
        )
        container_id = started.stdout.strip()
        try:
            port = self._run([self._docker, "port", container_id, "5000/tcp"])
            endpoint = port.stdout.strip()
            if not endpoint.startswith("127.0.0.1:"):
                raise RuntimeError("临时 Registry 未严格绑定回环地址")
            for _ in range(50):
                if self._health_probe(endpoint):
                    self._transactions_by_container[container_id] = transaction_id
                    return RegistryLease(endpoint=endpoint, resource_id=container_id)
                time.sleep(0.1)
            raise RuntimeError("临时 Registry 健康检查超时")
        except BaseException:
            try:
                self._run([self._docker, "rm", "--force", container_id])
            finally:
                # transaction_root 来自严格边界校验，不会越出专用运行根目录。
                shutil.rmtree(transaction_root, ignore_errors=True)
                self._remove_empty_work_root()
            raise

    def sign(
        self,
        request: OciSigningRequest,
        lease: RegistryLease,
        cancel_requested: Callable[[], bool] = lambda: False,
    ) -> SigningStepResult:
        source_layout = request.source_layout.resolve()
        output_layout = request.output_layout.resolve()
        private_key = request.private_key_path.resolve()
        public_key = request.public_key_path.resolve()
        if not source_layout.is_dir():
            raise ValueError("OCI 签名源 Layout 不存在")
        if output_layout == source_layout or output_layout.exists():
            raise ValueError("OCI 签名输出必须是新的独立 Layout")
        self._validate_signing_keys(private_key, public_key)

        oras = str(self._toolchain.oras_executable)
        cosign = str(self._toolchain.cosign_executable)
        descriptor = self._run_tool(
            [
                oras,
                "manifest",
                "fetch",
                request.source_reference,
                "--oci-layout-path",
                str(source_layout),
                "--descriptor",
            ],
            cancel_requested=cancel_requested,
            failure_message="OCI 签名工具执行失败",
        )
        source_digest = self._parse_descriptor_digest(descriptor.stdout)
        if source_digest != request.subject_digest:
            raise RuntimeError("OCI 源 Layout digest 与冻结请求不一致")

        registry_tag, registry_subject = self._registry_references(
            request,
            lease,
            request.transaction_id,
        )
        self._run_tool(
            [
                oras,
                "cp",
                "--from-oci-layout-path",
                str(source_layout),
                "--to-plain-http",
                request.source_reference,
                registry_tag,
                "--no-tty",
            ],
            cancel_requested=cancel_requested,
            failure_message="OCI 签名工具执行失败",
        )
        password = self._password_provider()
        if not password:
            raise RuntimeError("Cosign 加密私钥口令不可用")
        environment = os.environ.copy()
        # 口令只进入 Cosign 子进程环境，避免写入 argv、日志或可持久化证据。
        environment["COSIGN_PASSWORD"] = password
        # 该离线配置只允许回环 PoC：不使用 Rekor，HTTP Registry 由 Docker 严格绑定 127.0.0.1。
        self._run_tool(
            [
                cosign,
                "sign",
                "--key",
                str(private_key),
                "--use-signing-config=false",
                "--tlog-upload=false",
                "--yes",
                "--allow-http-registry",
                registry_subject,
            ],
            environment=environment,
            cancel_requested=cancel_requested,
            failure_message="OCI 签名工具执行失败",
        )
        password = ""
        self._verify_registry_signature(
            public_key=public_key,
            registry_subject=registry_subject,
            cancel_requested=cancel_requested,
            failure_message="OCI 签名工具执行失败",
        )
        discovered = self._run_tool(
            [
                oras,
                "discover",
                "--plain-http",
                "--distribution-spec",
                "v1.1-referrers-api",
                "--format",
                "json",
                registry_subject,
            ],
            cancel_requested=cancel_requested,
            failure_message="OCI 签名工具执行失败",
        )
        referrer_digests, signature_digest = self._parse_referrers(discovered.stdout)
        self._run_tool(
            [
                oras,
                "cp",
                "--recursive",
                "--from-plain-http",
                "--to-oci-layout-path",
                str(output_layout),
                registry_subject,
                request.output_reference,
                "--no-tty",
            ],
            cancel_requested=cancel_requested,
            failure_message="OCI 签名工具执行失败",
        )
        return SigningStepResult(
            subject_digest=request.subject_digest,
            signature_digest=signature_digest,
            public_key_sha256=sha256_file(public_key),
            referrer_digests=referrer_digests,
        )

    def verify_local(
        self,
        request: OciSigningRequest,
        cancel_requested: Callable[[], bool] = lambda: False,
    ) -> SigningStepResult:
        output_layout = request.output_layout.resolve()
        public_key = request.public_key_path.resolve()
        if not output_layout.is_dir() or not public_key.is_file():
            raise ValueError("OCI 签名 Layout 或公钥不可用")
        self._validate_key_location(public_key)

        oras = str(self._toolchain.oras_executable)
        descriptor = self._run_tool(
            [
                oras,
                "manifest",
                "fetch",
                request.output_reference,
                "--oci-layout-path",
                str(output_layout),
                "--descriptor",
            ],
            cancel_requested=cancel_requested,
            failure_message="OCI 签名本地重验失败",
        )
        discovered = self._run_tool(
            [
                oras,
                "discover",
                "--oci-layout-path",
                str(output_layout),
                "--format",
                "json",
                request.output_reference,
            ],
            cancel_requested=cancel_requested,
            failure_message="OCI 签名本地重验失败",
        )
        output_digest = self._parse_descriptor_digest(descriptor.stdout)
        referrer_digests, signature_digest = self._parse_referrers(discovered.stdout)
        if output_digest != request.subject_digest:
            raise RuntimeError("OCI 签名本地 Layout 与冻结请求不一致")

        verification_id = "verify-" + hashlib.sha256(
            request.transaction_id.encode("utf-8")
        ).hexdigest()[:20]
        self.recover(verification_id)
        lease = self.start_registry(verification_id)
        try:
            registry_tag, registry_subject = self._registry_references(
                request,
                lease,
                verification_id,
            )
            self._run_tool(
                [
                    oras,
                    "cp",
                    "--recursive",
                    "--from-oci-layout-path",
                    str(output_layout),
                    "--to-plain-http",
                    request.output_reference,
                    registry_tag,
                    "--no-tty",
                ],
                cancel_requested=cancel_requested,
                failure_message="OCI 签名本地重验失败",
            )
            self._verify_registry_signature(
                public_key=public_key,
                registry_subject=registry_subject,
                cancel_requested=cancel_requested,
                failure_message="OCI 签名本地重验失败",
            )
        finally:
            self.stop_registry(lease)
        return SigningStepResult(
            subject_digest=request.subject_digest,
            signature_digest=signature_digest,
            public_key_sha256=sha256_file(public_key),
            referrer_digests=referrer_digests,
        )

    def stop_registry(self, lease: RegistryLease) -> None:
        transaction_id = self._transactions_by_container.pop(lease.resource_id, None)
        try:
            self._run([self._docker, "rm", "--force", lease.resource_id])
        finally:
            if transaction_id is not None:
                transaction_root = self._transaction_root(transaction_id)
                # 容器到事务的映射只保存已校验标识，递归删除限定在专用子目录。
                shutil.rmtree(transaction_root, ignore_errors=True)
                self._remove_empty_work_root()


class OciSigningTransaction:
    """在唯一公共入口内保证签名事务结束前释放临时 Registry。"""

    def __init__(self, runtime: OciSigningRuntime) -> None:
        self._runtime = runtime

    @staticmethod
    def _evidence_path(request: OciSigningRequest) -> Path:
        return request.output_layout.with_name(
            f"{request.output_layout.name}.{request.transaction_id}.signing.json"
        )

    @staticmethod
    def _same_result(
        evidence: OciSigningEvidence,
        verified: SigningStepResult,
    ) -> bool:
        return evidence.model_dump(exclude={"transaction_id", "status"}) == (
            verified.model_dump()
        )

    def execute(
        self,
        request: OciSigningRequest,
        *,
        cancel_requested: Callable[[], bool] = lambda: False,
    ) -> OciSigningEvidence:
        lease: RegistryLease | None = None
        output_existed = request.output_layout.exists()
        try:
            self._runtime.recover(request.transaction_id)
            evidence_path = self._evidence_path(request)
            if evidence_path.is_file():
                try:
                    evidence = OciSigningEvidence.model_validate_json(
                        evidence_path.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    raise RuntimeError("OCI 签名幂等证据不可用") from error
                if (
                    evidence.transaction_id != request.transaction_id
                    or evidence.subject_digest != request.subject_digest
                ):
                    raise RuntimeError("OCI 签名幂等证据与冻结请求不一致")
                verified = self._runtime.verify_local(request, cancel_requested)
                if not self._same_result(evidence, verified):
                    raise RuntimeError("OCI 签名 Layout 重验结果与既有证据不一致")
                return evidence
            if cancel_requested():
                raise OciSigningCancelled("OCI 签名事务已取消")
            lease = self._runtime.start_registry(request.transaction_id)
            result = self._runtime.sign(request, lease, cancel_requested)
            if result.subject_digest != request.subject_digest:
                raise RuntimeError("签名主体 digest 与冻结请求不一致")
            self._runtime.stop_registry(lease)
            lease = None
            verified = self._runtime.verify_local(request, cancel_requested)
            if result != verified:
                raise RuntimeError("OCI 签名 Layout 首次重验结果与签名结果不一致")
            evidence = OciSigningEvidence(
                transaction_id=request.transaction_id,
                **result.model_dump(),
            )
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = evidence_path.with_name(
                f"{evidence_path.name}.tmp-{uuid.uuid4().hex[:12]}"
            )
            try:
                temporary.write_text(
                    evidence.model_dump_json(indent=2),
                    encoding="utf-8",
                )
                temporary.replace(evidence_path)
            finally:
                temporary.unlink(missing_ok=True)
            return evidence
        except BaseException:
            if not output_existed and request.output_layout.exists():
                # 只清理由本事务新建且请求模型已确认的独立输出，绝不覆盖或删除既有 Layout。
                _remove_generated_layout(request.output_layout)
            raise
        finally:
            if lease is not None:
                self._runtime.stop_registry(lease)
