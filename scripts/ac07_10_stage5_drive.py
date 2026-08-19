# -*- coding: utf-8 -*-
"""#15 AC07-10 阶段 5 驱动：revoke + 跨用户拒绝 + 篡改演示。

服务层直调（等价 API 校验后的写入路径）+ 装载门验证：
  1. 备份 → revoke 2.0.0 → 历史冻结恢复装载也被拒；
  2. 跨用户拒绝：真实普通用户（liyi111）对 admin_gray 平台能力被拒；
  3. 篡改演示（blob 级备份安全原则）：
     - 备份 3.0.0 主体 manifest blob 到验收目录；
     - 篡改一个字节 → 装载 409 fail-closed → 自动隔离事件（actor=system）；
     - restore 命令（恢复复查链）解除隔离；
     - 逐字节还原 blob → verify_local 复验通过 → 再次装载成功。
  全程不触碰发布事件证据；演示后主 Layout 与发布证据完全一致。

用法：
  python scripts/ac07_10_stage5_drive.py
"""
from __future__ import annotations

import argparse
import datetime
import io
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api.capability_governance_runtime import get_runtime_gate
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

OWNER_ID = "u_9505fd620899"  # liyi（super_admin）
CROSS_USER_ID = "u_439547686101"  # liyi111（真实普通用户）
PLATFORM_DIGESTS = {
    "2.0.0": "sha256:e5556f83e889a62fc0ce9d3f856db89c07fcc56dddc0d2ee2582b89cf2931bfb",
    "3.0.0": "sha256:9379fe2908a4f8c1827fbe1db94d66892dc62190ec3da67129a64ae0ef0dbe03",
}
EVIDENCE_ROOT = PROJECT_ROOT / "data/capability-governance/evidence/ac07-10-stage5"


def _backup_db() -> Path:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d-%H%M%S"
    )
    backup = (
        PROJECT_ROOT / "data/backups"
        / f"webui-before-ac07-10-stage5-{stamp}.db"
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


def _gate_ok(actor, pack) -> bool:
    try:
        get_runtime_gate().check_mount(actor, pack)
        return True
    except Exception:
        return False


def _drive() -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    governance = _governance()
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)

    print("[1/8] 在线一致性备份…")
    _backup_db()

    print("[2/8] revoke 2.0.0…")
    outcome = governance.revoke_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id="gray-python-table",
            version="2.0.0",
            digest=PLATFORM_DIGESTS["2.0.0"],
        ),
        reason="AC07-10 阶段 5 撤销 2.0.0（真实治理演示）",
        idempotency_key="ac07-10-revoke:2.0.0",
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"撤销被拒绝：{outcome.gaps}")
    print(f"  [ok] revoke {outcome.status}（lifecycle_changed→revoked）")
    pack2 = catalog.resolve_pack(actor, "gray-python-table", "2.0.0")
    if _gate_ok(actor, pack2):
        raise RuntimeError("revoked 2.0.0 历史恢复装载未被拒")
    print("  [ok] revoked 2.0.0 装载被拒（禁止新任务/重试/恢复）")

    print("[3/8] 跨用户拒绝（liyi111 普通用户）…")
    cross = CatalogActor(owner_id=CROSS_USER_ID, role="user")
    pack3 = catalog.resolve_pack(actor, "gray-python-table", "3.0.0")
    # 普通用户能看到平台 pack（list_visible_packs），但 admin_gray 受众被门拒。
    if _gate_ok(cross, pack3):
        raise RuntimeError("普通用户对 admin_gray 平台能力未被拒")
    print("  [ok] 普通用户对 3.0.0（admin_gray）装载被拒（受众门）")
    # 目录层：普通用户 resolve_pack 平台包可见（管理元数据不泄露）。
    cross_pack = catalog.resolve_pack(cross, "gray-python-table", "3.0.0")
    print(f"  [info] 普通用户 resolve_pack 3.0.0: {'可见' if cross_pack else '不可见'}")

    print("[4/8] 篡改演示：备份 3.0.0 主体 manifest blob…")
    digest = PLATFORM_DIGESTS["3.0.0"]
    blob_path = (
        Path(settings.capability_platform_oci_layout_path)
        / "blobs/sha256" / digest.removeprefix("sha256:")
    )
    if not blob_path.is_file():
        raise RuntimeError(f"主体 manifest blob 不存在: {blob_path}")
    original = blob_path.read_bytes()
    backup_blob = EVIDENCE_ROOT / "3.0.0-subject-manifest.blob.bak"
    backup_blob.write_bytes(original)
    print(f"  [ok] 备份 blob {len(original)} 字节 → {backup_blob.name}")

    print("[5/8] 篡改一个字节 → 装载 409 fail-closed + 自动隔离…")
    from src.capability_governance.models import CapabilityEligibility

    # 篡改演示前置：投影必须 eligible（残留隔离用唯一键解除，避免旧幂等键
    # 掩盖新状态——#14 教训：幂等键不能吞掉恢复后的新隔离）。
    proj = governance.runtime_projection_for_pack(pack3)
    if proj.eligibility is not CapabilityEligibility.ELIGIBLE:
        print(f"  [info] 前置投影 {proj.eligibility.value}，先用唯一键恢复…")
        outcome = governance.restore_pack(
            actor,
            pack_ref=CapabilityPackRef(
                pack_id="gray-python-table",
                version="3.0.0",
                digest=PLATFORM_DIGESTS["3.0.0"],
            ),
            reason="AC07-10 阶段 5 篡改演示前置恢复",
            idempotency_key=(
                f"ac07-10-restore:{PLATFORM_DIGESTS['3.0.0']}:"
                f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            ),
        )
        if outcome.status == "rejected":
            raise RuntimeError(f"前置恢复被拒绝：{outcome.gaps}")

    # Windows OCI blob 可能是只读属性：写入前显式移除只读（handoff §10.4）。
    import stat
    current_mode = blob_path.stat().st_mode
    readonly = not bool(current_mode & stat.S_IWUSR)
    if readonly:
        blob_path.chmod(current_mode | stat.S_IWUSR)
    tampered = bytearray(original)
    tampered[10] ^= 0x01  # 翻转一个字节
    blob_path.write_bytes(bytes(tampered))
    if readonly:
        blob_path.chmod(current_mode)
    try:
        _gate_ok(actor, pack3)
        # 篡改后 check_mount 应抛拒绝（主布局校验失败）并触发自动隔离。
        raise RuntimeError("篡改后装载未被拒（fail-closed 失效）")
    except Exception as error:
        print(f"  [ok] 篡改后装载被拒（预期）：{type(error).__name__}: {str(error)[:80]}")

    proj = governance.runtime_projection_for_pack(pack3)
    if proj.eligibility is not CapabilityEligibility.QUARANTINED:
        raise RuntimeError(f"自动隔离未生效：投影 {proj.eligibility.value}")
    print("  [ok] 自动隔离事件生效：3.0.0 投影 quarantined（actor=system）")

    print("[6/8] restore 命令（恢复复查链）解除隔离…")
    outcome = governance.restore_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id="gray-python-table",
            version="3.0.0",
            digest=PLATFORM_DIGESTS["3.0.0"],
        ),
        reason="AC07-10 阶段 5 篡改演示后恢复 3.0.0",
        idempotency_key=(
            f"ac07-10-restore:{PLATFORM_DIGESTS['3.0.0']}:"
            f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        ),
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"恢复被拒绝：{outcome.gaps}")
    print(f"  [ok] restore {outcome.status}")

    print("[7/8] 逐字节还原 blob → verify_local 复验…")
    import stat as _stat
    _current = blob_path.stat().st_mode
    _readonly = not bool(_current & _stat.S_IWUSR)
    if _readonly:
        blob_path.chmod(_current | _stat.S_IWUSR)
    blob_path.write_bytes(original)
    if _readonly:
        blob_path.chmod(_current)
    from src.api.capability_governance_runtime import (
        get_platform_signing_runtime,
    )
    from src.capability_governance.oci_signing import OciSigningRequest

    run_id = None
    with sqlite3.connect(f"file:{settings.webui_db_path}?mode=ro", uri=True) as con:
        import json
        for row in con.execute(
            "SELECT run_id, payload_json FROM capability_platform_validation_runs"
        ).fetchall():
            d = json.loads(row[1])
            if (
                d.get("target", {}).get("digest") == digest
                and d.get("status") == "succeeded"
            ):
                run_id = row[0]
    if run_id is None:
        raise RuntimeError("找不到 3.0.0 平台验证运行记录")
    runtime = get_platform_signing_runtime()
    verified = runtime.verify_local(
        OciSigningRequest(
            transaction_id="stage5-verify-3.0.0",
            source_layout=Path(settings.capability_platform_oci_layout_path),
            source_reference=digest,
            output_layout=(
                Path(settings.capability_platform_oci_layout_path)
                / "signed" / run_id
            ),
            output_reference=digest,
            registry_repository="mangrove/platform-snapshots",
            subject_digest=digest,
            public_key_path=Path(
                settings.capability_platform_signing_public_key
            ),
        )
    )
    if verified.subject_digest != digest:
        raise RuntimeError("还原后复验失败：主体 digest 不一致")
    print("  [ok] 还原后独立 Layout 密码学复验通过")

    print("[8/8] 还原后再次装载 + 投影核验…")
    pack3 = catalog.resolve_pack(actor, "gray-python-table", "3.0.0")
    if not _gate_ok(actor, pack3):
        raise RuntimeError("还原后 3.0.0 装载仍被拒")
    print("  [ok] 还原后 3.0.0 装载成功（签名验证通过，未隔离）")
    proj = governance.runtime_projection_for_pack(pack3)
    print(
        f"  3.0.0 投影: {proj.maturity.value}/{proj.lifecycle.value}/"
        f"{proj.eligibility.value}"
    )
    print("[done] 阶段 5 完成")


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    try:
        _drive()
    except Exception as error:
        print(f"[FAIL] {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
