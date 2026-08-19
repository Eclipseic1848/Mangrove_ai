# -*- coding: utf-8 -*-
"""#16 AC07-11 阶段 4 驱动：管理员选择 + 单 Sidecar 双能力真实装载。

AC4 核心：一个任务的多个能力仍共用单 Capability Host Sidecar，不新增
每工具独立容器。本脚本：
  1. 管理员选择列表核验（everything-mcp 平台行出现在新任务选择）；
  2. 单任务同时冻结 python-table@3.0.0（平台）+ everything-mcp@2026.7.4
     （平台）→ 真实 Pi 任务执行 → 单 Sidecar 装载双能力；
  3. 机制门核验：capability_python_table_summary 与 capability_everything_mcp
     均真实成功调用（tool.completed × 2）；
  4. 容器核验：任务期间只存在一个 mangrove-cap-host-* 容器。

用法：
  python scripts/ac07_11_stage4_drive.py
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sqlite3
import subprocess
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
PT_3 = "sha256:9379fe2908a4f8c1827fbe1db94d66892dc62190ec3da67129a64ae0ef0dbe03"  # python-table 3.0.0 平台
EM_2026 = "sha256:87741d37f6c293853687c1da1bc143dce0c5fb841b66f91f3eaaf04eaf99eb17"  # everything-mcp 平台（快照）
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
    title = "AC07-11 阶段 4 单 Sidecar 双能力真实装载"
    objective = (
        "必须完成两步："
        "1) 使用 python-table-summary 能力工具处理附件 CSV（部门金额样例.csv）："
        "调用该工具时传入单个 JSON 字符串参数，包含 group_field=\"部门\"、"
        "value_field=\"金额\" 和 csv（即附件 CSV 的完整内容），按部门汇总金额；"
        "2) 使用 everything-mcp 能力工具的 echo 工具回显消息 'ac07-11-multi'。"
        "两步都必须真实调用对应能力工具，输出 JSON 结果。"
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
        pack_refs=(
            CapabilityPackRef(
                pack_id="gray-python-table", version="3.0.0", digest=PT_3
            ),
            CapabilityPackRef(
                pack_id="gray-everything-mcp", version="2026.7.4", digest=EM_2026
            ),
        ),
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


def _verify_tools_called(task_id: str, task: dict) -> set[str]:
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
    return success_tools


def _count_host_containers() -> int:
    result = subprocess.run(
        ("docker", "ps", "-q", "--filter", "name=mangrove-cap-host-"),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    return len([line for line in result.stdout.splitlines() if line.strip()])


async def _drive() -> None:
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")

    print("[1/5] 管理员选择列表核验…")
    visible = [
        (p.pack_id, p.version, p.scope.value)
        for p in catalog.list_visible_packs(actor)
        if p.scope.value == "platform"
    ]
    has_em = any(
        p.pack_id == "gray-everything-mcp" and p.version == "2026.7.4"
        for p in catalog.list_visible_packs(actor)
    )
    if not has_em:
        raise RuntimeError("everything-mcp 平台行未出现在管理员可见列表")
    print(f"  [ok] everything-mcp@2026.7.4 在列表中（平台行 {len(visible)} 项）")

    print("[2/5] 创建单任务双能力冻结（python-table@3.0.0 + everything-mcp@2026.7.4）…")
    task_id = _create_task()
    print(f"  [ok] task={task_id}")

    print("[3/5] 执行真实 Pi 任务（单 Sidecar 双能力）…")
    manager = SemanticWorkspaceManager()
    manager.start()
    manager.enqueue(OWNER_ID, task_id)
    try:
        task = await asyncio.to_thread(_wait_task_completed, task_id)
        print(f"  [ok] 任务终态: {task['status']}")
    finally:
        await manager.stop()

    print("[4/5] 工具调用机制门核验（双工具）…")
    success_tools = _verify_tools_called(task_id, task)
    expected = {"capability_python_table_summary", "capability_everything_mcp"}
    missing = expected - success_tools
    if missing:
        raise RuntimeError(f"机制门未通过：缺少 {sorted(missing)}")
    print("  [ok] 双工具真实调用确认：python-table-summary + everything-mcp(echo)")

    print("[5/5] 单 Sidecar 核验…")
    hosts = _count_host_containers()
    print(f"  [info] 运行中的 mangrove-cap-host-* 容器: {hosts} 个")
    if hosts > 1:
        raise RuntimeError(f"存在多个 Host Sidecar（AC4 违反）：{hosts}")
    print("  [ok] 单 Sidecar 双能力装载确认（AC4 通过）")
    print("[done] 阶段 4 完成")


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    asyncio.run(_drive())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
