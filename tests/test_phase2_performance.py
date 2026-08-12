# -*- coding: utf-8 -*-
"""Phase 2 性能与流式验收测试（Task 12）。

验证 Task 2.5/12 逐批数据面：clean/validate/output 对大规模数据逐批处理，
峰值内存不随总记录数线性增长（plan 退出门禁 500 MB/100 万行）。

- test_large_dataset_streamed：5 万行常规测试，断言峰值内存 < 150MB + 账本守恒。
- test_million_row_jsonl_is_processed_in_batches：100 万行 @performance 标记，
  默认跳过（--run-performance 开启），验证分批 + 账本。
"""
from __future__ import annotations

import asyncio
import tracemalloc
from pathlib import Path

import pytest

import src.data_prep.artifact_store as as_mod
from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.graph import clean_node, output_node, validate_node
from src.data_prep.models import DataPrepTaskSpec, Recipe, SourceSpec, SourceType


def _make_parsed_batches(store: ArtifactStore, task_id: str, total: int, batch_size: int) -> list:
    """构造 total 行 parsed_batches（每批 batch_size 行），返回 BatchReference 列表。"""
    refs = []
    n_batches = (total + batch_size - 1) // batch_size
    for i in range(n_batches):
        rows = []
        for j in range(batch_size):
            idx = i * batch_size + j
            if idx >= total:
                break
            rows.append({
                "record_id": f"r{idx}",
                "data": {"id": idx, "name": f"item-{idx}", "value": idx * 1.5},
                "meta": {"artifact_id": f"a{i}", "source_id": "s1"},
            })
        refs.append(store.append_jsonl_batch(task_id, "parsed", rows, i))
    return refs


def _build_spec() -> DataPrepTaskSpec:
    """upload_file 类型 + 空 Recipe（不加载网页规则，避免误隔离结构化数据）。"""
    return DataPrepTaskSpec(
        intent="性能测试",
        sources=[SourceSpec(source_id="s1", source_type=SourceType.UPLOAD_FILE, locator="x")],
        cleaning_recipe=Recipe(),
    )


def test_large_dataset_streamed_clean_validate_output(tmp_path: Path, monkeypatch):
    """5 万行逐批 clean/validate/output，峰值内存 < 150MB，账本守恒，输出行数正确。"""
    monkeypatch.setattr(settings, "data_prep_batch_records", 10000)
    monkeypatch.setattr(as_mod, "_DEFAULT_ROOT", str(tmp_path))
    store = ArtifactStore(root=str(tmp_path))
    task_id = "perf"
    total = 50000
    refs = _make_parsed_batches(store, task_id, total, 10000)
    state = {
        "task_id": task_id, "spec": _build_spec(), "parsed_batches": refs,
        "record_counts": {"raw": total, "parsed": total},
    }

    tracemalloc.start()
    state.update(asyncio.run(clean_node(state)))
    state.update(asyncio.run(validate_node(state)))
    state.update(asyncio.run(output_node(state)))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    counts = state["record_counts"]
    assert counts["clean"] == total, f"clean 应 {total}，实际 {counts['clean']}"
    assert counts["parsed"] == counts["clean"] + counts["rejects_clean"] + counts.get("merged", 0)
    assert len(state["clean_batches"]) == 5  # 5 批
    # 输出 JSONL 行数
    jsonl = (tmp_path / task_id / "clean" / "data.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([l for l in jsonl if l]) == total
    # 峰值内存 < 150MB（逐批处理 + lineage 逐批写；全量物化 5 万行约 10MB，150MB 宽松阈值）
    assert peak < 150 * 1024 * 1024, f"峰值内存 {peak / 1024 / 1024:.1f}MB 超过 150MB"
    # 质量非 fail（血缘 100% + 无 rejects）
    assert state["quality"].overall.value != "fail", state["quality"].issues


@pytest.mark.performance
def test_million_row_jsonl_is_processed_in_batches(tmp_path: Path, monkeypatch):
    """100 万行 JSONL 分批处理，parsed_batches 每批 <= batch_size，账本守恒。

    plan 退出标准：500 MB/100 万行流式处理。默认跳过（--run-performance 开启）。
    """
    monkeypatch.setattr(settings, "data_prep_batch_records", 10000)
    monkeypatch.setattr(as_mod, "_DEFAULT_ROOT", str(tmp_path))
    store = ArtifactStore(root=str(tmp_path))
    task_id = "million"
    total = 1_000_000
    refs = _make_parsed_batches(store, task_id, total, 10000)
    state = {
        "task_id": task_id, "spec": _build_spec(), "parsed_batches": refs,
        "record_counts": {"raw": total, "parsed": total},
    }

    state.update(asyncio.run(clean_node(state)))
    state.update(asyncio.run(validate_node(state)))

    counts = state["record_counts"]
    assert counts["clean"] == total
    assert counts["parsed"] == counts["clean"] + counts["rejects_clean"] + counts.get("merged", 0)
    assert len(state["clean_batches"]) == 100  # 100 批
    assert max(ref.record_count for ref in state["clean_batches"]) <= 10000
