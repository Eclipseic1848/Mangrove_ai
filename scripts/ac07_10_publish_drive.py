# -*- coding: utf-8 -*-
"""#15 AC07-10 阶段 3 驱动：平台候选 -> 脱敏快照 -> 六步验证 -> 签名 -> admin_gray 发布。

服务层直调（等价 API 校验后的写入路径）：submit_platform_candidate（候选门 +
确定性脱敏快照 + platform_candidate 事件 + 平台验证运行 QUEUED）→ 本进程启动
平台验证 worker（六步验证 + Cosign 签名；8088 常驻 worker 也可能竞争执行，
Lease 串行化保证同一 run 只被一个 worker 执行）→ 轮询至 SUCCEEDED 且签名证据
齐备 → publish_platform（预期状态 + 目录写 + platform_published 事件）→
同幂等键重放 already_published（AC6 幂等）。

用法：
  python scripts/ac07_10_publish_drive.py --version 2.0.0
  python scripts/ac07_10_publish_drive.py --version 3.0.0
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import io
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
POLL_SECONDS = 8
TIMEOUT_SECONDS = 2400  # 六步含真实 Trivy/Syft 扫描 + 签名事务，给足时间


def _backup_db() -> Path:
    """写动作前在线一致性备份（同 #12/#14 惯例）。"""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d-%H%M%S"
    )
    backup = (
        PROJECT_ROOT / "data/backups" / f"webui-before-ac07-10-publish-{stamp}.db"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.webui_db_path, timeout=30) as source:
        with sqlite3.connect(backup, timeout=30) as destination:
            source.backup(destination)
    print(f"[backup] {backup}")
    return backup


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


def _wait_platform_run_signed(
    governance: CapabilityGovernance,
    *,
    version: str,
    platform_digest: str,
) -> None:
    """轮询候选列表直至验证六步 SUCCEEDED 且签名证据齐备。"""
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last = ""
    while time.monotonic() < deadline:
        items = governance.list_platform_candidates(actor)
        item = next(
            (
                i
                for i in items
                if i.version == version and i.platform_digest == platform_digest
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
            print(f"[poll] {version} {status_line}")
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
        f"平台验证 {version} 未在 {TIMEOUT_SECONDS}s 内全绿并签名"
    )


async def _drive(version: str) -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    governance = _governance()
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    pack = catalog.resolve_pack(actor, "gray-python-table", version)
    if pack is None or pack.scope is not ProcedureScope.PERSONAL:
        raise RuntimeError(f"{version} 不是个人能力包")
    pack_ref = CapabilityPackRef(
        pack_id=pack.pack_id, version=pack.version, digest=pack.digest
    )
    print(f"[0/6] 在线一致性备份…")
    _backup_db()

    print(
        f"[1/6] 提交平台候选 {version}（个人 digest={pack.digest[:18]}…）"
    )
    outcome = governance.submit_platform_candidate(
        actor,
        pack_ref=pack_ref,
        reason=f"AC07-10 阶段 3 真实平台发布 {version}",
        idempotency_key=f"ac07-10-candidate:{version}",
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

    print("[2/6] 启动本进程平台验证 worker（六步验证 + Cosign 签名）…")
    manager = get_platform_validation_manager()
    manager.start()
    try:
        await asyncio.to_thread(
            _wait_platform_run_signed,
            governance,
            version=version,
            platform_digest=snapshot.platform_digest,
        )
    finally:
        await manager.stop()
    print("[ok] 六步验证全绿且签名证据齐备")

    print("[3/6] 发布 admin_gray…")
    publish_ref = CapabilityPackRef(
        pack_id="gray-python-table",
        version=version,
        digest=snapshot.platform_digest,
    )
    published = governance.publish_platform(
        actor,
        pack_ref=publish_ref,
        reason=f"AC07-10 阶段 3 真实平台发布 {version}",
        idempotency_key=f"ac07-10-publish:{version}",
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

    print("[4/6] 幂等重放：同幂等键再发布…")
    replay = governance.publish_platform(
        actor,
        pack_ref=publish_ref,
        reason=f"AC07-10 阶段 3 幂等重放 {version}",
        idempotency_key=f"ac07-10-publish:{version}",
    )
    if replay.status != "already_published":
        raise RuntimeError(f"幂等重放异常：{replay.status}")
    print(f"[ok] 幂等重放 -> {replay.status}（事件未重复写入）")

    print("[5/6] 候选重放：同幂等键再提交候选…")
    replay_candidate = governance.submit_platform_candidate(
        actor,
        pack_ref=pack_ref,
        reason=f"AC07-10 阶段 3 候选幂等重放 {version}",
        idempotency_key=f"ac07-10-candidate:{version}",
    )
    if replay_candidate.status != "already_submitted":
        raise RuntimeError(f"候选幂等重放异常：{replay_candidate.status}")
    print(f"[ok] 候选幂等重放 -> {replay_candidate.status}")

    print("[6/6] 投影与事件核验…")
    projection = governance.runtime_projection_for_pack(
        catalog.resolve_pack(actor, "gray-python-table", version)
    )
    latest = governance.list_platform_candidates(actor)
    match = next(
        (i for i in latest if i.version == version), None
    )
    print(
        f"[done] {version} 平台 digest={snapshot.platform_digest[:18]}… "
        f"验证={match.validation_status if match else '?'} "
        f"签名={match.signed if match else '?'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, choices=("2.0.0", "3.0.0"))
    args = parser.parse_args()
    asyncio.run(_drive(args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
