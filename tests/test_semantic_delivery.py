# -*- coding: utf-8 -*-
"""Phase 4B 批次 6：11 格式交付、独立 QA 与发布失败闸门。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.semantic_harness.delivery import create_delivery
from src.semantic_harness.delivery import service as delivery_service
from src.semantic_harness.models import DeliveryFormat, DeliverySpec
from tests.test_semantic_table_execution import _gate_plan


FORMATS = (
    DeliveryFormat.JSON,
    DeliveryFormat.JSONL,
    DeliveryFormat.CSV,
    DeliveryFormat.XLSX,
    DeliveryFormat.PARQUET,
    DeliveryFormat.DOCX,
    DeliveryFormat.PDF,
    DeliveryFormat.HTML,
    DeliveryFormat.MARKDOWN,
    DeliveryFormat.TXT,
    DeliveryFormat.PPTX,
)


def test_document_passages_with_same_target_use_one_heading():
    sections = delivery_service._passage_sections(
        (
            SimpleNamespace(label="商务条款", text="费用由投标方承担。"),
            SimpleNamespace(label="商务条款", text="逾期交付承担违约金。"),
            SimpleNamespace(label="保密条款", text="不得对外泄露。"),
        )
    )

    assert sections == (
        ("商务条款", "费用由投标方承担。\n\n逾期交付承担违约金。"),
        ("保密条款", "不得对外泄露。"),
    )


class _DeliveryStore:
    def __init__(self) -> None:
        self.saved = None

    def save_semantic_delivery(self, **payload):
        self.saved = payload
        return payload["manifest"].model_dump(mode="json")


class _IdempotentDeliveryStore(_DeliveryStore):
    def __init__(self) -> None:
        super().__init__()
        self.save_count = 0

    def save_semantic_delivery(self, **payload):
        self.save_count += 1
        return super().save_semantic_delivery(**payload)

    def latest_semantic_delivery(self, user_id, run_id):
        if self.saved is None:
            return None
        if (
            self.saved["user_id"] != user_id
            or self.saved["run_id"] != run_id
        ):
            return None
        return self.saved["manifest"].model_dump(mode="json")


def _result(path: Path) -> Path:
    result = path / "result.parquet"
    pq.write_table(
        pa.table(
            {
                "姓名": ["谢超群", "谢超群"],
                "核销工作量天数": [0.5, 1.0],
                "工作量费用": [100.0, 200.0],
            }
        ),
        result,
    )
    return result


def test_all_formal_formats_are_rendered_reopened_and_published(tmp_path):
    plan = _gate_plan(("artifact-a",)).model_copy(
        update={
            "delivery": DeliverySpec(
                formats=FORMATS,
                output_name="批次6交付",
            )
        }
    )
    store = _DeliveryStore()

    manifest = create_delivery(
        store=store,
        output_root=tmp_path / "executions",
        user_id="user-a",
        run_id="run-a",
        plan=plan,
        artifact_paths={"result": _result(tmp_path)},
    )

    assert manifest.status.value == "succeeded"
    assert {item.format for item in manifest.outputs} == set(FORMATS)
    assert all(item.qa.openable for item in manifest.outputs)
    assert all(item.qa.sha256 == item.sha256 for item in manifest.outputs)
    assert store.saved is not None
    published = store.saved["output_dir"]
    assert published.is_dir()
    assert (published / "manifest.json").is_file()
    manifest_text = (published / "manifest.json").read_text(encoding="utf-8")
    assert "user-a" not in manifest_text
    assert str(tmp_path) not in manifest_text
    assert not any(
        item.name.endswith(".staging")
        for item in published.parent.iterdir()
    )


def test_unsupported_tsv_never_becomes_formal_download(tmp_path):
    plan = _gate_plan(("artifact-a",)).model_copy(
        update={
            "delivery": DeliverySpec(
                formats=(DeliveryFormat.TSV,),
                output_name="禁止交付",
            )
        }
    )

    with pytest.raises(ValueError, match="不支持"):
        create_delivery(
            store=_DeliveryStore(),
            output_root=tmp_path / "executions",
            user_id="user-a",
            run_id="run-a",
            plan=plan,
            artifact_paths={"result": _result(tmp_path)},
        )

    assert not (tmp_path / "executions").exists()


def test_renderer_failure_removes_staging_and_never_registers(
    tmp_path,
    monkeypatch,
):
    plan = _gate_plan(("artifact-a",)).model_copy(
        update={
            "delivery": DeliverySpec(
                formats=(DeliveryFormat.JSON,),
                output_name="失败交付",
            )
        }
    )
    store = _DeliveryStore()

    def fail_renderer(path, content):
        raise RuntimeError("模拟 Renderer 失败")

    monkeypatch.setitem(
        delivery_service._RENDERERS,
        DeliveryFormat.JSON,
        fail_renderer,
    )
    with pytest.raises(RuntimeError, match="模拟"):
        create_delivery(
            store=store,
            output_root=tmp_path / "executions",
            user_id="user-a",
            run_id="run-a",
            plan=plan,
            artifact_paths={"result": _result(tmp_path)},
        )

    assert store.saved is None
    assert not any(
        path.is_file()
        for path in (tmp_path / "executions").rglob("*")
    )


def test_repeated_delivery_request_reuses_published_manifest(tmp_path):
    plan = _gate_plan(("artifact-a",)).model_copy(
        update={
            "delivery": DeliverySpec(
                formats=(DeliveryFormat.JSON,),
                output_name="幂等交付",
            )
        }
    )
    store = _IdempotentDeliveryStore()
    result = _result(tmp_path)

    first = create_delivery(
        store=store,
        output_root=tmp_path / "executions",
        user_id="user-a",
        run_id="run-a",
        plan=plan,
        artifact_paths={"result": result},
    )
    second = create_delivery(
        store=store,
        output_root=tmp_path / "executions",
        user_id="user-a",
        run_id="run-a",
        plan=plan,
        artifact_paths={"result": result},
    )

    assert second.delivery_id == first.delivery_id
    assert store.save_count == 1
    assert len(
        list(
            (
                tmp_path
                / "executions"
                / hashlib.sha256(b"user-a").hexdigest()[:16]
                / plan.plan_id
                / "run-a"
                / "delivery"
            ).glob("delivery_*")
        )
    ) == 1
