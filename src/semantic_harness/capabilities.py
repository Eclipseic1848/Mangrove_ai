# -*- coding: utf-8 -*-
"""显式能力注册表；不允许按用户输入动态导入或执行代码。"""
from __future__ import annotations

from typing import Callable, Dict
from functools import lru_cache

from .models import (
    CapabilityLimits,
    CapabilityManifest,
    NetworkAccess,
    ResourceClass,
    SideEffect,
)


TABLE_DUCKDB_MANIFEST = CapabilityManifest(
    capability_id="table.duckdb",
    version="1.0.0",
    accepts=("csv", "tsv", "xlsx", "parquet", "json", "jsonl"),
    produces=("arrow", "parquet", "lineage_parquet"),
    operations=(
        "filter",
        "project",
        "rename",
        "sort",
        "union",
        "join",
        "deduplicate",
        "aggregate",
    ),
    deterministic=True,
    evidence_preserving=True,
    side_effect=SideEffect.READ_ONLY,
    network=NetworkAccess.NONE,
    resource_class=ResourceClass.CPU_MEDIUM,
    limits=CapabilityLimits(
        max_bytes=10 * 1024**3,
        max_rows=100_000_000,
        timeout_seconds=600,
        max_concurrency=4,
    ),
    healthcheck="duckdb.connect(':memory:').execute('SELECT 1')",
    parameters_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["physical_plan"],
    },
)

DOCUMENT_EVIDENCE_MANIFEST = CapabilityManifest(
    capability_id="document.evidence",
    version="1.0.0",
    accepts=("pdf", "docx", "pptx", "html", "markdown", "txt", "xml"),
    produces=("document_ast", "evidence_graph", "verification_report"),
    operations=(
        "verbatim",
        "compare",
        "audit",
        "summarize",
        "rewrite",
        "translate",
        "compose",
    ),
    deterministic=False,
    evidence_preserving=True,
    side_effect=SideEffect.READ_ONLY,
    network=NetworkAccess.LAN,
    resource_class=ResourceClass.CPU_MEDIUM,
    limits=CapabilityLimits(
        max_bytes=10 * 1024**3,
        max_pages=10_000,
        timeout_seconds=600,
        max_concurrency=4,
    ),
    healthcheck="document parser imports",
    parameters_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["physical_plan"],
    },
)


class CapabilityRegistry:
    """能力 ID 到固定实现的只读映射。"""

    def __init__(self) -> None:
        self._manifests: Dict[str, CapabilityManifest] = {}
        self._executors: Dict[str, Callable] = {}
        self._healthchecks: Dict[str, Callable[[], bool]] = {}

    def register(
        self,
        manifest: CapabilityManifest,
        executor: Callable,
        healthcheck: Callable[[], bool],
    ) -> None:
        if manifest.capability_id in self._manifests:
            raise ValueError(f"能力已登记：{manifest.capability_id}")
        self._manifests[manifest.capability_id] = manifest
        self._executors[manifest.capability_id] = executor
        self._healthchecks[manifest.capability_id] = healthcheck

    def manifest(self, capability_id: str) -> CapabilityManifest:
        try:
            return self._manifests[capability_id]
        except KeyError as exc:
            raise KeyError(f"未登记能力：{capability_id}") from exc

    def executor(self, capability_id: str) -> Callable:
        self.manifest(capability_id)
        return self._executors[capability_id]

    def manifests(self) -> tuple[CapabilityManifest, ...]:
        return tuple(self._manifests[key] for key in sorted(self._manifests))

    def is_healthy(self, capability_id: str) -> bool:
        self.manifest(capability_id)
        try:
            return bool(self._healthchecks[capability_id]())
        except Exception:
            return False


@lru_cache(maxsize=1)
def get_capability_registry() -> CapabilityRegistry:
    """构建固定注册表；延迟导入执行器以避免模块循环。"""

    from .document_executor import execute_document_plan
    from .table_executor import execute_physical_plan

    def duckdb_healthcheck() -> bool:
        import duckdb

        connection = duckdb.connect(database=":memory:")
        try:
            return connection.execute("SELECT 1").fetchone() == (1,)
        finally:
            connection.close()

    registry = CapabilityRegistry()
    registry.register(
        TABLE_DUCKDB_MANIFEST,
        execute_physical_plan,
        duckdb_healthcheck,
    )
    registry.register(
        DOCUMENT_EVIDENCE_MANIFEST,
        execute_document_plan,
        lambda: all(
            __import__(module)
            for module in ("docx", "pptx", "markdown_it", "rapidfuzz")
        ),
    )
    return registry
