# -*- coding: utf-8 -*-
"""AC-07-06 S4：任务管理元数据与业务正文读取（SqliteValidationTaskResolver 扩展）。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from src.capability_catalog import CatalogActor
from src.capability_governance import BusinessContent, SqliteValidationTaskResolver
from src.services.upload_store import UploadItem

# 与实现同值的正文读取上限（设计 D2）；测试用 ASCII 保证字节/字符一致。
_MAX_BUSINESS_CONTENT_BYTES = 2 * 1024 * 1024


def _user_objects(upload_root: Path, user_id: str) -> Path:
    safe = "".join(character for character in user_id if character.isalnum() or character in "-_")
    return upload_root / safe / "objects"


def _write_upload(
    upload_root: Path,
    *,
    user_id: str,
    upload_id: str,
    content: bytes,
    original_name: str,
) -> Path:
    objects_dir = _user_objects(upload_root, user_id)
    objects_dir.mkdir(parents=True, exist_ok=True)
    object_path = objects_dir / upload_id
    object_path.write_bytes(content)
    item = UploadItem(
        upload_id=upload_id,
        user_id=user_id,
        original_name=original_name,
        storage_path=str(object_path.resolve()),
        media_type="text/csv",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    (objects_dir / f"{upload_id}.meta").write_text(
        item.model_dump_json(), encoding="utf-8"
    )
    return object_path


def _make_db(db_path: Path) -> None:
    """建主工作台最小表：任务、冻结 revision、正式输出。"""
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE semantic_workspace_tasks (
                task_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                objective_text TEXT NOT NULL,
                upload_ids_json TEXT NOT NULL DEFAULT '[]',
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                output_formats_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE semantic_workspace_revisions (
                task_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                objective_text TEXT NOT NULL,
                output_formats_json TEXT NOT NULL DEFAULT '[]',
                run_id TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (task_id, revision)
            );
            CREATE TABLE formal_delivery_outputs (
                output_id TEXT PRIMARY KEY,
                delivery_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                format TEXT NOT NULL,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                qa_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def _seed_task(
    db_path: Path,
    *,
    user_id: str = "owner-a",
    task_id: str = "workspace-validated-source",
    revision: int = 2,
    objective_text: str = "汇总本季度销售数据，输出 CSV 表格。",
    upload_ids: list[str] | None = None,
    output_formats: list[str] | None = None,
    run_id: str = "run-1",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO semantic_workspace_tasks "
            "(task_id, user_id, title, objective_text, upload_ids_json, "
            "source_refs_json, output_formats_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                user_id,
                "季度销售汇总",
                objective_text,
                json.dumps(upload_ids or []),
                "[]",
                json.dumps(output_formats or []),
                "completed",
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO semantic_workspace_revisions "
            "(task_id, revision, user_id, objective_text, output_formats_json, "
            "run_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                revision,
                user_id,
                objective_text,
                json.dumps(output_formats or []),
                run_id,
                "completed",
                now,
                now,
            ),
        )


def _seed_output(
    db_path: Path,
    *,
    output_id: str,
    run_id: str,
    owner_id: str,
    file_path: Path,
    content: bytes,
    fmt: str = "csv",
) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO formal_delivery_outputs "
            "(output_id, delivery_id, run_id, owner_id, format, filename, "
            "media_type, sha256, size_bytes, file_path, qa_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                output_id,
                "delivery-1",
                run_id,
                owner_id,
                fmt,
                f"{output_id}.{fmt}",
                "text/csv",
                hashlib.sha256(content).hexdigest(),
                len(content),
                str(file_path.resolve()),
                "{}",
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def _resolver(
    db_path: Path,
    upload_root: Path,
    execution_root: Path,
) -> SqliteValidationTaskResolver:
    return SqliteValidationTaskResolver(
        db_path,
        upload_root=upload_root,
        execution_root=execution_root,
    )


def _admin() -> CatalogActor:
    return CatalogActor(owner_id="admin-x", role="admin")


def _fixture(tmp_path: Path) -> tuple[SqliteValidationTaskResolver, Path, Path]:
    db_path = tmp_path / "webui.db"
    upload_root = tmp_path / "uploads"
    execution_root = tmp_path / "execution"
    _make_db(db_path)
    return _resolver(db_path, upload_root, execution_root), upload_root, execution_root


class TestS4TaskMetadata:
    """管理元数据读取：脱敏计数与类型，不读正文。"""

    def test_reads_input_output_counts_and_types(self, tmp_path) -> None:
        resolver, upload_root, _ = _fixture(tmp_path)
        source = _write_upload(
            upload_root, user_id="owner-a", upload_id="up1",
            content="a,b\n1,2\n".encode("utf-8"), original_name="source.csv",
        )
        _seed_task(
            tmp_path / "webui.db",
            upload_ids=["up1"],
            output_formats=["csv", "json"],
        )
        output_content = "col,sum\nsales,123\n".encode("utf-8")
        output_path = tmp_path / "execution" / "deliveries" / "out1.csv"
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(output_content)
        _seed_output(
            tmp_path / "webui.db",
            output_id="out1", run_id="run-1", owner_id="owner-a",
            file_path=output_path, content=output_content,
        )
        metadata = resolver.read_task_metadata(
            _admin(), "workspace-validated-source", 2, task_owner_id="owner-a"
        )
        assert metadata.task_status == "completed"
        assert metadata.input_count == 1
        assert metadata.input_types == ("csv",)
        assert metadata.output_count == 1
        assert metadata.output_formats == ("csv", "json")
        assert source.exists()

    def test_rejects_cross_owner_read(self, tmp_path) -> None:
        resolver, _, _ = _fixture(tmp_path)
        _seed_task(tmp_path / "webui.db")
        with pytest.raises(PermissionError):
            resolver.read_task_metadata(
                CatalogActor(owner_id="owner-b", role="user"),
                "workspace-validated-source", 2, task_owner_id="owner-a",
            )

    def test_missing_task_raises_keyerror(self, tmp_path) -> None:
        resolver, _, _ = _fixture(tmp_path)
        with pytest.raises(KeyError):
            resolver.read_task_metadata(
                _admin(), "no-such-task", 2, task_owner_id="owner-a"
            )

    def test_metadata_forbids_objective_text(self, tmp_path) -> None:
        resolver, _, _ = _fixture(tmp_path)
        _seed_task(tmp_path / "webui.db")
        metadata = resolver.read_task_metadata(
            _admin(), "workspace-validated-source", 2, task_owner_id="owner-a"
        )
        assert metadata.task_status == "completed"
        # CapabilityTaskMetadata 模型没有 objective_text 字段，构造即失败。
        assert "objective_text" not in metadata.model_fields


class TestS4BusinessContent:
    """审计查看正文读取：成功、失败类型化、截断、跨 Owner 拒绝。"""

    def test_reads_task_prompt(self, tmp_path) -> None:
        resolver, _, _ = _fixture(tmp_path)
        _seed_task(
            tmp_path / "webui.db",
            objective_text="汇总本季度销售数据，输出 CSV 表格。",
        )
        content = resolver.read_business_content(
            _admin(), "workspace-validated-source", 2,
            "task_prompt", task_owner_id="owner-a",
        )
        assert content.status == "succeeded"
        assert content.subject_type == "task_prompt"
        assert "季度销售数据" in content.content
        assert content.content_sha256 == hashlib.sha256(
            content.content.encode("utf-8")
        ).hexdigest()
        assert content.truncated is False

    def test_reads_task_sources(self, tmp_path) -> None:
        resolver, upload_root, _ = _fixture(tmp_path)
        _write_upload(
            upload_root, user_id="owner-a", upload_id="up1",
            content="a,b\n1,2\n".encode("utf-8"), original_name="source.csv",
        )
        _seed_task(tmp_path / "webui.db", upload_ids=["up1"])
        content = resolver.read_business_content(
            _admin(), "workspace-validated-source", 2,
            "task_sources", task_owner_id="owner-a",
        )
        assert content.status == "succeeded"
        assert "a,b" in content.content
        assert content.content_sha256 == hashlib.sha256(
            content.content.encode("utf-8")
        ).hexdigest()

    def test_reads_task_output(self, tmp_path) -> None:
        resolver, _, _ = _fixture(tmp_path)
        _seed_task(tmp_path / "webui.db")
        output_content = "col,sum\nsales,123\n".encode("utf-8")
        output_path = tmp_path / "execution" / "deliveries" / "out1.csv"
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(output_content)
        _seed_output(
            tmp_path / "webui.db",
            output_id="out1", run_id="run-1", owner_id="owner-a",
            file_path=output_path, content=output_content,
        )
        content = resolver.read_business_content(
            _admin(), "workspace-validated-source", 2,
            "task_output", task_owner_id="owner-a",
        )
        assert content.status == "succeeded"
        assert "sales" in content.content

    def test_missing_prompt_task_fails_typed(self, tmp_path) -> None:
        resolver, _, _ = _fixture(tmp_path)
        content = resolver.read_business_content(
            _admin(), "no-such-task", 2, "task_prompt", task_owner_id="owner-a"
        )
        assert content.status == "failed"
        assert content.failure_reason is not None

    def test_symlinked_source_fails_typed(self, tmp_path) -> None:
        resolver, upload_root, _ = _fixture(tmp_path)
        objects_dir = _user_objects(upload_root, "owner-a")
        objects_dir.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside.txt"
        outside.write_text("逃逸内容", encoding="utf-8")
        link = objects_dir / "up1"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("当前环境不允许创建符号链接")
        _seed_task(tmp_path / "webui.db", upload_ids=["up1"])
        content = resolver.read_business_content(
            _admin(), "workspace-validated-source", 2,
            "task_sources", task_owner_id="owner-a",
        )
        assert content.status == "failed"
        assert content.failure_reason == "source_invalid"

    def test_missing_output_fails_typed(self, tmp_path) -> None:
        resolver, _, execution_root = _fixture(tmp_path)
        _seed_task(tmp_path / "webui.db")
        output_path = execution_root / "deliveries" / "missing.csv"
        _seed_output(
            tmp_path / "webui.db",
            output_id="out1", run_id="run-1", owner_id="owner-a",
            file_path=output_path, content=b"",
        )
        content = resolver.read_business_content(
            _admin(), "workspace-validated-source", 2,
            "task_output", task_owner_id="owner-a",
        )
        assert content.status == "failed"
        assert content.failure_reason == "output_invalid"

    def test_large_content_is_truncated(self, tmp_path) -> None:
        resolver, upload_root, _ = _fixture(tmp_path)
        big = b"x" * (_MAX_BUSINESS_CONTENT_BYTES + 1000)
        _write_upload(
            upload_root, user_id="owner-a", upload_id="up1",
            content=big, original_name="big.csv",
        )
        _seed_task(tmp_path / "webui.db", upload_ids=["up1"])
        content = resolver.read_business_content(
            _admin(), "workspace-validated-source", 2,
            "task_sources", task_owner_id="owner-a",
        )
        assert content.status == "succeeded"
        assert content.truncated is True
        assert len(content.content.encode("utf-8")) == _MAX_BUSINESS_CONTENT_BYTES

    def test_rejects_cross_owner_content_read(self, tmp_path) -> None:
        resolver, _, _ = _fixture(tmp_path)
        _seed_task(tmp_path / "webui.db")
        with pytest.raises(PermissionError):
            resolver.read_business_content(
                CatalogActor(owner_id="owner-b", role="user"),
                "workspace-validated-source", 2,
                "task_prompt", task_owner_id="owner-a",
            )
