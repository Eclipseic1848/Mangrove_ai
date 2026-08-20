# -*- coding: utf-8 -*-
"""#16 AC07-11 阶段 5 并行版本治理链：everything-mcp@2026.8.19（牺牲版本）。

AC5「并行版本」增量：
  1. 构造 2026.8.19 归档：从 2026.7.4 平台物化复制 + manifest version 改
     2026.8.19 + 确定性重打包 → OCI push（新 digest，与 2026.7.4 并行）；
  2. 登记个人 draft → 真实验证（Pi 任务 + 五步）→ 供应链 → verified；
  3. 平台发布（候选 → 六步 → 签名 → admin_gray）；
  4. 治理动作链（牺牲版本，2026.7.4 主版本不受影响）：deprecate → revoke；
  5. 核验：2026.8.19 投影 verified/revoked/eligible，2026.7.4 保持 active。

用法：
  python scripts/ac07_11_stage5_parallel.py
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import tarfile
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api.capability_governance_runtime import (
    get_platform_validation_manager,
)
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.agentic_runtime.models import PermissionProfile, RuntimeTaskConfig, RuntimeVersion
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.auth import get_store
from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    CatalogActor,
    OrasOciLayoutStore,
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
    CapabilityPack,
    ProcedureScope,
)
from src.services.upload_store import UploadStore

OWNER_ID = "u_9505fd620899"
NEW_VERSION = "2026.8.19"
BASE_DIGEST = "sha256:87741d37f6c293853687c1da1bc143dce0c5fb841b66f91f3eaaf04eaf99eb17"  # 2026.7.4 平台
POLL_SECONDS = 5
TIMEOUT_SECONDS = 900
_FIXED_MTIME = 946684800
_ARTIFACT_TYPE = "application/vnd.mangrove.capability.pack.v1"
_LAYER_MEDIA_TYPE = "application/octet-stream"


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d%H%M%S%f"
    )


def _backup_db() -> Path:
    backup = (
        PROJECT_ROOT / "data/backups"
        / f"webui-before-ac07-11-stage5b-{_stamp()}.db"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.webui_db_path, timeout=30) as source:
        with sqlite3.connect(backup, timeout=30) as destination:
            source.backup(destination)
    print(f"[backup] {backup}")
    return backup


def _materialize_base(work: Path) -> Path:
    from src.capability_catalog.oci_store import OrasOciLayoutStore

    store = OrasOciLayoutStore(
        settings.capability_platform_oci_layout_path,
        layout_id="mangrove-platform",
    )
    dest = work / "base"
    store.materialize(
        artifact_name="gray-everything-mcp",
        version="2026.7.4",
        digest=BASE_DIGEST,
        destination=dest,
    )
    return dest


def _build_new_version(work: Path) -> tuple[Path, str]:
    """复制基线 + manifest version 改 2026.8.19 + 确定性重打包 → 新 digest。"""
    base = _materialize_base(work)
    manifest_path = base / "mangrove-capability.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = NEW_VERSION
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    archive = work / "mangrove-capability.tar"
    members = sorted(
        path
        for path in base.rglob("*")
        if path.is_file() and path.name != "mangrove-capability.tar"
    )
    with tarfile.open(archive, "w") as bundle:
        for path in members:
            relative = path.relative_to(base).as_posix()
            info = tarfile.TarInfo(relative)
            info.size = path.stat().st_size
            info.mtime = _FIXED_MTIME
            info.mode = 0o644
            bundle.addfile(info, path.open("rb"))
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    return archive, digest


def _register_new_version(archive: Path) -> dict:
    """OCI push + 目录登记个人 draft + registered 事件（幂等）。"""
    store = OrasOciLayoutStore(
        settings.capability_oci_layout_path,
        layout_id="mangrove-capabilities",
    )
    descriptor = store.push_file(
        archive,
        artifact_name="gray-everything-mcp",
        version=NEW_VERSION,
        artifact_type=_ARTIFACT_TYPE,
        layer_media_type=_LAYER_MEDIA_TYPE,
    )
    with sqlite3.connect(f"file:{settings.webui_db_path}?mode=ro", uri=True) as con:
        existing = con.execute(
            "SELECT digest FROM capability_pack_versions "
            "WHERE owner_key=? AND pack_id='gray-everything-mcp' "
            "AND version=? AND scope='personal'",
            (OWNER_ID, NEW_VERSION),
        ).fetchone()
    if existing is not None:
        print(f"  [skip] 个人行已存在（digest={existing[0][:24]}…）")
        return {"digest": existing[0]}
    actor = CatalogActor(owner_id=OWNER_ID, role="user")
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    catalog.register_pack(
        actor,
        CapabilityPack(
            pack_id="gray-everything-mcp",
            version=NEW_VERSION,
            digest=descriptor.digest,
            scope=ProcedureScope.PERSONAL,
            maturity=CapabilityMaturity.DRAFT,
            owner_id=OWNER_ID,
            manifest=(
                ("display_name", "Everything MCP（并行版本 2026.8.19）"),
                ("kind", "mcp_local"),
                ("purpose", "验证并调用本地 MCP 标准工具协议"),
            ),
            source_provenance=(
                "npm:@modelcontextprotocol/server-everything@2026.7.4",
                descriptor.reference,
            ),
            created_by="ac07-11-stage5-parallel",
        ),
    )
    governance = CapabilityGovernance(
        catalog,
        SqliteCapabilityGovernanceRepository(settings.webui_db_path),
    )
    governance.register_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id="gray-everything-mcp",
            version=NEW_VERSION,
            digest=descriptor.digest,
        ),
        idempotency_key=f"ac07-11-register:{NEW_VERSION}",
    )
    print(f"  [ok] 个人 draft 登记（digest={descriptor.digest[:24]}…）")
    return {"digest": descriptor.digest}


def _create_task(digest: str) -> str:
    store = get_store()
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    upload_store = UploadStore(
        root=settings.data_prep_upload_root,
        max_bytes=settings.data_prep_max_upload_bytes,
    )
    upload = upload_store.save_bytes(
        OWNER_ID,
        "echo样例.csv",
        "部门,金额\n研发,10\n市场,20\n".encode("utf-8"),
        media_type="text/csv",
    )
    task_id = f"workspace_{uuid.uuid4().hex[:16]}"
    objective = (
        "使用 everything-mcp 能力工具的 echo 工具回显消息 "
        "'ac07-11-parallel'，并返回工具输出。"
    )
    store.create_semantic_workspace_task(
        OWNER_ID,
        task_id=task_id,
        title=f"AC07-11 并行版本验证 {NEW_VERSION}",
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
            pack_id="gray-everything-mcp",
            version=NEW_VERSION,
            digest=digest,
        ),),
        validation_target=CapabilityPackRef(
            pack_id="gray-everything-mcp",
            version=NEW_VERSION,
            digest=digest,
        ),
    )
    return task_id


def _wait_task(task_id: str) -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        task = get_store().get_semantic_workspace_task(OWNER_ID, task_id)
        assert task is not None
        if task["status"] in ("completed", "failed", "cancelled"):
            if task["status"] != "completed":
                raise RuntimeError(f"Pi 任务 {task['status']}：{task.get('failure')}")
            return
        time.sleep(POLL_SECONDS)
    raise TimeoutError("Pi 任务超时")


def _wait_validation(governance: CapabilityGovernance, run_id: str) -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        run = governance.get_validation(actor, run_id)
        if run.status is ValidationRunStatus.SUCCEEDED:
            print("  [ok] 验证五步全部通过")
            return
        if run.status in {
            ValidationRunStatus.FAILED,
            ValidationRunStatus.CANCELLED,
        }:
            raise RuntimeError(f"验证运行进入 {run.status.value}")
        time.sleep(POLL_SECONDS)
    raise TimeoutError("验证运行超时")


def _wait_platform_signed(
    governance: CapabilityGovernance,
    platform_digest: str,
) -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    deadline = time.monotonic() + 2400
    while time.monotonic() < deadline:
        items = governance.list_platform_candidates(actor)
        item = next(
            (
                i
                for i in items
                if i.version == NEW_VERSION
                and i.platform_digest == platform_digest
            ),
            None,
        )
        if (
            item is not None
            and item.validation_status == "succeeded"
            and item.steps_passed == item.steps_total
            and item.signed
        ):
            print("  [ok] 六步验证全绿且签名齐备")
            return
        time.sleep(POLL_SECONDS)
    raise TimeoutError("平台验证超时")


def _governance() -> CapabilityGovernance:
    from src.api.capability_governance_runtime import (
        get_platform_publication_dependencies,
        get_rescan_dependencies,
    )

    generator, publisher = get_platform_publication_dependencies()
    materialize, collector = get_rescan_dependencies()
    return CapabilityGovernance(
        CapabilityCatalog(
            SqliteCapabilityCatalogRepository(settings.webui_db_path)
        ),
        SqliteCapabilityGovernanceRepository(settings.webui_db_path),
        task_resolver=SqliteValidationTaskResolver(settings.webui_db_path),
        platform_snapshot_generator=generator,
        platform_publisher=publisher,
        platform_materialize=materialize,
        supply_chain_collector=collector,
    )


async def _drive() -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    governance = _governance()
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    print("[1/7] 在线一致性备份…")
    _backup_db()

    work = PROJECT_ROOT / "data/capability-governance/evidence/ac07-11-stage5/parallel"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    print("[2/7] 构造 2026.8.19 归档（基线 2026.7.4 + version 字段）…")
    archive, archive_digest = _build_new_version(work)
    print(f"  [ok] 归档 {archive.name}（tar digest={archive_digest[:24]}…）")
    registered = _register_new_version(archive)
    personal_digest = registered["digest"]

    print("[3/7] 真实验证（Pi 任务 + 五步）…")
    task_id = _create_task(personal_digest)
    manager = SemanticWorkspaceManager()
    manager.start()
    manager.enqueue(OWNER_ID, task_id)
    try:
        await asyncio.to_thread(_wait_task, task_id)
    finally:
        await manager.stop()
    print("  [ok] Pi 任务 completed（真实 MCP echo 调用）")
    run = governance.request_validation_for_task(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id="gray-everything-mcp",
            version=NEW_VERSION,
            digest=personal_digest,
        ),
        task_id=task_id,
        revision=1,
        idempotency_key=f"ac07-11-validation:{NEW_VERSION}:{task_id}",
    )
    from src.api.capability_governance_runtime import (
        get_capability_validation_manager,
    )

    validation_manager = get_capability_validation_manager()
    validation_manager.start()
    try:
        await asyncio.to_thread(_wait_validation, governance, run.run_id)
    finally:
        await validation_manager.stop()
    projection = governance.runtime_projection_for_pack(
        next(
            p
            for p in catalog.list_visible_packs(actor)
            if p.pack_id == "gray-everything-mcp"
            and p.version == NEW_VERSION
            and p.scope is ProcedureScope.PERSONAL
        )
    )
    print(f"  [ok] 晋级投影：{projection.maturity.value}/{projection.lifecycle.value}")

    print("[4/7] 平台发布（候选 → 六步 → 签名 → admin_gray）…")
    outcome = governance.submit_platform_candidate(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id="gray-everything-mcp",
            version=NEW_VERSION,
            digest=personal_digest,
        ),
        reason=f"AC07-11 阶段 5 并行版本发布 {NEW_VERSION}",
        idempotency_key=f"ac07-11-candidate:{NEW_VERSION}",
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"候选被拒绝：{outcome.gaps}")
    platform_digest = outcome.snapshot.platform_digest
    print(f"  [ok] 候选 {outcome.status}；平台 digest={platform_digest[:24]}…")
    publish_manager = get_platform_validation_manager()
    publish_manager.start()
    try:
        await asyncio.to_thread(
            _wait_platform_signed, governance, platform_digest
        )
    finally:
        await publish_manager.stop()
    published = governance.publish_platform(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id="gray-everything-mcp",
            version=NEW_VERSION,
            digest=platform_digest,
        ),
        reason=f"AC07-11 阶段 5 并行版本发布 {NEW_VERSION}",
        idempotency_key=f"ac07-11-publish:{NEW_VERSION}",
    )
    if published.status == "not_ready":
        raise RuntimeError(f"发布未就绪：{published.gaps}")
    print(f"  [ok] 发布 {published.status}（admin_gray）")

    print("[5/7] 治理动作链（牺牲版本）：deprecate…")
    dep_outcome = governance.deprecate_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id="gray-everything-mcp",
            version=NEW_VERSION,
            digest=platform_digest,
        ),
        reason=f"AC07-11 阶段 5 弃用并行版本 {NEW_VERSION}（牺牲演示）",
        idempotency_key=f"ac07-11-deprecate:{NEW_VERSION}",
    )
    if dep_outcome.status == "rejected":
        raise RuntimeError(f"弃用被拒绝：{dep_outcome.gaps}")
    print(f"  [ok] deprecate {dep_outcome.status}")

    print("[6/7] revoke…")
    rev_outcome = governance.revoke_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id="gray-everything-mcp",
            version=NEW_VERSION,
            digest=platform_digest,
        ),
        reason=f"AC07-11 阶段 5 撤销并行版本 {NEW_VERSION}（牺牲演示）",
        idempotency_key=f"ac07-11-revoke:{NEW_VERSION}",
    )
    if rev_outcome.status == "rejected":
        raise RuntimeError(f"撤销被拒绝：{rev_outcome.gaps}")
    print(f"  [ok] revoke {rev_outcome.status}")

    print("[7/7] 核验：并行版本牺牲 + 主版本不受影响…")
    for version in (NEW_VERSION, "2026.7.4"):
        candidates = [
            p
            for p in catalog.list_visible_packs(actor)
            if p.pack_id == "gray-everything-mcp"
            and p.version == version
            and p.scope is ProcedureScope.PLATFORM
        ]
        if not candidates:
            print(f"  [info] {version} 平台行缺失？")
            continue
        proj = governance.runtime_projection_for_pack(candidates[0])
        print(
            f"  {version} 平台投影: {proj.maturity.value}/"
            f"{proj.lifecycle.value}/{proj.eligibility.value}"
        )
    print("[done] 并行版本治理链完成")


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    try:
        asyncio.run(_drive())
    except Exception as error:
        print(f"[FAIL] {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
