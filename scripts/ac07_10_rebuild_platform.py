# -*- coding: utf-8 -*-
"""#15 AC07-10 方案 A：重建平台发布链（修复快照缺 purpose 后的真实重建）。

流程（每版本独立）：
  1. 在线一致性备份；
  2. 清理旧平台版本：删平台 OCI tag 条目 + 删平台目录行
     （同 prepare --replace 纪律；事件流保留旧记录作失败留痕）；
  3. 重新提交平台候选（修复后快照生成器 → 新 digest，含中性脱敏 purpose）；
  4. 等待六步验证 + Cosign 签名；
  5. admin_gray 发布（新 digest）；幂等重放核验；
  6. 独立 Layout 密码学复验 + 装载门闭环。

用法：
  python scripts/ac07_10_rebuild_platform.py --version 2.0.0
  python scripts/ac07_10_rebuild_platform.py --version 3.0.0
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
PERSONAL_DIGESTS = {
    "2.0.0": "sha256:59076f406fa10f4adfcbeba3534119bc767752ef6887109df7d38b70e09fbc3a",
    "3.0.0": "sha256:0ca80afdeeb7ad6408d23f2102a75252bafc2e84d80325c8382fa15eac18c27b",
}
POLL_SECONDS = 8
TIMEOUT_SECONDS = 2400


def _backup_db() -> Path:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d-%H%M%S"
    )
    backup = (
        PROJECT_ROOT / "data/backups"
        / f"webui-before-ac07-10-rebuild-{stamp}.db"
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


def _clear_platform_old(version: str) -> None:
    """删平台 OCI tag + 删平台目录行（--replace 纪律；事件流保留）。"""
    from src.api.capability_governance_runtime import (
        get_locked_signing_toolchain,
    )

    toolchain = get_locked_signing_toolchain()
    platform_store = OrasOciLayoutStore(
        settings.capability_platform_oci_layout_path,
        oras_executable=str(toolchain.oras_executable),
    )
    # 1) 删平台 OCI tag 条目
    index_path = (
        Path(settings.capability_platform_oci_layout_path) / "index.json"
    )
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        kept = [
            m
            for m in index.get("manifests", [])
            if m.get("annotations", {}).get(
                "org.opencontainers.image.ref.name", ""
            )
            != f"gray-python-table:{version}"
        ]
        if len(kept) != len(index.get("manifests", [])):
            index["manifests"] = kept
            index_path.write_text(
                json.dumps(index, indent=2), encoding="utf-8"
            )
            print(f"  [ok] 平台 OCI tag gray-python-table:{version} 已删除")
    # 2) 删平台目录行（scope=platform）
    with sqlite3.connect(settings.webui_db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM capability_pack_versions "
            "WHERE pack_id='gray-python-table' AND version=? AND scope='platform'",
            (version,),
        )
        connection.commit()
        print(f"  [ok] 平台目录行删除 {cursor.rowcount} 行")
    # 3) 删平台物化缓存目录（避免旧 digest 缓存被复用）
    probe_root = (
        Path(settings.capability_mount_cache_path) / "platform-probes"
    )
    if probe_root.is_dir():
        for child in probe_root.iterdir():
            if version in child.name:
                import shutil

                shutil.rmtree(child, ignore_errors=True)
                print(f"  [ok] 平台物化缓存清理: {child.name}")


def _wait_platform_run_signed(
    governance: CapabilityGovernance,
    *,
    version: str,
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


async def _rebuild(version: str) -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    governance = _governance()
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    print(f"[0/7] 备份…")
    _backup_db()

    print(f"[1/7] 清理旧平台版本 {version}…")
    _clear_platform_old(version)

    print(f"[2/7] 重新提交平台候选 {version}（修复后快照）…")
    pack_ref = CapabilityPackRef(
        pack_id="gray-python-table",
        version=version,
        digest=PERSONAL_DIGESTS[version],
    )
    outcome = governance.submit_platform_candidate(
        actor,
        pack_ref=pack_ref,
        reason=f"AC07-10 方案 A 重建发布链（修复缺 purpose）{version}",
        idempotency_key=f"ac07-10-rebuild-candidate:{version}",
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"候选被拒绝：{outcome.gaps}")
    snapshot = outcome.snapshot
    if snapshot is None:
        raise RuntimeError("候选结果缺少快照")
    print(
        f"[ok] 候选 {outcome.status}；新平台 digest={snapshot.platform_digest[:18]}…"
    )

    print(f"[3/7] 启动平台验证 worker（六步 + 签名）…")
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

    print(f"[4/7] 发布 admin_gray（新 digest）…")
    publish_ref = CapabilityPackRef(
        pack_id="gray-python-table",
        version=version,
        digest=snapshot.platform_digest,
    )
    published = governance.publish_platform(
        actor,
        pack_ref=publish_ref,
        reason=f"AC07-10 方案 A 重建发布 {version}",
        idempotency_key=f"ac07-10-rebuild-publish:{version}",
    )
    if published.status == "not_ready":
        raise RuntimeError(f"发布未就绪：{published.gaps}")
    event = published.event
    if event is None:
        raise RuntimeError("发布结果缺少事件")
    print(
        f"[ok] 发布 {published.status}：sign={event.signing_signature_digest[:18]}… "
        f"pubkey={event.signing_public_key_sha256[:12]}…"
    )

    print(f"[5/7] 幂等重放…")
    replay = governance.publish_platform(
        actor,
        pack_ref=publish_ref,
        reason=f"AC07-10 方案 A 重建幂等 {version}",
        idempotency_key=f"ac07-10-rebuild-publish:{version}",
    )
    if replay.status != "already_published":
        raise RuntimeError(f"幂等重放异常：{replay.status}")
    print(f"[ok] 幂等重放 -> {replay.status}")

    print(f"[6/7] 装载门闭环核验…")
    pack = catalog.resolve_pack(
        actor, "gray-python-table", version
    )
    if pack is None or pack.scope is not ProcedureScope.PLATFORM:
        raise RuntimeError(f"{version} 不是平台包")
    from src.api.capability_governance_runtime import get_runtime_gate

    get_runtime_gate().check_mount(actor, pack)
    print(f"[ok] admin 装载门通过（签名验证成功，未隔离）")

    print(f"[7/7] 最终投影…")
    proj = governance.runtime_projection_for_pack(pack)
    print(
        f"[done] v{version} 平台 digest={snapshot.platform_digest[:18]}… "
        f"投影={proj.maturity.value}/{proj.lifecycle.value}/"
        f"{proj.eligibility.value}"
    )


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, choices=("2.0.0", "3.0.0"))
    args = parser.parse_args()
    asyncio.run(_rebuild(args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
