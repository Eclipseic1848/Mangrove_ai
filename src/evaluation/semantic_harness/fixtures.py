# -*- coding: utf-8 -*-
"""批次 0 公开 Golden 清单和安全路径解析。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FixtureModel(BaseModel):
    """Golden 清单使用严格模型，防止字段漂移被静默接受。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedTable(FixtureModel):
    records_path: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    visible_columns: tuple[str, ...] = Field(min_length=1)
    table_count: int = Field(ge=0)
    evidence_coverage: float = Field(ge=0, le=1)


class Batch0Case(FixtureModel):
    case_id: str = Field(min_length=1)
    task_family: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    canonical_input: str = Field(min_length=1)
    input_paths: dict[str, str] = Field(min_length=1)
    selection: dict[str, Any] = Field(min_length=1)
    projection: tuple[str, ...] = Field(min_length=1)
    expected: ExpectedTable

    @model_validator(mode="after")
    def validate_projection(self) -> "Batch0Case":
        if self.projection != self.expected.visible_columns:
            raise ValueError("projection 必须与 expected.visible_columns 完全一致")
        return self


class FixtureFile(FixtureModel):
    path: str = Field(min_length=1)
    format: str = Field(min_length=1)
    role: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class Batch0Manifest(FixtureModel):
    manifest_version: str = Field(pattern=r"^1$")
    generated_by: str = Field(min_length=1)
    deidentified: bool
    core_formats: tuple[str, ...] = Field(min_length=1)
    compatibility_formats: tuple[str, ...] = Field(min_length=1)
    files: tuple[FixtureFile, ...] = Field(min_length=1)
    cases: tuple[Batch0Case, ...] = Field(min_length=1)
    root: Path = Field(exclude=True)

    def case(self, case_id: str) -> Batch0Case:
        for item in self.cases:
            if item.case_id == case_id:
                return item
        raise KeyError(f"未知批次 0 样例：{case_id}")

    def resolve(self, relative_path: str, *, require_exists: bool = True) -> Path:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"样例路径越界：{relative_path}") from exc
        if require_exists and not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate


def load_batch0_manifest(path: Path) -> Batch0Manifest:
    """读取清单并验证所有登记文件的路径、大小和哈希。"""

    manifest_path = path.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = Batch0Manifest.model_validate(
        {**payload, "root": manifest_path.parent.resolve()}
    )
    if not manifest.deidentified:
        raise ValueError("公开 Golden 必须明确标记 deidentified=true")

    import hashlib

    for item in manifest.files:
        file_path = manifest.resolve(item.path)
        raw = file_path.read_bytes()
        if len(raw) != item.size_bytes:
            raise ValueError(f"样例大小漂移：{item.path}")
        if hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError(f"样例哈希漂移：{item.path}")

    for case in manifest.cases:
        manifest.resolve(case.canonical_input)
        manifest.resolve(case.expected.records_path)
        for relative_path in case.input_paths.values():
            manifest.resolve(relative_path)
    return manifest
