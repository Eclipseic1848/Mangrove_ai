# -*- coding: utf-8 -*-
"""批次 0 的表格 merge/aggregate 正反赛马。"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any


def _stringify(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {str(key): str(value) for key, value in record.items()}
        for record in records
    ]


def _expected(root: Path) -> dict[str, Any]:
    return json.loads((root / "expected.json").read_text(encoding="utf-8"))


def _result(
    *,
    capability_id: str,
    merge: list[dict[str, Any]],
    aggregate: list[dict[str, Any]],
    missing_column_rejected: bool,
    duplicate_join_rejected: bool,
    started: float,
    root: Path,
) -> dict[str, Any]:
    expected = _expected(root)
    merge_records = _stringify(merge)
    aggregate_records = _stringify(aggregate)
    checks = {
        "merge_exact": merge_records == expected["merge"],
        "merge_ledger_conserved": len(merge_records) == 4,
        "aggregate_exact": aggregate_records == expected["aggregate"],
        "aggregate_recalculable": sum(
            float(item["费用合计"]) for item in aggregate_records
        )
        == 1000.0,
        "missing_column_rejected": missing_column_rejected,
        "duplicate_join_key_rejected": duplicate_join_rejected,
    }
    return {
        "capability_id": capability_id,
        "status": "pass" if all(checks.values()) else "fail",
        "quality_score": round(sum(checks.values()) / len(checks), 6),
        "checks": checks,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def _reject_duplicate_keys(values: list[Any]) -> None:
    if len(values) != len(set(values)):
        raise ValueError("join_cardinality_violation")


def _run_pandas(root: Path) -> dict[str, Any]:
    import pandas as pd

    started = time.perf_counter()
    facts = pd.read_parquet(root / "facts.parquet")
    departments = pd.read_parquet(root / "departments.parquet")
    _reject_duplicate_keys(departments["部门"].tolist())
    merge = (
        facts.merge(departments, on="部门", validate="many_to_one")[
            ["记录号", "姓名", "区域"]
        ]
        .to_dict(orient="records")
    )
    aggregate_frame = facts.assign(费用=facts["费用"].astype(float))
    aggregate = (
        aggregate_frame.groupby("部门", as_index=False)["费用"]
        .sum()
        .sort_values("部门")
        .rename(columns={"费用": "费用合计"})
    )
    aggregate["费用合计"] = aggregate["费用合计"].map(lambda value: f"{value:.2f}")
    missing_column_rejected = False
    duplicate_join_rejected = False
    try:
        facts[["不存在列"]]
    except KeyError:
        missing_column_rejected = True
    try:
        duplicated = pd.read_parquet(root / "departments-duplicate.parquet")
        _reject_duplicate_keys(duplicated["部门"].tolist())
    except ValueError:
        duplicate_join_rejected = True
    return _result(
        capability_id="table.pandas",
        merge=merge,
        aggregate=aggregate.to_dict(orient="records"),
        missing_column_rejected=missing_column_rejected,
        duplicate_join_rejected=duplicate_join_rejected,
        started=started,
        root=root,
    )


def _run_polars(root: Path) -> dict[str, Any]:
    import polars as pl

    started = time.perf_counter()
    facts = pl.read_parquet(root / "facts.parquet")
    departments = pl.read_parquet(root / "departments.parquet")
    _reject_duplicate_keys(departments["部门"].to_list())
    merge = facts.join(
        departments,
        on="部门",
        how="inner",
        validate="m:1",
    ).select(["记录号", "姓名", "区域"])
    aggregate = (
        facts.with_columns(pl.col("费用").cast(pl.Float64))
        .group_by("部门")
        .agg(pl.col("费用").sum().alias("费用合计"))
        .sort("部门")
        .with_columns(pl.col("费用合计").round(2).cast(pl.String))
    )
    missing_column_rejected = False
    duplicate_join_rejected = False
    try:
        facts.select("不存在列")
    except Exception:  # noqa: BLE001 - 不同 Polars 版本异常类型不同
        missing_column_rejected = True
    try:
        duplicated = pl.read_parquet(root / "departments-duplicate.parquet")
        _reject_duplicate_keys(duplicated["部门"].to_list())
    except ValueError:
        duplicate_join_rejected = True
    aggregate_records = aggregate.to_dicts()
    for item in aggregate_records:
        item["费用合计"] = f"{float(item['费用合计']):.2f}"
    return _result(
        capability_id="table.polars",
        merge=merge.to_dicts(),
        aggregate=aggregate_records,
        missing_column_rejected=missing_column_rejected,
        duplicate_join_rejected=duplicate_join_rejected,
        started=started,
        root=root,
    )


def _run_duckdb(root: Path) -> dict[str, Any]:
    import duckdb

    started = time.perf_counter()
    connection = duckdb.connect(database=":memory:")
    try:
        merge_rows = connection.execute(
            """
            SELECT f."记录号", f."姓名", d."区域"
            FROM read_parquet(?) f
            JOIN read_parquet(?) d USING ("部门")
            ORDER BY f."记录号"
            """,
            [str(root / "facts.parquet"), str(root / "departments.parquet")],
        ).fetchall()
        aggregate_rows = connection.execute(
            """
            SELECT "部门", printf('%.2f', SUM(CAST("费用" AS DOUBLE))) AS "费用合计"
            FROM read_parquet(?)
            GROUP BY "部门"
            ORDER BY "部门"
            """,
            [str(root / "facts.parquet")],
        ).fetchall()
        duplicate_count = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT "部门" FROM read_parquet(?)
                GROUP BY "部门" HAVING COUNT(*) > 1
            )
            """,
            [str(root / "departments-duplicate.parquet")],
        ).fetchone()[0]
        missing_column_rejected = False
        try:
            connection.execute(
                'SELECT "不存在列" FROM read_parquet(?)',
                [str(root / "facts.parquet")],
            )
        except duckdb.BinderException:
            missing_column_rejected = True
    finally:
        connection.close()
    return _result(
        capability_id="table.duckdb",
        merge=[
            {"记录号": row[0], "姓名": row[1], "区域": row[2]}
            for row in merge_rows
        ],
        aggregate=[
            {"部门": row[0], "费用合计": row[1]} for row in aggregate_rows
        ],
        missing_column_rejected=missing_column_rejected,
        duplicate_join_rejected=duplicate_count > 0,
        started=started,
        root=root,
    )


def run_table_operation_suite(capability_id: str, fixture_root: Path) -> dict[str, Any]:
    runners = {
        "table.duckdb": _run_duckdb,
        "table.polars": _run_polars,
        "table.pandas": _run_pandas,
    }
    try:
        runner = runners[capability_id]
    except KeyError as exc:
        raise KeyError(f"未登记的表格候选：{capability_id}") from exc
    return runner(fixture_root.resolve())
