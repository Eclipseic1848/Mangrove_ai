# -*- coding: utf-8 -*-
"""把已验证个人能力复制为删除 Owner 依赖的脱敏平台快照。

快照只保留运行必需字段，业务描述、连接引用与 Secret 引用一律不进入平台内容；
同一来源重复生成必须得到同一平台 digest（确定性重打包），这是"重复发布不产生
重复平台版本"的内容层保证。
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tarfile
import tempfile
from typing import Protocol

from src.capability_adapters.models import CapabilityRuntimeManifest
from src.capability_catalog import OciArtifactDescriptor
from src.conversation_steering import CapabilityPack

from .models import PlatformSnapshot


class ArtifactStore(Protocol):
    """快照生成器需要的 OCI 制品边界；生产实现是 OrasOciLayoutStore。"""

    def materialize(
        self,
        *,
        artifact_name: str,
        version: str,
        digest: str,
        destination: str | Path,
    ) -> Path: ...

    def push_file(
        self,
        source_path: str | Path,
        *,
        artifact_name: str,
        version: str,
        artifact_type: str,
        layer_media_type: str = "application/octet-stream",
    ) -> OciArtifactDescriptor: ...


# 快照 manifest 白名单：只保留执行能力必需的结构字段。
_MANIFEST_KEEP_FIELDS = (
    "schema_version",
    "name",
    "version",
    "kind",
    "entrypoint",
    "healthcheck",
    "skill_path",
    "permissions",
)
# 平台快照 manifest 的中性脱敏 purpose：运行时模型要求 purpose 必填
# （CapabilityRuntimeManifest），但个人业务描述不得进入平台内容，因此
# 用固定中性文案替代删除（#15 真实装载暴露：缺 purpose 直接 PI_RUNTIME_FAILED）。
_SANITIZED_PURPOSE = "平台能力（脱敏）：按任务要求执行能力声明的数据处理"

_SNAPSHOT_TAR_NAME = "mangrove-capability.tar"
# 成员时间戳归一：tar 头的时间字段参与字节级 digest，必须固定才可确定重打包。
_FIXED_MTIME = 946684800  # 2000-01-01T00:00:00Z
_FIXED_MODE = 0o644
_ARTIFACT_TYPE = "application/vnd.mangrove.capability.v1"


def _pack_tar(source_dir: Path, output: Path) -> None:
    """按 arcname 排序、时间戳/权限归一打包；同源目录必然产出相同字节。"""

    members = sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file()
        and path.name != _SNAPSHOT_TAR_NAME
    )
    with tarfile.open(output, "w") as bundle:
        for path in members:
            relative = path.relative_to(source_dir).as_posix()
            info = tarfile.TarInfo(relative)
            info.size = path.stat().st_size
            info.mtime = _FIXED_MTIME
            info.mode = _FIXED_MODE
            with path.open("rb") as stream:
                bundle.addfile(info, stream)


class PlatformSnapshotGenerator:
    """从个人 OCI 内容生成独立平台 OCI 内容；不修改来源 Layout。"""

    def __init__(
        self,
        source_store: ArtifactStore,
        platform_store: ArtifactStore,
    ) -> None:
        self._source_store = source_store
        self._platform_store = platform_store

    def generate(self, pack: CapabilityPack) -> PlatformSnapshot:
        work = Path(tempfile.mkdtemp(prefix="platform-snapshot-"))
        try:
            materialized = self._source_store.materialize(
                artifact_name=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
                destination=work / "source",
            )
            # 真实能力归档的标准 manifest 名是 mangrove-capability.json（与
            # mount_resolver 物化展开校验一致）；manifest.json 仅是测试夹具沿用名。
            # 重打包必须保留源 manifest 文件名，否则平台快照物化展开会失败。
            manifest_path = materialized / "mangrove-capability.json"
            if not manifest_path.is_file():
                manifest_path = materialized / "manifest.json"
            if not manifest_path.is_file():
                raise ValueError("个人能力缺少能力 manifest")
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            # 来源合法性必须先按完整模型校验；白名单重写只能发生在合法 manifest 上。
            CapabilityRuntimeManifest.model_validate(raw)
            sanitized = {
                key: raw[key]
                for key in _MANIFEST_KEEP_FIELDS
                if key in raw
            }
            # 运行时模型要求 purpose 必填；业务 purpose 是脱敏目标，用中性文案替代。
            sanitized["purpose"] = _SANITIZED_PURPOSE
            # entrypoint/healthcheck 的 environment 可能携带凭证、working_directory
            # 可能携带宿主绝对路径；快照统一清空环境并归一工作目录。
            for command_key in ("entrypoint", "healthcheck"):
                command = sanitized.get(command_key)
                if isinstance(command, dict):
                    sanitized[command_key] = {
                        **command,
                        "environment": (),
                        "working_directory": ".",
                    }
            manifest_path.write_text(
                json.dumps(
                    sanitized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            tar_path = work / _SNAPSHOT_TAR_NAME
            _pack_tar(materialized, tar_path)
            descriptor = self._platform_store.push_file(
                tar_path,
                artifact_name=pack.pack_id,
                version=pack.version,
                artifact_type=_ARTIFACT_TYPE,
            )
            return PlatformSnapshot(
                pack_id=pack.pack_id,
                version=pack.version,
                source_digest=pack.digest,
                platform_digest=descriptor.digest,
                manifest_summary=tuple(sorted(sanitized)),
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
