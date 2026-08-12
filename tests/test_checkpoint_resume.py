# -*- coding: utf-8 -*-
"""数据准备断点续跑测试（Phase 2 Task 2）。"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.checkpoints import Checkpoint
from src.data_prep.graph import parse_node
from src.data_prep.models import (
    DataPrepTaskSpec,
    OutputFormat,
    Recipe,
    SourceLimits,
    SourceSpec,
    SourceType,
)

import json


def _make_raw_artifacts(store: ArtifactStore, task_id: str, count: int = 3):
    artifacts = []
    for i in range(1, count + 1):
        payload = json.dumps(
            {
                "url": f"http://a.com/{i}",
                "title": f"真实文章{i}",
                "content": f"第{i}篇足够长的真实正文内容，详述产品参数与用户体验，内容详实。",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        art = store.write_raw(
            task_id=task_id, source_id="web-1", data=payload,
            uri=f"http://a.com/{i}", media_type="application/json", ext="json",
        )
        artifacts.append(art)
    return artifacts


def _build_spec() -> DataPrepTaskSpec:
    return DataPrepTaskSpec(
        intent="断点续跑测试",
        sources=[SourceSpec(
            source_id="web-1", source_type=SourceType.WEB, locator="http://a.com",
            limits=SourceLimits(max_records=20),
        )],
        cleaning_recipe=Recipe(),
        outputs=[OutputFormat.JSONL],
    )


def test_parse_skips_completed_artifacts():
    """checkpoint 中已处理的 artifact 在恢复时跳过。"""
    task_id = f"resume_{uuid.uuid4().hex[:8]}"
    try:
        store = ArtifactStore()
        artifacts = _make_raw_artifacts(store, task_id, count=3)
        checkpoint = Checkpoint(processed_artifact_ids={
            artifacts[0].artifact_id, artifacts[1].artifact_id,
        })
        state = {
            "task_id": task_id, "spec": _build_spec(), "artifacts": artifacts,
            "record_counts": {"raw": 3}, "checkpoint": checkpoint,
        }
        state.update(asyncio.run(parse_node(state)))

        assert state["parsed_count"] == 1
        updated = state["checkpoint"]
        assert artifacts[2].artifact_id in updated.processed_artifact_ids
        assert artifacts[0].artifact_id in updated.processed_artifact_ids
    finally:
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
