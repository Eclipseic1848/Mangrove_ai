# -*- coding: utf-8 -*-
"""从只读能力挂载解析运行清单。"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .adapters import SkillAdapter
from .models import CapabilityRuntimeManifest


class MountedCapabilityManifest(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    root: Path
    mount_index: int
    manifest: CapabilityRuntimeManifest

    @property
    def container_root(self) -> str:
        return f"/workspace/capabilities/{self.mount_index}"

    @property
    def container_skill_path(self) -> str | None:
        if self.manifest.skill_path is None:
            return None
        return f"{self.container_root}/{self.manifest.skill_path}"


def load_runtime_manifests(
    capability_dirs: tuple[Path, ...],
) -> tuple[MountedCapabilityManifest, ...]:
    """旧能力包可继续只读挂载；只有显式清单才获得可执行身份。"""

    mounted: list[MountedCapabilityManifest] = []
    names: set[str] = set()
    for index, raw_root in enumerate(capability_dirs, start=1):
        if raw_root.is_symlink():
            raise ValueError("能力挂载根目录不得是符号链接")
        root = raw_root.resolve(strict=True)
        manifest_path = root / "mangrove-capability.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            continue
        if manifest_path.stat().st_size > 64 * 1024:
            raise ValueError("能力运行清单超过 64 KiB")
        manifest = CapabilityRuntimeManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        if manifest.name in names:
            raise ValueError(f"能力运行清单名称重复：{manifest.name}")
        names.add(manifest.name)
        if manifest.skill_path is not None:
            raw_skill = root / manifest.skill_path
            if raw_skill.is_symlink():
                raise ValueError("Skill 挂载路径不得是符号链接")
            skill = raw_skill.resolve(strict=True)
            if root not in skill.parents or not skill.is_dir():
                raise ValueError("Skill 挂载路径越过冻结目录")
            if not (skill / "SKILL.md").is_file():
                raise ValueError("Skill 挂载目录缺少 SKILL.md")
            # 生产装载不能绕过 AC-06 的无脚本、大小和路径一致性门。
            SkillAdapter().prepare(skill)
        mounted.append(
            MountedCapabilityManifest(
                root=root,
                mount_index=index,
                manifest=manifest,
            )
        )
    return tuple(mounted)
