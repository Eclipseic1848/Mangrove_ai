# -*- coding: utf-8 -*-
"""#15 AC07-10 阶段 4 真实装载：管理员任务冻结平台能力 -> 真实 Pi 任务调用工具。

复用阶段 2 驱动模式：创建任务 + 冻结 3.0.0 平台能力（digest b462e577…）→
本进程 SemanticWorkspaceManager 执行真实 Pi 任务 → 平台能力物化 + Sidecar 启动
+ 真实调用 capability_python_table_summary → 任务 completed → 工具调用机制门核验。

用法：
  python scripts/ac07_10_stage4_mount_drive.py
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agentic_runtime.models import PermissionProfile, RuntimeTaskConfig, RuntimeVersion
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.auth import get_store
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    CatalogActor,
    SqliteCapabilityCatalogRepository,
)
from src.config.settings import settings
from src.services.upload_store import UploadStore

OWNER_ID = "u_9505fd620899"  # liyi（super_admin）
PLATFORM_DIGEST_3 = "sha256:9379fe2908a4f8c1827fbe1db94d66892dc62190ec3da67129a64ae0ef0dbe03"
SAMPLE_CSV = "部门,金额\n研发,10\n市场,20\n研发,5\n"
POLL_SECONDS = 5
TIMEOUT_SECONDS = 900


def _upload_sample():
    store = UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )
    return store.save_bytes(
        OWNER_ID,
        "部门金额样例.csv",
        SAMPLE_CSV.encode("utf-8"),
        media_type="text/csv",
    )


def _create_task() -> str:
    store = get_store()
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    upload = _upload_sample()
    task_id = f"workspace_{uuid.uuid4().hex[:16]}"
    title = "AC07-10 阶段 4 管理员真实装载（平台 3.0.0）"
    objective = (
        "使用 python-table-summary 能力工具处理附件 CSV，"
        "按部门汇总金额，输出 JSON 结果。"
    )
    store.create_semantic_workspace_task(
        OWNER_ID,
        task_id=task_id,
        title=title,
        objective_text=objective,
        upload_ids=[upload.upload_id],
        output_formats=["json"],
        provider="local",
        model=None,
        external_api_confirmed=False,
        source_refs=[
            {"upload_id": upload.upload_id, "sha256": upload.sha256}
        ],
    )
    repository.register(
        RuntimeTaskConfig(
            user_id=OWNER_ID,
            task_id=task_id,
            revision=1,
            runtime_version=RuntimeVersion.PI,
            permission_profile=PermissionProfile.STANDARD,
            model_connection_id=None,
            model_connection_version=None,
            model_connection_model=None,
            external_api_confirmed=False,
        )
    )
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    catalog.freeze_selection(
        actor,
        task_id=task_id,
        revision=1,
        pack_refs=(CapabilityPackRef(
            pack_id="gray-python-table",
            version="3.0.0",
            digest=PLATFORM_DIGEST_3,
        ),),
    )
    return task_id


def _wait_task_completed(task_id: str) -> dict:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        task = get_store().get_semantic_workspace_task(OWNER_ID, task_id)
        assert task is not None, "任务不存在"
        status = task["status"]
        if status in ("completed", "failed", "cancelled"):
            return task
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"任务 {task_id} 未在 {TIMEOUT_SECONDS}s 内终态")


def _verify_tool_called(task_id: str, task: dict) -> None:
    """工具调用机制门：agentic_runtime_events 中该任务出现成功的工具调用。"""
    import sqlite3

    if task["status"] != "completed":
        raise RuntimeError(f"Pi 任务进入 {task['status']}：{task.get('failure')}")
    with sqlite3.connect(
        f"file:{settings.webui_db_path}?mode=ro", uri=True
    ) as con:
        rows = con.execute(
            "SELECT details_json FROM agentic_runtime_events "
            "WHERE user_id=? AND task_id=? AND revision=1 "
            "AND event_type='tool.completed'",
            (OWNER_ID, task_id),
        ).fetchall()
    success_tools = set()
    for row in rows:
        try:
            details = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        tool = str(details.get("tool") or "")
        if tool and not bool(details.get("failed")):
            success_tools.add(tool)
    print(f"  [info] 成功调用工具: {sorted(success_tools)}")
    expected = "capability_python_table_summary"
    if expected not in success_tools:
        raise RuntimeError(f"机制门未通过：{expected} 不在成功工具列表 {success_tools}")
    print("  [ok] 真实工具调用确认：capability_python_table_summary")


async def _drive() -> None:
    print("[1/4] 创建管理员任务并冻结 3.0.0 平台能力…")
    task_id = _create_task()
    print(f"  [ok] task={task_id}（冻结 gray-python-table@3.0.0 平台 digest）")

    print("[2/4] 执行真实 Pi 任务（平台能力物化 + Sidecar + 真实调用）…")
    manager = SemanticWorkspaceManager()
    manager.start()
    manager.enqueue(OWNER_ID, task_id)
    try:
        task = await asyncio.to_thread(_wait_task_completed, task_id)
        print(f"  [ok] 任务终态: {task['status']}")
    finally:
        await manager.stop()

    print("[3/4] 工具调用机制门核验…")
    _verify_tool_called(task_id, task)

    print("[4/4] 任务结果核验…")
    if task["status"] != "completed":
        raise RuntimeError(f"任务未完成：{task['status']}")
    print("  [ok] 阶段 4 管理员真实装载完成（平台 3.0.0 真实调用）")
    print("[done]")


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    asyncio.run(_drive())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
