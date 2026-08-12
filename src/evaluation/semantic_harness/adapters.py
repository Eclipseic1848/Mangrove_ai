# -*- coding: utf-8 -*-
"""批次 0 表格候选适配器；所有候选输出同一评测协议。"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
from pathlib import Path
import time
from typing import Any, Protocol

from src.semantic_harness.models import (
    CapabilityLimits,
    CapabilityManifest,
    NetworkAccess,
    ResourceClass,
    SideEffect,
)

from .fixtures import Batch0Case


@dataclass(frozen=True)
class AdapterOutput:
    payload: dict[str, Any]
    duration_ms: int
    version: str


class TableAdapter(Protocol):
    capability_id: str

    def manifest(self) -> CapabilityManifest: ...

    def run(self, case: Batch0Case, source_path: Path) -> AdapterOutput: ...


def _manifest(capability_id: str, package: str) -> CapabilityManifest:
    return CapabilityManifest(
        capability_id=capability_id,
        version=importlib.metadata.version(package),
        accepts=("parquet",),
        produces=("benchmark_table_json",),
        operations=("filter", "project"),
        deterministic=True,
        evidence_preserving=True,
        side_effect=SideEffect.NONE,
        network=NetworkAccess.NONE,
        resource_class=ResourceClass.CPU_SMALL,
        limits=CapabilityLimits(
            max_rows=1_000_000,
            timeout_seconds=60,
            max_concurrency=1,
        ),
        healthcheck=f"{capability_id}.import",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["selection", "projection"],
        },
    )


def _finish(
    frame_records: list[dict[str, Any]],
    *,
    case: Batch0Case,
    started: float,
    version: str,
) -> AdapterOutput:
    evidence: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for index, row in enumerate(frame_records):
        source_ref = str(row.pop("__source_ref"))
        records.append({column: row[column] for column in case.projection})
        evidence.append(
            {
                "record_index": index,
                "source_ref": source_ref,
                "selection": dict(case.selection),
            }
        )
    return AdapterOutput(
        payload={
            "table_count": 1,
            "visible_columns": list(case.projection),
            "records": records,
            "evidence": evidence,
        },
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        version=version,
    )


class PandasTableAdapter:
    capability_id = "table.pandas"

    def manifest(self) -> CapabilityManifest:
        return _manifest(self.capability_id, "pandas")

    def run(self, case: Batch0Case, source_path: Path) -> AdapterOutput:
        import pandas as pd

        started = time.perf_counter()
        frame = pd.read_parquet(source_path)
        mask = None
        for field, value in case.selection.items():
            current = frame[field].astype(str) == str(value)
            mask = current if mask is None else mask & current
        filtered = frame.loc[mask, [*case.projection, "__source_ref"]]
        records = filtered.astype(str).to_dict(orient="records")
        return _finish(
            records,
            case=case,
            started=started,
            version=importlib.metadata.version("pandas"),
        )


class DuckDBTableAdapter:
    capability_id = "table.duckdb"

    def manifest(self) -> CapabilityManifest:
        return _manifest(self.capability_id, "duckdb")

    def run(self, case: Batch0Case, source_path: Path) -> AdapterOutput:
        import duckdb

        started = time.perf_counter()
        selected_columns = [*case.projection, "__source_ref"]

        def quote_identifier(value: str) -> str:
            return '"' + value.replace('"', '""') + '"'

        projection_sql = ", ".join(
            quote_identifier(column) for column in selected_columns
        )
        predicates = [
            f"CAST({quote_identifier(field)} AS VARCHAR) = ?"
            for field in case.selection
        ]
        query = (
            f"SELECT {projection_sql} FROM read_parquet(?) "
            f"WHERE {' AND '.join(predicates)}"
        )
        connection = duckdb.connect(database=":memory:")
        try:
            rows = connection.execute(
                query,
                [str(source_path), *[str(value) for value in case.selection.values()]],
            ).fetchall()
        finally:
            connection.close()
        records = [
            {
                column: str(value)
                for column, value in zip(selected_columns, row, strict=True)
            }
            for row in rows
        ]
        return _finish(
            records,
            case=case,
            started=started,
            version=importlib.metadata.version("duckdb"),
        )


class PolarsTableAdapter:
    capability_id = "table.polars"

    def manifest(self) -> CapabilityManifest:
        return _manifest(self.capability_id, "polars")

    def run(self, case: Batch0Case, source_path: Path) -> AdapterOutput:
        import polars as pl

        started = time.perf_counter()
        lazy = pl.scan_parquet(source_path)
        for field, value in case.selection.items():
            lazy = lazy.filter(pl.col(field).cast(pl.String) == str(value))
        frame = lazy.select([*case.projection, "__source_ref"]).collect()
        records = [
            {key: str(value) for key, value in row.items()}
            for row in frame.to_dicts()
        ]
        return _finish(
            records,
            case=case,
            started=started,
            version=importlib.metadata.version("polars"),
        )


def get_table_adapter(capability_id: str) -> TableAdapter:
    adapters: dict[str, type[Any]] = {
        PandasTableAdapter.capability_id: PandasTableAdapter,
        DuckDBTableAdapter.capability_id: DuckDBTableAdapter,
        PolarsTableAdapter.capability_id: PolarsTableAdapter,
    }
    try:
        adapter_type = adapters[capability_id]
    except KeyError as exc:
        raise KeyError(f"未登记的表格候选：{capability_id}") from exc
    return adapter_type()
