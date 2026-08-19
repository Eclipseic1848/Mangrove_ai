# -*- coding: utf-8 -*-
"""AC-07-07 S3：平台快照生成器（脱敏白名单重写 + 确定性重打包）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.capability_catalog import OciArtifactDescriptor
from src.capability_governance.platform_snapshot import (
    PlatformSnapshotGenerator,
)
from src.conversation_steering import CapabilityMaturity, CapabilityPack, ProcedureScope


def _source_pack(digest_char: str = "a") -> CapabilityPack:
    return CapabilityPack(
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.VERIFIED,
        owner_id="owner-a",
    )


def _source_manifest() -> dict:
    """含业务字段的完整个人 manifest（本地能力不带远程引用）。"""
    return {
        "schema_version": 1,
        "name": "python-table-summary",
        "version": "1.0.0",
        "kind": "python",
        "purpose": "汇总季度销售数据用于内部周报",
        "entrypoint": {"program": "python", "arguments": ("main.py",)},
        "healthcheck": {"program": "python", "arguments": ("-c", "print('ok')")},
        "permissions": ("work:write", "workspace:read"),
    }


def _remote_manifest() -> dict:
    """远程 MCP 合法形态：connection_ref 与 secret_ref 是敏感引用，必须被快照删除。"""
    return {
        "schema_version": 1,
        "name": "remote-mcp-bridge",
        "version": "2.0.0",
        "kind": "mcp_remote",
        "purpose": "连接个人网关读取内部数据",
        "connection_ref": "conn-personal-deepseek",
        "secret_ref": "secretref:personal.api.key",
        "permissions": (),
    }


class _FakeArtifactStore:
    """记录 materialize 与 push_file；不依赖真实 ORAS CLI。"""

    def __init__(self, *, materialized_content: dict[str, bytes] | None = None):
        self.materialized_content = materialized_content or {}
        self.materialize_calls: list[tuple] = []
        self.push_calls: list[tuple[str, Path, bytes]] = []

    def materialize(self, *, artifact_name, version, digest, destination) -> Path:
        self.materialize_calls.append((artifact_name, version, digest, destination))
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        for name, content in self.materialized_content.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return output

    def push_file(
        self,
        source_path,
        *,
        artifact_name,
        version,
        artifact_type,
        layer_media_type="application/octet-stream",
    ) -> OciArtifactDescriptor:
        content = Path(source_path).read_bytes()
        self.push_calls.append((artifact_name, version, content))
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        return OciArtifactDescriptor(
            reference=f"oci://{artifact_name}:{version}",
            digest=digest,
            media_type=layer_media_type,
            artifact_type=artifact_type,
        )


def _source_content() -> dict[str, bytes]:
    return {
        "manifest.json": json.dumps(
            _source_manifest(), ensure_ascii=False
        ).encode("utf-8"),
        "main.py": b"print('hello from capability')\n",
    }


def _source_content_standard_manifest() -> dict[str, bytes]:
    """真实能力归档命名：#15 暴露的缺陷——物化目录是 mangrove-capability.json。"""
    return {
        "mangrove-capability.json": json.dumps(
            _source_manifest(), ensure_ascii=False
        ).encode("utf-8"),
        "main.py": b"print('hello from capability')\n",
    }


class TestS3PlatformSnapshotGenerator:
    def test_generate_is_deterministic(self, tmp_path) -> None:
        source_store = _FakeArtifactStore(
            materialized_content=_source_content()
        )
        platform_store = _FakeArtifactStore()
        generator = PlatformSnapshotGenerator(source_store, platform_store)
        first = generator.generate(_source_pack())
        second = generator.generate(_source_pack())
        assert first.platform_digest == second.platform_digest
        # 两次 push 的 tar 字节一致（确定性重打包）。
        assert len(platform_store.push_calls) == 2
        assert platform_store.push_calls[0][2] == platform_store.push_calls[1][2]

    def test_generate_accepts_standard_mangrove_capability_manifest(self, tmp_path) -> None:
        """#15 真实缺陷回归：物化目录用 mangrove-capability.json 时必须可生成。"""
        import io
        import tarfile

        source_store = _FakeArtifactStore(
            materialized_content=_source_content_standard_manifest()
        )
        platform_store = _FakeArtifactStore()
        generator = PlatformSnapshotGenerator(source_store, platform_store)
        snapshot = generator.generate(_source_pack())
        assert snapshot.platform_digest.startswith("sha256:")
        pushed_tar = platform_store.push_calls[0][2]
        with tarfile.open(fileobj=io.BytesIO(pushed_tar), mode="r:") as bundle:
            names = bundle.getnames()
            member = bundle.extractfile("mangrove-capability.json")
            assert member is not None, f"平台快照 tar 必须保留标准 manifest 名，实际: {names}"
            manifest = json.loads(member.read().decode("utf-8"))
        assert manifest["purpose"]  # 中性脱敏 purpose 必须存在（运行时必填）
        assert "main.py" in names

    def test_manifest_whitelist_removes_business_and_sensitive_fields(
        self, tmp_path
    ) -> None:
        source_store = _FakeArtifactStore(
            materialized_content=_source_content()
        )
        platform_store = _FakeArtifactStore()
        generator = PlatformSnapshotGenerator(source_store, platform_store)
        snapshot = generator.generate(_source_pack())
        # purpose 被中性脱敏文案替代（运行时必填，业务描述不进入平台内容）。
        assert "purpose" in snapshot.manifest_summary
        assert "connection_ref" not in snapshot.manifest_summary
        assert "secret_ref" not in snapshot.manifest_summary
        assert "entrypoint" in snapshot.manifest_summary
        assert "permissions" in snapshot.manifest_summary
        # 推送到平台的 tar 内 manifest 不含业务 purpose 与敏感引用。
        import io
        import tarfile

        pushed_tar = platform_store.push_calls[0][2]
        with tarfile.open(fileobj=io.BytesIO(pushed_tar), mode="r:") as bundle:
            names = bundle.getnames()
            member = bundle.extractfile("manifest.json")
            assert member is not None
            manifest = json.loads(member.read().decode("utf-8"))
        assert "汇总季度销售" not in manifest["purpose"]
        assert "连接个人网关" not in json.dumps(manifest)
        assert "connection_ref" not in manifest
        assert "secret_ref" not in manifest
        assert manifest["entrypoint"]["arguments"] == ["main.py"]
        assert manifest["permissions"] == ["work:write", "workspace:read"]
        assert "main.py" in names

    def test_platform_digest_differs_from_source(self, tmp_path) -> None:
        source_store = _FakeArtifactStore(
            materialized_content=_source_content()
        )
        platform_store = _FakeArtifactStore()
        generator = PlatformSnapshotGenerator(source_store, platform_store)
        snapshot = generator.generate(_source_pack())
        assert snapshot.source_digest != snapshot.platform_digest
        assert snapshot.platform_digest.startswith("sha256:")

    def test_remote_connection_refs_are_removed(self, tmp_path) -> None:
        """mcp_remote 的 connection_ref/secret_ref 是敏感引用，快照必须删除。"""
        import io
        import tarfile

        remote_pack = CapabilityPack(
            pack_id="remote-mcp-bridge",
            version="2.0.0",
            digest="sha256:" + "a" * 64,
            scope=ProcedureScope.PERSONAL,
            maturity=CapabilityMaturity.VERIFIED,
            owner_id="owner-a",
        )
        content = {
            "manifest.json": json.dumps(
                _remote_manifest(), ensure_ascii=False
            ).encode("utf-8"),
        }
        source_store = _FakeArtifactStore(materialized_content=content)
        platform_store = _FakeArtifactStore()
        generator = PlatformSnapshotGenerator(source_store, platform_store)
        snapshot = generator.generate(remote_pack)
        assert "connection_ref" not in snapshot.manifest_summary
        assert "secret_ref" not in snapshot.manifest_summary
        pushed_tar = platform_store.push_calls[0][2]
        with tarfile.open(fileobj=io.BytesIO(pushed_tar), mode="r:") as bundle:
            member = bundle.extractfile("manifest.json")
            assert member is not None
            manifest = json.loads(member.read().decode("utf-8"))
        assert "connection_ref" not in manifest
        assert "secret_ref" not in manifest
        assert manifest["kind"] == "mcp_remote"

    def test_rejects_invalid_source_manifest(self, tmp_path) -> None:
        content = _source_content()
        content["manifest.json"] = json.dumps(
            {"schema_version": 1, "name": "bad"}  # 缺少必填字段
        ).encode("utf-8")
        source_store = _FakeArtifactStore(materialized_content=content)
        platform_store = _FakeArtifactStore()
        generator = PlatformSnapshotGenerator(source_store, platform_store)
        with pytest.raises(ValueError):
            generator.generate(_source_pack())
        assert platform_store.push_calls == []

    def test_entrypoint_environment_and_working_directory_are_sanitized(
        self, tmp_path
    ) -> None:
        """entrypoint 的 environment/working_directory 可能携带宿主路径，必须清洗。

        凭证类环境变量在来源 manifest 模型层已被禁止（Secret 拒绝测试见模型层）；
        这里验证合法来源的绝对工作目录与环境配置在快照中被归一清空。
        """
        import io
        import tarfile

        manifest = _source_manifest()
        manifest["entrypoint"] = {
            "program": "python",
            "arguments": ("main.py",),
            "environment": (("LANG", "zh_CN.UTF-8"),),
            "working_directory": "C:\\Users\\owner-a\\workspace",
        }
        content = _source_content()
        content["manifest.json"] = json.dumps(
            manifest, ensure_ascii=False
        ).encode("utf-8")
        source_store = _FakeArtifactStore(materialized_content=content)
        platform_store = _FakeArtifactStore()
        generator = PlatformSnapshotGenerator(source_store, platform_store)
        generator.generate(_source_pack())
        pushed_tar = platform_store.push_calls[0][2]
        with tarfile.open(fileobj=io.BytesIO(pushed_tar), mode="r:") as bundle:
            member = bundle.extractfile("manifest.json")
            assert member is not None
            sanitized = json.loads(member.read().decode("utf-8"))
        assert sanitized["entrypoint"]["environment"] == []
        assert sanitized["entrypoint"]["working_directory"] == "."

    def test_tar_members_are_normalized(self, tmp_path) -> None:
        """打包成员无绝对路径、无越界、时间戳归一（确定性来源之一）。"""
        import io
        import tarfile

        content = _source_content()
        content["nested/deep/payload.py"] = b"deep = True\n"
        source_store = _FakeArtifactStore(materialized_content=content)
        platform_store = _FakeArtifactStore()
        generator = PlatformSnapshotGenerator(source_store, platform_store)
        generator.generate(_source_pack())
        pushed_tar = platform_store.push_calls[0][2]
        with tarfile.open(fileobj=io.BytesIO(pushed_tar), mode="r:") as bundle:
            for member in bundle.getmembers():
                assert not member.name.startswith("/")
                assert ".." not in Path(member.name).parts
                assert member.mtime > 0
        # 两次打包同一 tar 字节：成员时间戳与顺序均确定。
        first = platform_store.push_calls[0][2]
        generator.generate(_source_pack())
        assert platform_store.push_calls[1][2] == first
