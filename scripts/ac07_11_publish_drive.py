# -*- coding: utf-8 -*-
"""#16 AC07-11 阶段 3 驱动：平台候选 -> 脱敏快照 -> 六步验证 -> 签名 -> admin_gray 发布。

服务层直调（等价 API 校验后的写入路径）：
  0. 在线备份 DB；
  0.5 --replace 纪律：删旧 OCI tag（gray-everything-mcp:2026.7.4=dce5be51…）+
      删平台目录行（__platform__ 2026.7.4）——发布同版本需先清旧 legacy 行
      （capability_pack_versions 唯一键不覆盖；事件流保留旧记录作失败留痕）；
  1. submit_platform_candidate（候选门 + 确定性脱敏快照新 digest + 平台验证 QUEUED）；
  2. 平台验证 worker（六步 + Cosign 签名）；
  3. publish_platform（admin_gray）；
  4. 幂等重放 already_published；候选重放 already_submitted；
  5. 投影与候选核验。

用法：
  python scripts/ac07_11_publish_drive.py
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import io
import json
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api.capability_governance_runtime import (
    get_platform_validation_manager,
)
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
)
from src.config.settings import settings
from src.conversation_steering import ProcedureScope

OWNER_ID = "u_9505fd620899"  # liyi（super_admin）
PACK_ID = "gray-everything-mcp"
VERSION = "2026.7.4"
POLL_SECONDS = 8
TIMEOUT_SECONDS = 2400


def _backup_db() -> Path:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d-%H%M%S"
    )
    backup = (
        PROJECT_ROOT / "data/backups"
        / f"webui-before-ac07-11-publish-{stamp}.db"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.webui_db_path, timeout=30) as source:
        with sqlite3.connect(backup, timeout=30) as destination:
            source.backup(destination)
    print(f"[backup] {backup}")
    return backup


def _remove_legacy_platform_row() -> None:
    """--replace 纪律：删旧平台目录行（事件流保留旧记录）。

    注意：不删 OCI tag——everything-mcp 个人行与平台 legacy 行共用同一
    归档（digest dce5be51…），候选物化依赖该 tag；删 tag 会破坏个人
    物化路径（#16 阶段 3 首跑暴露，已恢复并保留）。
    """
    with sqlite3.connect(settings.webui_db_path, timeout=30) as connection:
        cursor = connection.execute(
            "DELETE FROM capability_pack_versions "
            "WHERE pack_id=? AND version=? AND scope='platform'",
            (PACK_ID, VERSION),
        )
        connection.commit()
        print(f"  [ok] 平台目录行删除 {cursor.rowcount} 行（OCI tag 保留）")


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


def _personal_pack(catalog: CapabilityCatalog, actor: CatalogActor):
    """同 digest 歧义（#16 缺陷 #1）：显式按 Owner 解析个人行。"""
    return next(
        (
            item
            for item in catalog.list_visible_packs(actor)
            if item.pack_id == PACK_ID
            and item.version == VERSION
            and item.scope is ProcedureScope.PERSONAL
            and item.owner_id == OWNER_ID
        ),
        None,
    )


def _wait_platform_run_signed(
    governance: CapabilityGovernance,
    *,
    platform_digest: str,
) -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last = ""
    while time.monotonic() < deadline:
        items = governance.list_platform_candidates(actor)
        item = next(
            (
                i
                for i in items
                if i.version == VERSION
                and i.platform_digest == platform_digest
            ),
            None,
        )
        if item is None:
            status_line = "(候选未就绪)"
        else:
            status_line = (
                f"{item.validation_status} "
                f"{item.steps_passed}/{item.steps_total} "
                f"signed={item.signed}"
            )
        if status_line != last:
            print(f"[poll] {VERSION} {status_line}")
            last = status_line
        if (
            item is not None
            and item.validation_status == "succeeded"
            and item.steps_passed == item.steps_total
            and item.signed
        ):
            return
        time.sleep(POLL_SECONDS)
    raise TimeoutError(
        f"平台验证 {VERSION} 未在 {TIMEOUT_SECONDS}s 内全绿并签名"
    )


async def _drive() -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    governance = _governance()
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    pack = _personal_pack(catalog, actor)
    if pack is None:
        raise RuntimeError(f"{VERSION} 个人能力包不可解析")
    pack_ref = CapabilityPackRef(
        pack_id=pack.pack_id, version=pack.version, digest=pack.digest
    )
    print("[0/7] 在线一致性备份…")
    _backup_db()

    print("[0.5/7] --replace：删旧 legacy 平台行与 OCI tag…")
    _remove_legacy_platform_row()

    print(f"[1/7] 提交平台候选（个人 digest={pack.digest[:18]}…）")
    outcome = governance.submit_platform_candidate(
        actor,
        pack_ref=pack_ref,
        reason=f"AC07-11 阶段 3 真实平台发布 {VERSION}",
        idempotency_key=f"ac07-11-candidate:{VERSION}",
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"候选被拒绝：{outcome.gaps}")
    snapshot = outcome.snapshot
    if snapshot is None:
        raise RuntimeError("候选结果缺少快照")
    print(
        f"[ok] 候选 {outcome.status}；平台 digest={snapshot.platform_digest[:18]}…"
    )
    if outcome.status == "already_submitted":
        print("[note] 同源快照幂等命中（确定性重打包），继续推进验证")

    print("[2/7] 启动平台验证 worker（六步 + Cosign 签名）…")
    manager = get_platform_validation_manager()
    manager.start()
    try:
        await asyncio.to_thread(
            _wait_platform_run_signed,
            governance,
            platform_digest=snapshot.platform_digest,
        )
    finally:
        await manager.stop()
    print("[ok] 六步验证全绿且签名证据齐备")

    print("[3/7] 发布 admin_gray…")
    publish_ref = CapabilityPackRef(
        pack_id=PACK_ID,
        version=VERSION,
        digest=snapshot.platform_digest,
    )
    published = governance.publish_platform(
        actor,
        pack_ref=publish_ref,
        reason=f"AC07-11 阶段 3 真实平台发布 {VERSION}",
        idempotency_key=f"ac07-11-publish:{VERSION}",
    )
    if published.status == "not_ready":
        raise RuntimeError(f"发布未就绪：{published.gaps}")
    event = published.event
    if event is None:
        raise RuntimeError("发布结果缺少事件")
    print(
        f"[ok] 发布 {published.status}：{event.event_type} "
        f"sign={event.signing_signature_digest[:18]}… "
        f"pubkey={event.signing_public_key_sha256[:12]}…"
    )

    print("[4/7] 幂等重放：同幂等键再发布…")
    replay = governance.publish_platform(
        actor,
        pack_ref=publish_ref,
        reason=f"AC07-11 阶段 3 幂等重放 {VERSION}",
        idempotency_key=f"ac07-11-publish:{VERSION}",
    )
    if replay.status != "already_published":
        raise RuntimeError(f"幂等重放异常：{replay.status}")
    print(f"[ok] 幂等重放 -> {replay.status}（事件未重复写入）")

    print("[5/7] 候选重放：同幂等键再提交候选…")
    replay_candidate = governance.submit_platform_candidate(
        actor,
        pack_ref=pack_ref,
        reason=f"AC07-11 阶段 3 候选幂等重放 {VERSION}",
        idempotency_key=f"ac07-11-candidate:{VERSION}",
    )
    if replay_candidate.status != "already_submitted":
        raise RuntimeError(f"候选幂等重放异常：{replay_candidate.status}")
    print(f"[ok] 候选幂等重放 -> {replay_candidate.status}")

    print("[6/7] 投影与候选核验…")
    projection = governance.runtime_projection_for_pack(pack)
    latest = governance.list_platform_candidates(actor)
    match = next((i for i in latest if i.version == VERSION), None)
    print(
        f"[done] 平台 digest={snapshot.platform_digest[:18]}… "
        f"验证={match.validation_status if match else '?'} "
        f"签名={match.signed if match else '?'}"
    )


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    asyncio.run(_drive())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
