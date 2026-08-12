# -*- coding: utf-8 -*-
"""数据准备批次存储与流水线测试（Phase 2 Task 1/2）。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import pytest

from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.batches import BatchReference
from src.data_prep.checkpoints import Checkpoint
from src.data_prep.graph import clean_node, output_node, parse_node, profile_node, validate_node
from src.data_prep.output import export_dataset
from src.cleaning.profiler import ProfileAccumulator, profile
from src.config.settings import settings
from src.data_prep.models import (
    DataPrepTaskSpec,
    OutputFormat,
    QualityPolicy,
    QualityReport,
    QualityResult,
    Recipe,
    RecordEnvelope,
    SourceLimits,
    SourceSpec,
    SourceType,
    TargetSchema,
    TargetSchemaField,
)
from src.quality.validators import QualityAccumulator, validate as quality_validate


def test_append_jsonl_batch_writes_hash_and_iterates_rows(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))

    reference = store.append_jsonl_batch(
        task_id="task-1",
        dataset="parsed",
        rows=[{"record_id": "r1"}, {"record_id": "r2"}],
        part_no=0,
    )

    assert isinstance(reference, BatchReference)
    payload = (tmp_path / reference.path).read_bytes()
    assert reference.record_count == 2
    assert reference.byte_count == len(payload)
    assert reference.sha256 == hashlib.sha256(payload).hexdigest()
    assert reference.path == "task-1/parsed/part-00000.jsonl"
    assert list(store.iter_jsonl(reference.path)) == [
        {"record_id": "r1"},
        {"record_id": "r2"},
    ]


def test_append_jsonl_batch_never_overwrites_existing_part(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    store.append_jsonl_batch("task-1", "parsed", [{"record_id": "r1"}], 0)

    with pytest.raises(FileExistsError):
        store.append_jsonl_batch("task-1", "parsed", [{"record_id": "r2"}], 0)


def test_iter_jsonl_rejects_path_outside_store_root(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))

    with pytest.raises(ValueError, match="任务产物目录"):
        list(store.iter_jsonl("../outside.jsonl"))


def test_iter_jsonl_rejects_non_object_rows(tmp_path: Path):
    path = tmp_path / "task-1" / "parsed" / "part-00000.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('["not-an-object"]\n', encoding="utf-8")
    store = ArtifactStore(root=str(tmp_path))

    with pytest.raises(ValueError, match="JSONL 每行必须是 JSON 对象"):
        list(store.iter_jsonl("task-1/parsed/part-00000.jsonl"))


def test_read_jsonl_rejects_path_outside_store_root(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))

    with pytest.raises(ValueError, match="任务产物目录"):
        store.read_jsonl("task-1", "../../outside.jsonl")


def test_read_raw_bytes_rejects_path_outside_store_root(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))

    with pytest.raises(ValueError, match="任务产物目录"):
        store.read_raw_bytes("task-1", "../../outside.bin")


def test_checkpoint_serializes_batch_progress():
    checkpoint = Checkpoint(
        completed_batch_ids=["parsed-00000"],
        next_part_no=1,
    )

    assert checkpoint.to_dict()["completed_batch_ids"] == ["parsed-00000"]
    assert checkpoint.to_dict()["next_part_no"] == 1


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
        intent="批次契约测试",
        sources=[SourceSpec(
            source_id="web-1", source_type=SourceType.WEB, locator="http://a.com",
            limits=SourceLimits(max_records=20),
        )],
        cleaning_recipe=Recipe(),
        outputs=[OutputFormat.JSONL],
    )


async def _run_parse_clean(task_id: str, spec: DataPrepTaskSpec, artifacts):
    state = {
        "task_id": task_id, "spec": spec, "artifacts": artifacts,
        "record_counts": {"raw": len(artifacts)},
    }
    state.update(await parse_node(state))
    state.update(await clean_node(state))
    return state


def test_pipeline_state_uses_batch_references():
    """管线运行后 state 含批次引用而非单路径全量记录。"""
    task_id = f"batch_contract_{uuid.uuid4().hex[:8]}"
    try:
        store = ArtifactStore()
        artifacts = _make_raw_artifacts(store, task_id)
        state = asyncio.run(_run_parse_clean(task_id, _build_spec(), artifacts))

        assert state["parsed_batches"]
        assert state["clean_batches"]
        assert all(isinstance(b, BatchReference) for b in state["parsed_batches"])
        assert all(isinstance(b, BatchReference) for b in state["clean_batches"])
        assert "parsed_path" not in state
        assert "clean_path" not in state
        counts = state["record_counts"]
        assert counts["parsed"] == counts["clean"] + counts["rejects_clean"] + counts.get("merged", 0)
    finally:
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


def test_parse_splits_into_multiple_batches(monkeypatch):
    """batch_size 小于记录数时产生多个批次，part_no 连续。"""
    from src.config.settings import settings

    monkeypatch.setattr(settings, "data_prep_batch_records", 2)
    task_id = f"multibatch_{uuid.uuid4().hex[:8]}"
    try:
        store = ArtifactStore()
        artifacts = _make_raw_artifacts(store, task_id, count=5)
        state = {
            "task_id": task_id, "spec": _build_spec(), "artifacts": artifacts,
            "record_counts": {"raw": 5},
        }
        state.update(asyncio.run(parse_node(state)))

        assert len(state["parsed_batches"]) == 3
        assert state["parsed_count"] == 5
        assert [b.part_no for b in state["parsed_batches"]] == [0, 1, 2]
        assert sum(b.record_count for b in state["parsed_batches"]) == 5
    finally:
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


# ===========================================================================
# Phase 2 Task 2.5：逐批数据面（profile/clean 不全量物化）
# ===========================================================================
def test_profile_accumulator_matches_full():
    """ProfileAccumulator 分批累计结果与全量 profile 一致。"""
    records = [
        RecordEnvelope(
            record_id=f"r{i}",
            data={"content": f"第{i}篇正文", "url": f"http://x/{i}"},
            meta={},
        )
        for i in range(6)
    ]
    full = profile(records)
    acc = ProfileAccumulator()
    acc.add_records(records[:3])
    acc.add_records(records[3:])
    batch_result = acc.finalize()
    assert batch_result.record_count == full.record_count == 6
    assert set(batch_result.fields) == set(full.fields)
    assert batch_result.dup_count == full.dup_count == 0


def test_profile_node_multibatch(monkeypatch):
    """多批 parsed 的 profile_node 逐批累计，结果与记录数一致。"""
    monkeypatch.setattr(settings, "data_prep_batch_records", 2)
    task_id = f"prof_{uuid.uuid4().hex[:8]}"
    try:
        store = ArtifactStore()
        artifacts = _make_raw_artifacts(store, task_id, count=5)
        state = {
            "task_id": task_id, "spec": _build_spec(), "artifacts": artifacts,
            "record_counts": {"raw": 5},
        }
        state.update(asyncio.run(parse_node(state)))
        assert len(state["parsed_batches"]) >= 2  # 多批
        prof = asyncio.run(profile_node(state))
        assert prof["profile"]["record_count"] == 5
    finally:
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


def test_clean_multibatch_ledger(monkeypatch):
    """多批 clean 账本守恒：parsed = clean + rejects_clean + merged。"""
    monkeypatch.setattr(settings, "data_prep_batch_records", 2)
    task_id = f"cleandan_{uuid.uuid4().hex[:8]}"
    try:
        store = ArtifactStore()
        artifacts = _make_raw_artifacts(store, task_id, count=5)
        state = asyncio.run(_run_parse_clean(task_id, _build_spec(), artifacts))
        counts = state["record_counts"]
        assert counts["parsed"] == counts["clean"] + counts["rejects_clean"] + counts.get("merged", 0)
        assert state["clean_batches"]
    finally:
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


def test_clean_cross_batch_dedup(monkeypatch):
    """跨批同 record_id 被 clean_node 去重，计入 merged（不存完整记录）。"""
    monkeypatch.setattr(settings, "data_prep_batch_records", 2)  # 强制 2 批
    task_id = f"crossdedup_{uuid.uuid4().hex[:8]}"
    try:
        store = ArtifactStore()
        long_text = "足够长的真实正文内容，详述产品参数与用户体验，内容详实。"
        batch0 = [
            {"data": {"url": "http://a.com/1", "title": "文章1", "content": long_text},
             "meta": {"artifact_id": "a1", "source_id": "s1"}, "record_id": "dup-1"},
            {"data": {"url": "http://a.com/2", "title": "文章2", "content": long_text + "2"},
             "meta": {"artifact_id": "a2", "source_id": "s1"}, "record_id": "uniq-1"},
        ]
        batch1 = [
            {"data": {"url": "http://a.com/1", "title": "文章1", "content": long_text},
             "meta": {"artifact_id": "a3", "source_id": "s1"}, "record_id": "dup-1"},
            {"data": {"url": "http://a.com/3", "title": "文章3", "content": long_text + "3"},
             "meta": {"artifact_id": "a4", "source_id": "s1"}, "record_id": "uniq-2"},
        ]
        ref0 = store.append_jsonl_batch(task_id, "parsed", batch0, 0)
        ref1 = store.append_jsonl_batch(task_id, "parsed", batch1, 1)
        spec = DataPrepTaskSpec(
            intent="跨批去重",
            sources=[SourceSpec(source_id="s1", source_type=SourceType.WEB, locator="http://a.com")],
            cleaning_recipe=Recipe(),
        )
        state = {
            "task_id": task_id, "spec": spec, "parsed_batches": [ref0, ref1],
            "record_counts": {"raw": 4, "parsed": 4},
        }
        result = asyncio.run(clean_node(state))
        # dup-1 跨批去重：4 条 -> 3 条 clean，跨批去重计入 merged
        assert result["clean_count"] == 3
        assert result["merged_count"] >= 1
        counts = result["record_counts"]
        assert counts["parsed"] == counts["clean"] + counts["rejects_clean"] + counts.get("merged", 0)
    finally:
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


# ===========================================================================
# Phase 2 Task 2.5 阶段2：validate/output 逐批（不全量物化）
# ===========================================================================
def test_quality_accumulator_matches_full():
    """QualityAccumulator 分批累计字段非空率/唯一性与全量 validate 一致。"""
    records = [
        RecordEnvelope(
            record_id=f"r{i}",
            data={"id": i, "name": f"n{i}" if i % 2 == 0 else ""},
            meta={},
        )
        for i in range(6)
    ]
    schema = TargetSchema(
        fields=[
            TargetSchemaField(name="id", dtype="integer", required=True),
            TargetSchemaField(name="name", dtype="string", required=True),
        ],
        primary_key=["id"],
    )
    policy = QualityPolicy()
    counts = {"raw": 6, "parsed": 6, "clean": 6}

    full = quality_validate(
        clean_records=records, record_counts=counts, artifacts=[],
        policy=policy, target_schema=schema, lineage_coverage=1.0,
    )
    acc = QualityAccumulator(required_fields=["id", "name"], primary_key=["id"])
    acc.add_batch(records[:3])
    acc.add_batch(records[3:])
    batch_report = quality_validate(
        record_counts=counts, artifacts=[], policy=policy,
        target_schema=schema, lineage_coverage=1.0, accumulated=acc,
    )

    full_field = next(d for d in full.dimensions if d.name == "字段完整性")
    batch_field = next(d for d in batch_report.dimensions if d.name == "字段完整性")
    assert full_field.value == batch_field.value
    assert full_field.details["per_field"] == batch_field.details["per_field"]

    full_uniq = next(d for d in full.dimensions if d.name == "唯一性")
    batch_uniq = next(d for d in batch_report.dimensions if d.name == "唯一性")
    assert full_uniq.value == batch_uniq.value
    assert full_uniq.details["duplicates"] == batch_uniq.details["duplicates"]
    assert full.overall == batch_report.overall


def test_validate_node_multibatch_lineage_from_file(monkeypatch):
    """多批 clean 的 validate_node 逐批累计，血缘覆盖率从 lineage 文件算。"""
    monkeypatch.setattr(settings, "data_prep_batch_records", 2)
    task_id = f"valmb_{uuid.uuid4().hex[:8]}"
    try:
        store = ArtifactStore()
        rows = [{"_record_id": f"r{i}", "id": i, "name": f"n{i}"} for i in range(5)]
        refs = []
        for i in range(0, 5, 2):
            chunk = rows[i:i + 2]
            refs.append(store.append_jsonl_batch(task_id, "clean_batches", chunk, len(refs)))
        store.write_lineage(task_id, [
            {"record_id": f"r{i}", "artifact_id": f"a{i}", "source_id": "s1"}
            for i in range(5)
        ])
        schema = TargetSchema(
            fields=[TargetSchemaField(name="id", dtype="integer", required=True)],
            primary_key=["id"],
        )
        spec = DataPrepTaskSpec(
            intent="逐批校验",
            sources=[SourceSpec(source_id="s1", source_type=SourceType.WEB, locator="http://a.com")],
            target_schema=schema,
            quality_policy=QualityPolicy(),
        )
        state = {
            "task_id": task_id, "spec": spec, "clean_batches": refs,
            "record_counts": {"raw": 5, "parsed": 5, "clean": 5},
        }
        result = asyncio.run(validate_node(state))
        q = result["quality"]
        lineage_dim = next(d for d in q.dimensions if d.name == "可追溯性")
        assert lineage_dim.value == 1.0
        uniq_dim = next(d for d in q.dimensions if d.name == "唯一性")
        assert uniq_dim.value == 1.0
        field_dim = next(d for d in q.dimensions if d.name == "字段完整性")
        assert field_dim.value == 1.0
        assert q.overall.value != "fail"
    finally:
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


def test_export_dataset_multibatch_formats(tmp_path, monkeypatch):
    """逐批 export_dataset 生成 JSONL/CSV/Parquet，内容与记录数一致。"""
    monkeypatch.setattr(settings, "data_prep_batch_records", 2)
    store = ArtifactStore(root=str(tmp_path))
    task_id = "exp"
    rows = [{"_record_id": f"r{i}", "id": i, "name": f"n{i}"} for i in range(5)]
    refs = []
    for i in range(0, 5, 2):
        chunk = rows[i:i + 2]
        refs.append(store.append_jsonl_batch(task_id, "clean_batches", chunk, len(refs)))

    entries, schema = export_dataset(
        store, task_id, refs, [OutputFormat.JSONL, OutputFormat.CSV, OutputFormat.PARQUET]
    )

    # JSONL 行数与内容
    jsonl_path = tmp_path / task_id / "clean" / "data.jsonl"
    lines = [l for l in jsonl_path.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 5
    rows_back = [json.loads(l) for l in lines]
    assert {r["_record_id"] for r in rows_back} == {f"r{i}" for i in range(5)}

    # schema 逐批推断
    field_names = {f["name"] for f in schema["fields"]}
    assert {"id", "name"} <= field_names
    assert schema["record_count"] == 5

    # CSV 行数（含表头）
    csv_path = tmp_path / task_id / "clean" / "data.csv"
    csv_lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
    assert len(csv_lines) == 6  # 表头 + 5 行

    # Parquet 行数与列
    import pyarrow.parquet as pq
    table = pq.read_table(tmp_path / task_id / "clean" / "data.parquet")
    assert table.num_rows == 5
    assert "id" in table.column_names

    # Manifest 条目
    fmts = {e.format for e in entries}
    assert {OutputFormat.JSONL, OutputFormat.CSV, OutputFormat.PARQUET} <= fmts
    for e in entries:
        assert e.records == 5


def test_output_node_does_not_publish_clean_outputs_when_quality_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data_prep.graph.ArtifactStore", lambda: ArtifactStore(root=str(tmp_path)))
    task_id = "quality-fail"
    store = ArtifactStore(root=str(tmp_path))
    refs = [store.append_jsonl_batch(task_id, "clean_batches", [{"id": 1}], 0)]
    spec = DataPrepTaskSpec(
        intent="失败质量门",
        sources=[SourceSpec(source_id="s1", source_type=SourceType.UPLOAD_FILE, locator="x")],
    )
    state = {
        "task_id": task_id,
        "spec": spec,
        "artifacts": [],
        "clean_batches": refs,
        "record_counts": {"parsed": 2, "clean": 1, "rejects_parse": 1},
        "quality": QualityReport(task_id=task_id, overall=QualityResult.FAIL),
        "status": "FAILED",
    }

    store.write_quality(task_id, state["quality"])
    result = asyncio.run(output_node(state))
    manifest = json.loads(
        (tmp_path / task_id / "manifest.json").read_text(encoding="utf-8")
    )
    assert result["outputs"] == []
    assert manifest["outputs"] == []
    assert not (tmp_path / task_id / "clean" / "data.jsonl").exists()
    assert (tmp_path / task_id / "quality_report.json").exists()


def test_output_node_multibatch_parquet(monkeypatch):
    """output_node 逐批导出默认 JSONL+Parquet，产物行数 == clean_count。"""
    monkeypatch.setattr(settings, "data_prep_batch_records", 2)
    task_id = f"outmb_{uuid.uuid4().hex[:8]}"
    try:
        store = ArtifactStore()
        artifacts = _make_raw_artifacts(store, task_id, count=5)
        spec = DataPrepTaskSpec(
            intent="逐批输出",
            sources=[SourceSpec(
                source_id="web-1", source_type=SourceType.WEB, locator="http://a.com",
                limits=SourceLimits(max_records=20),
            )],
            cleaning_recipe=Recipe(),
        )  # outputs=None -> 默认 JSONL+Parquet
        state = {
            "task_id": task_id, "spec": spec, "artifacts": artifacts,
            "record_counts": {"raw": 5},
        }
        state.update(asyncio.run(parse_node(state)))
        state.update(asyncio.run(clean_node(state)))
        result = asyncio.run(output_node(state))

        task_dir = Path("downloads") / task_id
        jsonl_path = task_dir / "clean" / "data.jsonl"
        parquet_path = task_dir / "clean" / "data.parquet"
        assert jsonl_path.exists(), "data.jsonl 未生成"
        assert parquet_path.exists(), "data.parquet 未生成"

        clean_n = state["record_counts"]["clean"]
        lines = [l for l in jsonl_path.read_text(encoding="utf-8").splitlines() if l]
        assert len(lines) == clean_n

        import pyarrow.parquet as pq
        table = pq.read_table(parquet_path)
        assert table.num_rows == clean_n

        assert (task_dir / "manifest.json").exists()
        assert (task_dir / "schema.json").exists()
        assert result["manifest_path"]
    finally:
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
