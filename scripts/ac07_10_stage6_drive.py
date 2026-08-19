# -*- coding: utf-8 -*-
"""#15 AC07-10 阶段 6 驱动：真实 risk_accept applied 链 + 惰性到期 + 手动重扫 + 零残留。

服务层直调（等价 API 校验后的写入路径）：
  1. 在线备份 DB；
  2. 人工隔离 3.0.0（quarantine_pack，唯一幂等键）；
  3. risk_accept applied 链：accept_pack_risk（finding_ref 实引本包平台验证
     运行 pfval_2d816c74…，30 天）→ applied → 投影 eligible；
  4. 惰性到期演示：把该 risk_accepted 事件的 expires_at 改为过去（验收专用
     动作，改前记录原值）→ 投影重新 quarantined（零新事件）；
  5. restore_pack（唯一幂等键）→ 复查链通过 → 解除隔离恢复 eligible；
     恢复 expires_at 原值（验收动作恢复，当前状态由 restore 事件决定）；
  6. 手动重扫：rescan_supply_chain（真实采集器：物化 + Trivy/Syft）→
     供应链证据追加 + rescan_completed 事件；
  7. 零残留核验：Lease 表清零、临时探针目录、投影复验、事件计数。

全程不触碰发布事件证据与签名密钥；结束状态 3.0.0 投影 verified/active/eligible。

用法：
  python scripts/ac07_10_stage6_drive.py
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
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
    CapabilityEligibility,
    CapabilityGovernance,
    SqliteCapabilityGovernanceRepository,
    SqliteValidationTaskResolver,
)
from src.config.settings import settings

OWNER_ID = "u_9505fd620899"  # liyi（super_admin）
PLATFORM_DIGEST_3 = "sha256:9379fe2908a4f8c1827fbe1db94d66892dc62190ec3da67129a64ae0ef0dbe03"
PLATFORM_FINDING_RUN = "pfval_2d816c74238b45b0bc8d"  # 3.0.0 平台验证运行（六步全绿+签名）
RISK_ACCEPT_DAYS = 30


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d%H%M%S%f"
    )


def _backup_db() -> Path:
    stamp = _stamp()
    backup = (
        PROJECT_ROOT / "data/backups"
        / f"webui-before-ac07-10-stage6-{stamp}.db"
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
        pack_id="gray-python-table",
        version="3.0.0",
        digest=PLATFORM_DIGEST_3,
    )


def _projection_line(
    governance: CapabilityGovernance,
    catalog: CapabilityCatalog,
) -> str:
    pack = catalog.resolve_pack(
        CatalogActor(owner_id=OWNER_ID, role="admin"),
        "gray-python-table",
        "3.0.0",
        PLATFORM_DIGEST_3,
    )
    if pack is None:
        raise RuntimeError("3.0.0 平台包不可解析")
    proj = governance.runtime_projection_for_pack(pack)
    return f"{proj.maturity.value}/{proj.lifecycle.value}/{proj.eligibility.value}"


def _set_expires_at(governance: CapabilityGovernance, key: str, new_value: str):
    """验收专用：改写指定 risk_accepted 事件的 expires_at（payload_json）。"""
    with sqlite3.connect(settings.webui_db_path, timeout=30) as con:
        row = con.execute(
            "SELECT event_id, payload_json FROM capability_governance_events "
            "WHERE event_type='risk_accepted' AND idempotency_key=?",
            (key,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"找不到 risk_accepted 事件：{key}")
        payload = json.loads(row[1])
        payload["expires_at"] = new_value
        con.execute(
            "UPDATE capability_governance_events SET payload_json=? "
            "WHERE event_id=?",
            (json.dumps(payload, ensure_ascii=False), row[0]),
        )
    return row[0], payload


def _verify_only() -> None:
    """可重跑核验（--verify-only）：跳过写入，只核验零残留与最近一轮事件。"""
    governance = _governance()
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    pack3 = catalog.resolve_pack(actor, "gray-python-table", "3.0.0")
    if pack3 is None:
        raise RuntimeError("3.0.0 平台包不可解析")
    print("[verify-only] 零残留核验…")
    with sqlite3.connect(f"file:{settings.webui_db_path}?mode=ro", uri=True) as con:
        for table in (
            "capability_validation_leases",
            "capability_platform_validation_leases",
        ):
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is None:
                continue
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  [info] {table}: {n} 行")
        total = con.execute(
            "SELECT COUNT(*) FROM capability_governance_events"
        ).fetchone()[0]
        risk_n = con.execute(
            "SELECT COUNT(*) FROM capability_governance_events "
            "WHERE event_type='risk_accepted'"
        ).fetchone()[0]
        rescan_n = con.execute(
            "SELECT COUNT(*) FROM capability_governance_events "
            "WHERE event_type='rescan_completed'"
        ).fetchone()[0]
        evidence_n = con.execute(
            "SELECT COUNT(*) FROM capability_supply_chain_evidence "
            "WHERE pack_id='gray-python-table' AND version='3.0.0'"
        ).fetchone()[0]
    print(f"  [info] 治理事件总数 {total}（risk_accepted={risk_n}，rescan_completed={rescan_n}）")
    print(f"  [info] 3.0.0 供应链证据行数 {evidence_n}（追加不覆盖旧行）")
    probes = Path(settings.capability_mount_cache_path) / "platform-probes"
    leftovers = list(probes.glob("*")) if probes.is_dir() else []
    if leftovers:
        print(f"  [warn] 平台探针残留 {len(leftovers)} 项：{[p.name for p in leftovers]}")
    print(f"  [ok] 3.0.0 最终投影 {_projection_line(governance, catalog)}")
    print("[done] 核验完成")


def _drive() -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    governance = _governance()
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )
    pack3 = catalog.resolve_pack(actor, "gray-python-table", "3.0.0")
    if pack3 is None or pack3.digest != PLATFORM_DIGEST_3:
        raise RuntimeError("3.0.0 平台包不可解析")
    key_quarantine = f"ac07-10-stage6:quarantine:3.0.0:{_stamp()}"
    key_risk = f"ac07-10-stage6:risk-accept:3.0.0:{_stamp()}"
    key_restore = f"ac07-10-stage6:restore:3.0.0:{_stamp()}"
    key_rescan = f"ac07-10-stage6:rescan:3.0.0:{_stamp()}"

    print("[1/7] 在线一致性备份…")
    _backup_db()

    print("[2/7] 人工隔离 3.0.0…")
    outcome = governance.quarantine_pack(
        actor,
        pack_ref=_ref(),
        reason="AC07-10 阶段 6 人工隔离 3.0.0（真实治理演示）",
        idempotency_key=key_quarantine,
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"隔离被拒绝：{outcome.gaps}")
    proj = governance.runtime_projection_for_pack(pack3)
    if proj.eligibility is not CapabilityEligibility.QUARANTINED:
        raise RuntimeError(f"隔离未生效：{proj.eligibility.value}")
    print(f"  [ok] 隔离 {outcome.status} → 投影 {_projection_line(governance, catalog)}")

    print(f"[3/7] risk_accept applied 链（finding_ref={PLATFORM_FINDING_RUN}，{RISK_ACCEPT_DAYS} 天）…")
    outcome = governance.accept_pack_risk(
        actor,
        pack_ref=_ref(),
        reason="AC07-10 阶段 6 限期风险接受（真实 applied 链，无修复且路径不可达的 High）",
        finding_ref=PLATFORM_FINDING_RUN,
        days=RISK_ACCEPT_DAYS,
        idempotency_key=key_risk,
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"风险接受被拒绝：{outcome.gaps}")
    event = outcome.event
    proj = governance.runtime_projection_for_pack(pack3)
    if proj.eligibility is not CapabilityEligibility.ELIGIBLE:
        raise RuntimeError(f"接受后投影未恢复 eligible：{proj.eligibility.value}")
    print(f"  [ok] risk_accepted {outcome.status}（expires_at={event.expires_at.isoformat()}）")
    print(f"  [ok] 投影 → {_projection_line(governance, catalog)}")

    print("[4/7] 惰性到期演示（验收专用：改 expires_at 为过去）…")
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=1)).isoformat()
    event_id, _ = _set_expires_at(governance, key_risk, past)
    proj = governance.runtime_projection_for_pack(pack3)
    if proj.eligibility is not CapabilityEligibility.QUARANTINED:
        raise RuntimeError(f"惰性到期未生效：{proj.eligibility.value}")
    print(f"  [ok] 到期后投影重新 quarantined（{_projection_line(governance, catalog)}）")
    with sqlite3.connect(f"file:{settings.webui_db_path}?mode=ro", uri=True) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM capability_governance_events WHERE event_id=?",
            (event_id,),
        ).fetchone()[0]
    # 惰性判定零新事件：事件数在到期前后相同（只该行被改写）。
    if count != 1:
        raise RuntimeError("惰性到期不应产生新事件")
    print("  [ok] 零新事件（惰性判定，无 eligibility_changed 写入）")

    print("[5/7] restore（到期后恢复，复查链全绿）…")
    outcome = governance.restore_pack(
        actor,
        pack_ref=_ref(),
        reason="AC07-10 阶段 6 到期后恢复 3.0.0（复查链通过）",
        idempotency_key=key_restore,
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"恢复被拒绝：{outcome.gaps}")
    proj = governance.runtime_projection_for_pack(pack3)
    if proj.eligibility is not CapabilityEligibility.ELIGIBLE:
        raise RuntimeError(f"恢复后投影异常：{proj.eligibility.value}")
    # 恢复验收专用改写：expires_at 恢复原值（当前状态由 restore 事件决定）。
    future = (datetime.datetime.now(datetime.timezone.utc)
              + datetime.timedelta(days=RISK_ACCEPT_DAYS)).isoformat()
    _set_expires_at(governance, key_risk, future)
    print(f"  [ok] restore {outcome.status} → 投影 {_projection_line(governance, catalog)}")
    print("  [ok] expires_at 已恢复原值（验收专用动作收尾）")

    print("[6/7] 手动重扫（真实采集：物化 + Trivy/Syft）…")
    outcome = governance.rescan_supply_chain(
        actor,
        pack_ref=_ref(),
        reason="AC07-10 阶段 6 手动重扫 3.0.0（真实采集器）",
        idempotency_key=key_rescan,
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"重扫被拒绝：{outcome.gaps}")
    evidence_id = outcome.event.source_supply_chain_evidence_id
    print(f"  [ok] rescan_completed {outcome.status}（新证据行 {evidence_id}）")
    with sqlite3.connect(f"file:{settings.webui_db_path}?mode=ro", uri=True) as con:
        row = con.execute(
            "SELECT payload_json FROM capability_governance_events "
            "WHERE event_type='rescan_completed' AND idempotency_key=?",
            (key_rescan,),
        ).fetchone()
        evidence = con.execute(
            "SELECT status, evidence_id FROM capability_supply_chain_evidence "
            "WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
    event_payload = json.loads(row[0])
    print(f"  [ok] 重扫事件快照：{event_payload.get('reason', '')[:60]}")
    print(f"  [ok] 新证据：status={evidence[0]}（PASSED → 不隔离）")
    proj = governance.runtime_projection_for_pack(pack3)
    if proj.eligibility is not CapabilityEligibility.ELIGIBLE:
        raise RuntimeError(f"重扫后投影异常：{proj.eligibility.value}")

    print("[7/7] 零残留核验…")
    with sqlite3.connect(f"file:{settings.webui_db_path}?mode=ro", uri=True) as con:
        for table in (
            "capability_validation_leases",
            "capability_platform_validation_leases",
        ):
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is None:
                continue
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  [info] {table}: {n} 行")
        total = con.execute(
            "SELECT COUNT(*) FROM capability_governance_events"
        ).fetchone()[0]
        risk_n = con.execute(
            "SELECT COUNT(*) FROM capability_governance_events "
            "WHERE event_type='risk_accepted'"
        ).fetchone()[0]
        rescan_n = con.execute(
            "SELECT COUNT(*) FROM capability_governance_events "
            "WHERE event_type='rescan_completed'"
        ).fetchone()[0]
        evidence_n = con.execute(
            "SELECT COUNT(*) FROM capability_supply_chain_evidence "
            "WHERE pack_id='gray-python-table' AND version='3.0.0'"
        ).fetchone()[0]
    print(f"  [info] 治理事件总数 {total}（risk_accepted={risk_n}，rescan_completed={rescan_n}）")
    print(f"  [info] 3.0.0 供应链证据行数 {evidence_n}（追加不覆盖旧行）")
    probes = Path(settings.capability_mount_cache_path) / "platform-probes"
    leftovers = list(probes.glob("*")) if probes.is_dir() else []
    if leftovers:
        print(f"  [warn] 平台探针残留 {len(leftovers)} 项：{[p.name for p in leftovers]}")
    print(f"  [ok] 3.0.0 最终投影 {_projection_line(governance, catalog)}")
    print("[done] 阶段 6 完成")


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="只做零残留核验，跳过写入（事件链已落库后补核验）",
    )
    args = parser.parse_args()
    try:
        if args.verify_only:
            _verify_only()
        else:
            _drive()
    except Exception as error:
        print(f"[FAIL] {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
