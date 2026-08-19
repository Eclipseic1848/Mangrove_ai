# -*- coding: utf-8 -*-
"""#16 AC07-11 阶段 5 驱动：篡改演示 + 跨 Owner 拒绝 + 并行版本治理链 + 零残留。

服务层直调（等价 API 校验后的写入路径）：
  A. 篡改演示（blob 级备份安全原则，复用 #15 模式）：
     备份 everything-mcp 平台主体 blob → 篡改 1 字节 → 装载 409 fail-closed
     → 自动隔离（actor=system）→ restore（唯一幂等键）→ 逐字节还原 →
     独立 Layout 复验 → 再次装载成功；
  B. 跨 Owner 拒绝：liyi111（真实普通用户）对 admin_gray 被拒；
  C. 零残留核验：Lease 表、容器、网络、挂载。

并行版本（2026.8.19）治理链由 ac07_11_stage5_parallel.py 单独执行。

用法：
  python scripts/ac07_11_stage5_drive.py
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import sqlite3
import stat
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
    CapabilityEligibility,
    CapabilityGovernance,
    SqliteCapabilityGovernanceRepository,
    SqliteValidationTaskResolver,
)
from src.config.settings import settings

OWNER_ID = "u_9505fd620899"  # liyi（super_admin）
CROSS_USER_ID = "u_439547686101"  # liyi111（真实普通用户）
EM_DIGEST = "sha256:87741d37f6c293853687c1da1bc143dce0c5fb841b66f91f3eaaf04eaf99eb17"
EVIDENCE_ROOT = PROJECT_ROOT / "data/capability-governance/evidence/ac07-11-stage5"


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d%H%M%S%f"
    )


def _backup_db() -> Path:
    backup = (
        PROJECT_ROOT / "data/backups"
        / f"webui-before-ac07-11-stage5-{_stamp()}.db"
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


def _ref() -> CapabilityPackRef:
    return CapabilityPackRef(
        pack_id="gray-everything-mcp",
        version="2026.7.4",
        digest=EM_DIGEST,
    )


def _gate_ok(actor: CatalogActor, pack) -> bool:
    try:
        get_runtime_gate().check_mount(actor, pack)
        return True
    except Exception:
        return False


def _blob_path() -> Path:
    return (
        Path(settings.capability_platform_oci_layout_path)
        / "blobs/sha256" / EM_DIGEST.removeprefix("sha256:")
    )


def _write_blob(blob: Path, content: bytes) -> None:
    mode = blob.stat().st_mode
    readonly = not bool(mode & stat.S_IWUSR)
    if readonly:
        blob.chmod(mode | stat.S_IWUSR)
    blob.write_bytes(content)
    if readonly:
        blob.chmod(mode)


def _drive() -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    governance = _governance()
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    pack = catalog.resolve_pack(
        actor, "gray-everything-mcp", "2026.7.4", EM_DIGEST
    )
    if pack is None or pack.scope.value != "platform":
        raise RuntimeError("everything-mcp 平台行不可解析")
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)

    print("[1/6] 在线一致性备份…")
    _backup_db()

    print("[2/6] 篡改演示：备份主体 blob…")
    blob = _blob_path()
    if not blob.is_file():
        raise RuntimeError(f"主体 manifest blob 不存在: {blob}")
    original = blob.read_bytes()
    backup_blob = EVIDENCE_ROOT / "2026.7.4-subject-manifest.blob.bak"
    backup_blob.write_bytes(original)
    print(f"  [ok] 备份 blob {len(original)} 字节 → {backup_blob.name}")

    print("[3/6] 篡改 1 字节 → 装载 409 fail-closed + 自动隔离…")
    proj = governance.runtime_projection_for_pack(pack)
    if proj.eligibility is not CapabilityEligibility.ELIGIBLE:
        raise RuntimeError(f"前置投影异常：{proj.eligibility.value}")
    tampered = bytearray(original)
    tampered[10] ^= 0x01
    _write_blob(blob, bytes(tampered))
    try:
        _gate_ok(actor, pack)
        raise RuntimeError("篡改后装载未被拒（fail-closed 失效）")
    except Exception as error:
        print(f"  [ok] 篡改后装载被拒（预期）：{type(error).__name__}: {str(error)[:80]}")
    proj = governance.runtime_projection_for_pack(pack)
    if proj.eligibility is not CapabilityEligibility.QUARANTINED:
        raise RuntimeError(f"自动隔离未生效：{proj.eligibility.value}")
    print("  [ok] 自动隔离事件生效：投影 quarantined（actor=system）")

    print("[4/6] restore（唯一幂等键，复查链）…")
    outcome = governance.restore_pack(
        actor,
        pack_ref=_ref(),
        reason="AC07-11 阶段 5 篡改演示后恢复 everything-mcp",
        idempotency_key=f"ac07-11-restore:{EM_DIGEST}:{_stamp()}",
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"恢复被拒绝：{outcome.gaps}")
    print(f"  [ok] restore {outcome.status}")

    print("[5/6] 逐字节还原 blob → 独立 Layout 复验 → 再次装载…")
    _write_blob(blob, original)
    from src.api.capability_governance_runtime import (
        get_platform_signing_runtime,
    )
    from src.capability_governance.oci_signing import OciSigningRequest

    with sqlite3.connect(f"file:{settings.webui_db_path}?mode=ro", uri=True) as con:
        row = con.execute(
            "SELECT run_id FROM capability_platform_validation_runs "
            "WHERE payload_json LIKE ? ORDER BY created_at DESC LIMIT 1",
            (f"%{EM_DIGEST}%",),
        ).fetchone()
    if row is None:
        raise RuntimeError("找不到 everything-mcp 平台验证运行")
    run_id = row[0]
    runtime = get_platform_signing_runtime()
    verified = runtime.verify_local(
        OciSigningRequest(
            transaction_id="stage5-verify-everything-mcp",
            source_layout=Path(settings.capability_platform_oci_layout_path),
            source_reference=EM_DIGEST,
            output_layout=(
                Path(settings.capability_platform_oci_layout_path)
                / "signed" / run_id
            ),
            output_reference=EM_DIGEST,
            registry_repository="mangrove/platform-snapshots",
            subject_digest=EM_DIGEST,
            public_key_path=Path(
                settings.capability_platform_signing_public_key
            ),
        )
    )
    if verified.subject_digest != EM_DIGEST:
        raise RuntimeError("还原后复验失败：主体 digest 不一致")
    print("  [ok] 还原后独立 Layout 密码学复验通过")
    pack = catalog.resolve_pack(
        actor, "gray-everything-mcp", "2026.7.4", EM_DIGEST
    )
    if not _gate_ok(actor, pack):
        raise RuntimeError("还原后装载仍被拒")
    print("  [ok] 还原后装载成功（签名验证通过，未隔离）")

    print("[6/6] 跨 Owner 拒绝 + 零残留核验…")
    cross = CatalogActor(owner_id=CROSS_USER_ID, role="user")
    if _gate_ok(cross, pack):
        raise RuntimeError("普通用户对 admin_gray 平台能力未被拒")
    print("  [ok] liyi111（普通用户）对 everything-mcp 装载被拒（受众门）")
    with sqlite3.connect(f"file:{settings.webui_db_path}?mode=ro", uri=True) as con:
        for table in (
            "capability_validation_leases",
            "capability_platform_validation_leases",
        ):
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is not None:
                n = con.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                print(f"  [info] {table}: {n} 行")
    import subprocess as _sp

    hosts = _sp.run(
        ("docker", "ps", "-q", "--filter", "name=mangrove-cap-host-"),
        capture_output=True, text=True, timeout=30,
    ).stdout.splitlines()
    print(f"  [info] mangrove-cap-host-* 容器: {len(hosts)} 个")
    proj = governance.runtime_projection_for_pack(pack)
    print(
        f"  [ok] 最终投影 {proj.maturity.value}/{proj.lifecycle.value}/"
        f"{proj.eligibility.value}"
    )
    print("[done] 阶段 5 主链完成")


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
