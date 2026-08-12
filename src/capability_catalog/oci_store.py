# -*- coding: utf-8 -*-
"""通过 ORAS 管理本机 OCI Image Layout，避免自建制品协议。"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import threading

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field


class OciArtifactDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str = Field(min_length=1, max_length=1000)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=200)
    artifact_type: str = Field(min_length=1, max_length=200)


class OrasOciLayoutStore:
    """把 ORAS CLI 隐藏在一个只返回冻结 digest 的窄接口后。"""

    _locks_guard = threading.Lock()
    _layout_locks: dict[str, threading.Lock] = {}

    def __init__(
        self,
        layout_path: str | Path,
        *,
        oras_executable: str | None = None,
        layout_id: str = "local",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        executable = oras_executable or shutil.which("oras")
        if not executable:
            raise RuntimeError("未找到 ORAS，请先安装并确保 oras 在 PATH 中")
        self._layout_path = Path(layout_path).resolve()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", layout_id):
            raise ValueError("OCI Layout 逻辑标识格式无效")
        self._layout_id = layout_id
        self._oras_executable = executable
        self._runner = runner
        self._file_lock = FileLock(f"{self._layout_path}.lock", timeout=30)
        self._resolved: dict[
            str,
            tuple[str, OciArtifactDescriptor],
        ] = {}

    def _lock(self) -> threading.Lock:
        key = str(self._layout_path).casefold()
        with self._locks_guard:
            return self._layout_locks.setdefault(key, threading.Lock())

    def _descriptor(
        self,
        payload: dict[str, object],
        *,
        artifact_name: str,
        artifact_type: str,
    ) -> OciArtifactDescriptor:
        digest = payload.get("digest")
        if not isinstance(digest, str):
            raise RuntimeError("ORAS 未返回制品 digest")
        returned_type = str(payload.get("artifactType", artifact_type))
        if returned_type != artifact_type:
            raise ValueError("现有 OCI 制品类型与请求不一致")
        return OciArtifactDescriptor(
            reference=f"oci-layout://{self._layout_id}/{artifact_name}@{digest}",
            digest=digest,
            media_type=str(payload.get("mediaType", "")),
            artifact_type=returned_type,
        )

    def _existing_descriptor(
        self,
        *,
        target: str,
        source_digest: str,
        artifact_name: str,
        artifact_type: str,
        cwd: str,
    ) -> OciArtifactDescriptor | None:
        if not (self._layout_path / "oci-layout").is_file():
            return None
        command = (
            self._oras_executable,
            "manifest",
            "fetch",
            target,
            "--oci-layout-path",
            str(self._layout_path),
        )
        completed = self._runner(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            message = completed.stderr.casefold()
            if any(marker in message for marker in ("not found", "missing")):
                return None
            raise RuntimeError(f"ORAS 读取现有 manifest 失败：{completed.stderr.strip()}")
        manifest = json.loads(completed.stdout)
        layer_digests = {
            layer.get("digest")
            for layer in manifest.get("layers", ())
            if isinstance(layer, dict)
        }
        if layer_digests != {source_digest}:
            raise ValueError("同一制品版本已存在且内容不同，不得覆盖")
        descriptor = self._runner(
            (*command, "--descriptor"),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return self._descriptor(
            json.loads(descriptor.stdout),
            artifact_name=artifact_name,
            artifact_type=artifact_type,
        )

    def push_file(
        self,
        source_path: str | Path,
        *,
        artifact_name: str,
        version: str,
        artifact_type: str,
        layer_media_type: str = "application/octet-stream",
    ) -> OciArtifactDescriptor:
        source = Path(source_path).resolve(strict=True)
        with source.open("rb") as stream:
            source_digest = "sha256:" + hashlib.file_digest(
                stream,
                "sha256",
            ).hexdigest()
        target = f"{artifact_name}:{version}"
        with self._lock(), self._file_lock:
            cached = self._resolved.get(target)
            if cached is not None:
                cached_source_digest, descriptor = cached
                if cached_source_digest != source_digest:
                    raise ValueError("同一制品版本已存在且内容不同，不得覆盖")
                return descriptor
            self._layout_path.mkdir(parents=True, exist_ok=True)
            existing = self._existing_descriptor(
                target=target,
                source_digest=source_digest,
                artifact_name=artifact_name,
                artifact_type=artifact_type,
                cwd=str(source.parent),
            )
            if existing is not None:
                self._resolved[target] = (source_digest, existing)
                return existing
            command: Sequence[str] = (
                self._oras_executable,
                "push",
                target,
                f"{source.name}:{layer_media_type}",
                "--oci-layout-path",
                str(self._layout_path),
                "--artifact-type",
                artifact_type,
                "--format",
                "json",
                "--no-tty",
            )
            completed = self._runner(
                command,
                cwd=str(source.parent),
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            descriptor = self._descriptor(
                json.loads(completed.stdout),
                artifact_name=artifact_name,
                artifact_type=artifact_type,
            )
            self._resolved[target] = (source_digest, descriptor)
            return descriptor

    def lookup_file(
        self,
        *,
        artifact_name: str,
        version: str,
        source_digest: str,
        artifact_type: str,
    ) -> OciArtifactDescriptor | None:
        """按冻结源 digest 查询现有版本，不依赖可移动 tag 作为信任证据。"""

        target = f"{artifact_name}:{version}"
        with self._lock(), self._file_lock:
            cached = self._resolved.get(target)
            if cached is not None:
                cached_source_digest, descriptor = cached
                if cached_source_digest != source_digest:
                    raise ValueError("同一制品版本已存在且内容不同，不得覆盖")
                return descriptor
            existing = self._existing_descriptor(
                target=target,
                source_digest=source_digest,
                artifact_name=artifact_name,
                artifact_type=artifact_type,
                cwd=str(self._layout_path.parent),
            )
            if existing is not None:
                self._resolved[target] = (source_digest, existing)
            return existing

    def materialize(
        self,
        *,
        artifact_name: str,
        version: str,
        digest: str,
        destination: str | Path,
    ) -> Path:
        """只按不可变 digest 拉取 OCI 内容，拒绝可移动 tag。"""

        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("能力制品必须使用冻结 OCI digest")
        output = Path(destination).resolve()
        output.mkdir(parents=True, exist_ok=False)
        try:
            with self._lock(), self._file_lock:
                target = f"{artifact_name}:{version}"
                descriptor = self._runner(
                    (
                        self._oras_executable,
                        "manifest",
                        "fetch",
                        target,
                        "--oci-layout-path",
                        str(self._layout_path),
                        "--descriptor",
                    ),
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                if descriptor.returncode != 0:
                    raise RuntimeError(
                        f"ORAS 读取冻结制品失败：{descriptor.stderr.strip()[:500]}"
                    )
                if json.loads(descriptor.stdout).get("digest") != digest:
                    raise ValueError("OCI tag 当前 digest 与 TaskRevision 冻结值不一致")
                completed = self._runner(
                    (
                        self._oras_executable,
                        "pull",
                        target,
                        "--oci-layout-path",
                        str(self._layout_path),
                        "--output",
                        str(output),
                        "--no-tty",
                    ),
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                if completed.returncode != 0:
                    raise RuntimeError(
                        f"ORAS 物化冻结制品失败：{completed.stderr.strip()[:500]}"
                    )
            self._expand_capability_archive(output)
            for path in output.rglob("*"):
                if output not in path.resolve().parents:
                    raise RuntimeError("OCI 制品解包路径越界")
            return output
        except Exception:
            shutil.rmtree(output, ignore_errors=True)
            raise

    @staticmethod
    def _expand_capability_archive(output: Path) -> None:
        """安全展开单层能力归档；普通单文件制品保持既有行为。"""

        archives = [
            path
            for path in output.iterdir()
            if path.is_file()
            and path.name in {
                "mangrove-capability.tar",
                "mangrove-capability.tar.gz",
                "mangrove-capability.tgz",
            }
        ]
        if not archives:
            return
        if len(archives) != 1 or len(list(output.iterdir())) != 1:
            raise RuntimeError("能力归档必须是 OCI 制品中的唯一负载")
        archive = archives[0]
        file_count = 0
        total_bytes = 0
        with tarfile.open(archive, "r:*") as bundle:
            for member in bundle.getmembers():
                if member.issym() or member.islnk() or member.isdev():
                    raise RuntimeError("能力归档不得包含链接或设备文件")
                relative = Path(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError("能力归档路径越界")
                destination = (output / relative).resolve()
                if output.resolve() not in destination.parents:
                    raise RuntimeError("能力归档路径越界")
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise RuntimeError("能力归档包含不支持的成员类型")
                file_count += 1
                total_bytes += member.size
                if file_count > 50_000 or total_bytes > 512 * 1024 * 1024:
                    raise RuntimeError("能力归档超过文件数或解包大小限制")
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError("能力归档成员无法读取")
                with source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
        archive.unlink()
        if not (output / "mangrove-capability.json").is_file():
            raise RuntimeError("能力归档缺少 mangrove-capability.json")
