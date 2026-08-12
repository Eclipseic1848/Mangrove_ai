# -*- coding: utf-8 -*-
"""uv、npm、Release CLI 与 Agent Skills 的窄适配层。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath

import yaml

from .models import PreparationCommand, PreparationPlan, PreparedCli, PreparedSkill


def _root(path: str | Path) -> Path:
    original = Path(path)
    if original.is_symlink():
        raise ValueError("能力根目录不得是符号链接")
    root = original.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("能力根目录必须是实体目录")
    return root


class PythonAdapter:
    """把 uv 项目收敛为可审计的冻结同步计划。"""

    def prepare(self, path: str | Path) -> PreparationPlan:
        root = _root(path)
        for required in ("pyproject.toml", "uv.lock", ".python-version"):
            if not (root / required).is_file():
                raise ValueError(f"Python 能力缺少 {required}")
        python_version = (root / ".python-version").read_text(
            encoding="utf-8"
        ).strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+", python_version):
            raise ValueError("Python 能力必须冻结精确 patch 版本")
        return PreparationPlan(
            root=root,
            runtime_identity=f"cpython@{python_version}",
            commands=(
                PreparationCommand(argv=("uv", "lock", "--check")),
                PreparationCommand(
                    argv=(
                        "uv",
                        "sync",
                        "--frozen",
                        "--no-dev",
                        "--no-editable",
                    )
                ),
            ),
        )


class NodeAdapter:
    """默认关闭生命周期脚本的 npm lock Adapter。"""

    def __init__(self, *, node_version: str, npm_version: str) -> None:
        self._node_version = node_version.removeprefix("v")
        self._npm_version = npm_version

    def prepare(self, path: str | Path) -> PreparationPlan:
        root = _root(path)
        for required in ("package.json", "package-lock.json"):
            if not (root / required).is_file():
                raise ValueError(f"Node 能力缺少 {required}")
        return PreparationPlan(
            root=root,
            runtime_identity=(
                f"node@{self._node_version}/npm@{self._npm_version}"
            ),
            commands=(
                PreparationCommand(argv=("npm", "ci", "--ignore-scripts")),
            ),
        )


class CliAdapter:
    """验证官方 Release 资产和解包后唯一入口的 digest。"""

    def prepare(
        self,
        path: str | Path,
        *,
        entrypoint: str,
        expected_digest: str,
        platform: str,
        architecture: str,
        source_ref: str,
        asset_path: str | Path | None = None,
        expected_asset_digest: str | None = None,
    ) -> PreparedCli:
        root = _root(path)
        asset_digest: str | None = None
        if (asset_path is None) != (expected_asset_digest is None):
            raise ValueError("Release 资产路径与 digest 必须同时提供")
        if asset_path is not None:
            raw_asset = Path(asset_path)
            if raw_asset.is_symlink():
                raise ValueError("Release 资产必须是实体文件")
            asset = raw_asset.resolve(strict=True)
            if not asset.is_file():
                raise ValueError("Release 资产必须是实体文件")
            asset_digest = (
                "sha256:" + hashlib.sha256(asset.read_bytes()).hexdigest()
            )
            if asset_digest != expected_asset_digest:
                raise ValueError("Release 资产 digest 校验失败")
        relative = PurePosixPath(entrypoint.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("CLI 入口路径越界")
        raw_binary = root / Path(*relative.parts)
        if raw_binary.is_symlink():
            raise ValueError("CLI 入口必须是能力目录内的实体文件")
        binary = raw_binary.resolve(strict=True)
        if root not in binary.parents or not binary.is_file():
            raise ValueError("CLI 入口必须是能力目录内的实体文件")
        digest = "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()
        if digest != expected_digest:
            raise ValueError("CLI 入口 digest 校验失败")
        return PreparedCli(
            root=root,
            entrypoint=Path(*relative.parts),
            digest=digest,
            asset_digest=asset_digest,
            platform=platform,
            architecture=architecture,
            source_ref=source_ref,
        )


class SkillAdapter:
    """Agent Skills 格式门之外，再收紧路径和可执行内容边界。"""

    _FRONTMATTER = re.compile(r"\A---\n(?P<body>.*?)\n---(?:\n|\Z)", re.DOTALL)
    _MAX_FILES = 256
    _MAX_FILE_BYTES = 1024 * 1024
    _MAX_TOTAL_BYTES = 4 * 1024 * 1024
    _SAFE_DATA_SUFFIXES = {
        ".csv", ".docx", ".gif", ".jpeg", ".jpg", ".json", ".md",
        ".pdf", ".png", ".pptx", ".svg", ".toml", ".tsv", ".txt",
        ".webp", ".xlsx", ".yaml", ".yml",
    }

    def prepare(
        self,
        path: str | Path,
    ) -> PreparedSkill:
        root = _root(path)
        skill_file = root / "SKILL.md"
        if not skill_file.is_file() or skill_file.is_symlink():
            raise ValueError("Skill 缺少 SKILL.md")
        text = skill_file.read_text(encoding="utf-8")
        match = self._FRONTMATTER.match(text)
        if not match:
            raise ValueError("Skill frontmatter 无效")
        fields = yaml.safe_load(match.group("body"))
        if not isinstance(fields, dict):
            raise ValueError("Skill frontmatter 必须是对象")
        name = str(fields.get("name", ""))
        if name != root.name:
            raise ValueError("Skill name 必须与目录名一致")
        if not isinstance(fields.get("description"), str) or not fields["description"].strip():
            raise ValueError("Skill 缺少 description")
        files = [item for item in root.rglob("*") if item.is_file()]
        if len(files) > self._MAX_FILES:
            raise ValueError("Skill 文件数量超过 256")
        total_bytes = 0
        for item in root.rglob("*"):
            resolved = item.resolve()
            if item.is_symlink() or root not in resolved.parents:
                raise ValueError("Skill 包含越界路径或符号链接")
            if item.is_file():
                if item.suffix.casefold() not in self._SAFE_DATA_SUFFIXES:
                    raise ValueError("无脚本 Skill 只能包含已批准的数据文件")
                size = item.stat().st_size
                if size > self._MAX_FILE_BYTES:
                    raise ValueError("Skill 单个文件超过 1 MiB")
                total_bytes += size
        if total_bytes > self._MAX_TOTAL_BYTES:
            raise ValueError("Skill 总大小超过 4 MiB")
        scripts = root / "scripts"
        if scripts.exists() and any(item.is_file() for item in scripts.rglob("*")):
            # Markdown 外壳不能绕过 Python/Node/CLI 的独立适配和隔离门。
            raise ValueError("Skill 脚本必须拆分为独立可执行能力")
        return PreparedSkill(
            root=root,
            name=name,
            skill_file=Path("SKILL.md"),
        )
