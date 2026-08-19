# -*- coding: utf-8 -*-
"""#16 AC07-11 阶段 2 驱动：真实验证任务 -> 三类验证 -> 供应链证据 -> 晋级。

服务层直调（等价 API 校验后的写入路径）：创建验证任务（validation_target）→
本进程 SemanticWorkspaceManager 执行真实 Pi 任务（objective 指向 everything-mcp
echo 工具，真实 MCP 调用由机制门 tool.completed 证明）→ request_validation_for_task
→ 验证 worker（外部进程启动，DB 轮询）→ 轮询成功 → 供应链采集 → 晋级核验。

三类验证映射（AC2）：
  ① 合成 Smoke —— Smoke 步骤真实 MCP echo 调用（Sidecar 内，validation_runtime 增量）；
  ② 真实任务重放 —— 真实 Pi 任务真实调用 MCP 工具（owner_task_replay）；
  ③ 协议生命周期验证 —— 握手/健康/调用在 Smoke 与重放中隐式覆盖；超时/取消/
     进程异常等纵深放阶段 5 演示。

用法：
  python scripts/ac07_11_validation_drive.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
import time
import uuid

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
from src.capability_governance import (
    CapabilityGovernance,
    SqliteCapabilityGovernanceRepository,
    SqliteValidationTaskResolver,
    ValidationRunStatus,
)
from src.config.settings import settings
from src.conversation_steering import ProcedureScope
from src.services.upload_store import UploadStore

OWNER_ID = "u_9505fd620899"  # liyi（super_admin）
POLL_SECONDS = 5
TIMEOUT_SECONDS = 900
# Pi 运行时要求任务至少 1 个 source（PiRuntimeRequest.sources 非空）。
# 样例文件仅作为输入存在；objective 指向 everything-mcp echo 工具。
SAMPLE_CSV = "部门,金额\n研发,10\n市场,20\n"


def _upload_sample():
    store = UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )
    return store.save_bytes(
        OWNER_ID,
        "echo样例.csv",
        SAMPLE_CSV.encode("utf-8"),
        media_type="text/csv",
    )


def _create_task(pack_ref: CapabilityPackRef) -> str:
    """等价 create_task 校验后的写入：任务 + revision + 冻结 selection。

    everything-mcp 是 MCP 协议测试服务器（echo 工具）：objective 指向真实
    MCP 工具调用（工具调用发生由机制门证明）。
    """
    store = get_store()
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    upload = _upload_sample()
    task_id = f"workspace_{uuid.uuid4().hex[:16]}"
    title = f"AC07-11 验证任务 gray-everything-mcp@{pack_ref.version}"
    objective = (
        "使用 everything-mcp 能力工具的 echo 工具回显消息 "
        "'ac07-11-validation'，并返回工具输出。"
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
        pack_refs=(pack_ref,),
        validation_target=pack_ref,
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


def _wait_validation_run(governance: CapabilityGovernance, run_id: str) -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    while time.monotonic() < deadline:
        run = governance.get_validation(actor, run_id)
        if run.status is ValidationRunStatus.SUCCEEDED:
            print(f"[ok] 验证运行 {run_id} 全部通过")
            for item in run.evidence:
                print(f"  - {item.step.value}: {item.status.value} {item.summary}")
            return
        if run.status in {
            ValidationRunStatus.FAILED,
            ValidationRunStatus.CANCELLED,
        }:
            raise RuntimeError(f"验证运行 {run_id} 进入 {run.status.value}")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"验证运行 {run_id} 未在 {TIMEOUT_SECONDS}s 内终态")


async def _drive() -> None:
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    governance = CapabilityGovernance(
        catalog,
        SqliteCapabilityGovernanceRepository(settings.webui_db_path),
        task_resolver=SqliteValidationTaskResolver(settings.webui_db_path),
    )
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    # 同归档同 digest 的平台 legacy 行与个人行并存（#16 缺陷 #1）：resolve_pack
    # 按可见性顺序命中平台行。验证链显式按 Owner 解析个人行（内容相同，
    # 治理语义必须以个人行为准；发布后平台行换新 digest，歧义自然消失）。
    pack = next(
        (
            item
            for item in catalog.list_visible_packs(actor)
            if item.pack_id == "gray-everything-mcp"
            and item.version == "2026.7.4"
            and item.scope is ProcedureScope.PERSONAL
            and item.owner_id == OWNER_ID
        ),
        None,
    )
    if pack is None:
        raise RuntimeError("2026.7.4 个人能力包不可解析")
    pack_ref = CapabilityPackRef(
        pack_id=pack.pack_id, version=pack.version, digest=pack.digest
    )
    print(f"[1/4] 创建验证任务（validation_target=2026.7.4，digest={pack.digest[:18]}…）")
    task_id = _create_task(pack_ref)
    print(f"[2/4] 执行真实 Pi 任务 {task_id}…")
    manager = SemanticWorkspaceManager()
    manager.start()
    manager.enqueue(OWNER_ID, task_id)
    try:
        task = await asyncio.to_thread(_wait_task_completed, task_id)
        if task["status"] != "completed":
            raise RuntimeError(
                f"Pi 任务进入 {task['status']}：{task.get('failure')}"
            )
        print("[ok] Pi 任务 completed（真实 MCP 工具调用由机制门证明）")
    finally:
        await manager.stop()
    print("[3/4] 发起验证…")
    run = governance.request_validation_for_task(
        actor,
        pack_ref=pack_ref,
        task_id=task_id,
        revision=1,
        idempotency_key=f"ac07-11-validation:{task_id}",
    )
    print(f"[4/4] 等待验证 worker（run={run.run_id}）…")
    from src.api.capability_governance_runtime import (
        get_capability_validation_manager,
    )

    validation_manager = get_capability_validation_manager()
    validation_manager.start()
    try:
        await asyncio.to_thread(_wait_validation_run, governance, run.run_id)
    finally:
        await validation_manager.stop()
    projection = governance.runtime_projection_for_pack(pack)
    print(f"[done] 投影：{projection.maturity.value} / "
          f"{projection.lifecycle.value} / {projection.eligibility.value}")


def main() -> int:
    import io as _io

    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    asyncio.run(_drive())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
