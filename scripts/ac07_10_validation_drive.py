# -*- coding: utf-8 -*-
"""#15 AC07-10 阶段 2 驱动：真实验证任务 -> 验证五步 -> 供应链证据 -> 晋级。

服务层直调（等价 API 校验后的写入路径）：创建验证任务（validation_target）→
本进程 SemanticWorkspaceManager 执行真实 Pi 任务 → request_validation_for_task
→ 验证 worker（外部进程启动，DB 轮询；8088 的 worker 也可能竞争执行，
Lease 保证同一 run 只被一个 worker 执行）→ 轮询五步成功 → 输出证据摘要。

用法：
  python scripts/ac07_10_validation_drive.py --version 2.0.0
  python scripts/ac07_10_validation_drive.py --version 3.0.0
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
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
from src.conversation_steering import (
    CapabilityMaturity,
    ProcedureScope,
)
from src.services.upload_store import UploadStore

OWNER_ID = "u_9505fd620899"  # liyi（super_admin）
# 3 行数据（2 部门）样例：降低真实 LLM 语义验证对行数核对的分心
# （4 行数据曾让 Qwen 误判 row_count 与源行数不一致）。
SAMPLE_CSV = "部门,金额\n研发,10\n市场,20\n研发,5\n"
POLL_SECONDS = 5
TIMEOUT_SECONDS = 900


def _upload_sample():
    # 必须写入生产上传根目录：Pi 运行时按 settings.data_prep_upload_root
    # 解析上传（与 create_task 同一路径）。
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


def _create_task(
    *,
    version: str,
    pack_ref: CapabilityPackRef,
) -> str:
    """等价 create_task 校验后的写入：任务 + revision + 冻结 selection。"""
    store = get_store()
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    upload = _upload_sample()
    task_id = f"workspace_{uuid.uuid4().hex[:16]}"
    title = f"AC07-10 验证任务 gray-python-table@{version}"
    # 明确指示使用能力工具：capability-host.json 现在会随物化正确生成，
    # agent 工具列表含 python-table-summary；机制门（_expected_target_tools/
    # _successful_tools_for_run）再验证真实调用发生。
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
            print(f"[ok] 验证运行 {run_id} 五步全部通过")
            for item in run.evidence:
                print(f"  - {item.step.value}: {item.status.value} {item.summary}")
            return
        if run.status in {
            ValidationRunStatus.FAILED,
            ValidationRunStatus.CANCELLED,
        }:
            raise RuntimeError(
                f"验证运行 {run_id} 进入 {run.status.value}"
            )
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"验证运行 {run_id} 未在 {TIMEOUT_SECONDS}s 内终态")


async def _drive(version: str) -> None:
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    governance = CapabilityGovernance(
        catalog,
        SqliteCapabilityGovernanceRepository(settings.webui_db_path),
        task_resolver=SqliteValidationTaskResolver(settings.webui_db_path),
    )
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    pack = catalog.resolve_pack(
        actor, "gray-python-table", version
    )
    if pack is None or pack.scope is not ProcedureScope.PERSONAL:
        raise RuntimeError(f"{version} 不是个人能力包")
    pack_ref = CapabilityPackRef(
        pack_id=pack.pack_id, version=pack.version, digest=pack.digest
    )
    print(f"[1/4] 创建验证任务（validation_target={version}，digest={pack.digest[:18]}…）")
    task_id = _create_task(version=version, pack_ref=pack_ref)
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
        print("[ok] Pi 任务 completed")
    finally:
        await manager.stop()
    print("[3/4] 发起验证五步…")
    run = governance.request_validation_for_task(
        actor,
        pack_ref=pack_ref,
        task_id=task_id,
        revision=1,
        idempotency_key=f"ac07-10-validation:{version}:{task_id}",
    )
    print(f"[4/4] 等待验证 worker（run={run.run_id}，8088 或本进程 worker 执行）…")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, choices=("2.0.0", "3.0.0"))
    args = parser.parse_args()
    asyncio.run(_drive(args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
