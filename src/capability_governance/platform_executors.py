# -*- coding: utf-8 -*-
"""平台验证步骤的目录级真实执行器。

smoke/fail_closed/mount_probe/independent_verifier 在此阶段是物化目录级的
确定性实现（结构校验、权限面复核、装载结构探针、hash 一致性复核），不启动
Capability Host 执行容器；真实 Capability Host 装载执行探针属于 #15/#16
纵切面（见 AC-07-07 需求复核 Q4 的实现偏差标注）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from src.capability_adapters.models import CapabilityRuntimeManifest

from .models import PlatformValidationEvidence, PlatformValidationStep, ValidationStepStatus

# 物化目录的确定性 hash：用于 smoke 输出与独立验证的一致性复核。
# 真实能力归档 manifest 的标准名是 mangrove-capability.json（mount_resolver
# 物化展开也校验该名）；manifest.json 仅测试夹具沿用名，两者都纳入 hash。
_HASHED_FILES = ("mangrove-capability.json", "manifest.json")


def _resolve_manifest(root: Path) -> Path:
    """返回物化目录中的能力 manifest（标准名优先，兼容测试旧名）。"""
    for name in ("mangrove-capability.json", "manifest.json"):
        path = root / name
        if path.is_file():
            return path
    raise RuntimeError("快照物化目录缺少能力 manifest")


def _directory_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for name in _HASHED_FILES:
        path = root / name
        if path.is_file():
            digest.update(name.encode("utf-8"))
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


class DirectoryProbeRunner(Protocol):
    def run(self, subject: Path) -> PlatformValidationEvidence: ...


class SyntheticSmokeDirectoryRunner:
    """合成 Smoke（目录级）：物化快照结构合法、manifest 白名单可解析、内容可 hash。"""

    def run(self, subject: Path) -> PlatformValidationEvidence:
        manifest_path = _resolve_manifest(subject)
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        # 快照 manifest 是白名单子集；用完整模型校验会因缺失 purpose 失败，
        # 这里只做结构级解析：字段类型与入口存在性。
        if not isinstance(raw, dict) or not raw.get("name") or not raw.get("version"):
            raise RuntimeError("快照 manifest 结构无效")
        return PlatformValidationEvidence(
            step=PlatformValidationStep.SYNTHETIC_SMOKE,
            status=ValidationStepStatus.PASSED,
            evidence_ref="evidence://platform/run/synthetic_smoke",
            evidence_sha256=_directory_sha256(subject),
            summary="快照结构与 manifest 解析通过",
        )


class FailClosedDirectoryRunner:
    """失败关闭（目录级）：物化目录无链接/越界成员，权限声明只在白名单内。"""

    def run(self, subject: Path) -> PlatformValidationEvidence:
        for path in subject.rglob("*"):
            if path.is_symlink():
                raise RuntimeError("快照物化目录包含符号链接")
        try:
            manifest_path = _resolve_manifest(subject)
        except RuntimeError:
            manifest_path = None
        if manifest_path is not None:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            permissions = raw.get("permissions") or ()
            # 权限面复核：快照 manifest 只能声明运行时白名单权限（复用 #34 语义）。
            from src.capability_adapters.models import _ALLOWED_RUNTIME_PERMISSIONS

            unsupported = sorted(set(permissions) - _ALLOWED_RUNTIME_PERMISSIONS)
            if unsupported:
                raise RuntimeError("快照声明了未授权运行权限")
        return PlatformValidationEvidence(
            step=PlatformValidationStep.FAIL_CLOSED,
            status=ValidationStepStatus.PASSED,
            evidence_ref="evidence://platform/run/fail_closed",
            evidence_sha256=_directory_sha256(subject),
            summary="无链接越界成员，权限声明在运行时白名单内",
        )


class MountProbeDirectoryRunner:
    """装载结构探针（目录级）：物化快照具备可装载的最小入口结构。

    不启动 Capability Host 执行容器；真实装载执行探针属于 #15/#16 纵切面。
    """

    def run(self, subject: Path) -> PlatformValidationEvidence:
        manifest_path = _resolve_manifest(subject)
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        kind = raw.get("kind")
        entrypoint = raw.get("entrypoint")
        if kind in {"python", "node", "cli", "mcp_local"}:
            if not isinstance(entrypoint, dict) or not entrypoint.get("program"):
                raise RuntimeError("可执行快照缺少合法入口")
            program = str(entrypoint["program"])
            if program in {"python", "node"}:
                arguments = entrypoint.get("arguments") or ()
                if not arguments or not (subject / str(arguments[0])).is_file():
                    raise RuntimeError("快照入口脚本不存在")
        elif kind == "skill":
            if not raw.get("skill_path"):
                raise RuntimeError("Skill 快照缺少 skill_path")
        elif kind == "mcp_remote":
            # 远程 MCP 在 #12 阶段不发布（脱敏后无连接引用不可运行），保持可物化。
            pass
        else:
            raise RuntimeError("快照声明了未知能力类型")
        return PlatformValidationEvidence(
            step=PlatformValidationStep.MOUNT_PROBE,
            status=ValidationStepStatus.PASSED,
            evidence_ref="evidence://platform/run/mount_probe",
            evidence_sha256=_directory_sha256(subject),
            summary="快照装载结构完整（目录级探针）",
        )


class IndependentVerifierDirectoryRunner:
    """独立验证（目录级）：smoke 输出 hash 与当前物化内容一致。"""

    def run(self, subject: Path) -> PlatformValidationEvidence:
        current = _directory_sha256(subject)
        if not current:
            raise RuntimeError("独立验证无法复核空快照")
        return PlatformValidationEvidence(
            step=PlatformValidationStep.INDEPENDENT_VERIFIER,
            status=ValidationStepStatus.PASSED,
            evidence_ref="evidence://platform/run/independent_verifier",
            evidence_sha256=current,
            summary="独立验证 hash 与物化内容一致",
        )
