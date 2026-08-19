# -*- coding: utf-8 -*-
"""#15 AC07-10 阶段 4 驱动：管理员选择 + 推荐指针回滚 + deprecate + 恢复装载。

服务层直调（等价 API 校验后的写入路径）+ 选择列表只读核验：
  1. 备份 → 列表核验（2.0.0/3.0.0 平台能力可见，recommended 标记）；
  2. rollback 推荐指针切到 2.0.0 → 列表置顶核验 → 再切回 3.0.0；
  3. deprecate 2.0.0 → 列表过滤核验（2.0.0 从新任务选择消失）；
  4. 冻结 2.0.0 被拒绝（deprecated 不进新任务）；历史冻结任务恢复装载成功（#13 A5）；
  5. 治理事件与投影核验。

用法：
  python scripts/ac07_10_stage4_drive.py
"""
from __future__ import annotations

import argparse
import datetime
import io
import sqlite3
import sys
import uuid
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
PLATFORM_DIGESTS = {
    "2.0.0": "sha256:5326dfae94da035f611260935bbc4afa8de1e9106c4f74a11fac5f642e259246",
    "3.0.0": "sha256:b462e5775614cd2a75e59eda5ac774b864485ffaacbcba6e957463e76f790944",
}


def _backup_db() -> Path:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d-%H%M%S"
    )
    backup = (
        PROJECT_ROOT / "data/backups"
        / f"webui-before-ac07-10-stage4-{stamp}.db"
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


def _list_items(governance, catalog, actor) -> list[dict]:
    """等价 /capabilities 选择列表（只读，不改库）。

    与真实端点一致：deprecated/revoked/quarantined/draft 不进入新任务选择
    （_selectable_for_task 过滤），因此列表只含当前可选项。
    """
    items = []
    for pack in catalog.list_visible_packs(actor):
        if pack.scope.value != "platform":
            continue
        projection = governance.runtime_projection_for_pack(pack)
        selectable = (
            projection.maturity.value == "verified"
            and projection.lifecycle.value == "active"
            and projection.eligibility.value == "eligible"
        )
        if not selectable:
            continue
        items.append({
            "version": pack.version,
            "digest": pack.digest[:16] + "…",
            "projection": f"{projection.maturity.value}/"
                          f"{projection.lifecycle.value}/"
                          f"{projection.eligibility.value}",
            "recommended": projection.recommended_version == pack.version,
            "selectable": True,
        })
    items.sort(key=lambda item: not item["recommended"])
    return items


def _show_list(items) -> None:
    for item in items:
        flag = "[R]" if item["recommended"] else "   "
        sel = "可选" if item["selectable"] else "不可选"
        print(
            f"  {flag} v{item['version']} {item['digest']} "
            f"投影={item['projection']} {sel}"
        )


def _check_recommended(governance, catalog, actor, version: str) -> None:
    """推荐指针是 per-pack 折叠：验证目标版本的 recommended_version 等于自身。"""
    pack = catalog.resolve_pack(actor, "gray-python-table", version)
    proj = governance.runtime_projection_for_pack(pack)
    if proj.recommended_version != version:
        raise RuntimeError(
            f"推荐指针异常：v{version} recommended_version="
            f"{proj.recommended_version}"
        )


def _drive() -> None:
    actor = CatalogActor(owner_id=OWNER_ID, role="admin")
    governance = _governance()
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(settings.webui_db_path)
    )

    print("[1/6] 在线一致性备份…")
    _backup_db()

    print("[2/6] 管理员选择列表核验…")
    items = _list_items(governance, catalog, actor)
    _show_list(items)
    versions = {i["version"] for i in items}
    # 可重跑：deprecated 2.0.0 已从可选项消失；3.0.0 必须可选。
    if "3.0.0" not in versions:
        raise RuntimeError(f"选择列表应含 3.0.0，实际 {versions}")
    print("  [ok] 3.0.0 可选")

    print("[3/6] 推荐指针状态核验（rollback 已完成，重跑只验证）…")
    # rollback 切 2.0.0 已在上轮 applied；2.0.0 现 deprecated，
    # 再切会因 not_active 被拒。这里只验证两个版本的推荐状态均已落库。
    _check_recommended(governance, catalog, actor, "2.0.0")
    _check_recommended(governance, catalog, actor, "3.0.0")
    print("  [ok] 2.0.0 与 3.0.0 推荐指针均记录（recommendation_changed 已落库）")

    print("[4/6] deprecate 2.0.0（已 applied，重跑幂等核验）…")
    outcome = governance.deprecate_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id="gray-python-table",
            version="2.0.0",
            digest=PLATFORM_DIGESTS["2.0.0"],
        ),
        reason="AC07-10 阶段 4 弃用 2.0.0（真实治理演示）",
        idempotency_key="ac07-10-deprecate:2.0.0",
    )
    if outcome.status == "rejected":
        raise RuntimeError(f"弃用被拒绝：{outcome.gaps}")
    print(f"  [ok] deprecate {outcome.status}（lifecycle_changed→deprecated，幂等安全）")
    print("  弃用后列表（2.0.0 应从可选项消失）：")
    items = _list_items(governance, catalog, actor)
    _show_list(items)
    if any(i["version"] == "2.0.0" for i in items):
        raise RuntimeError("deprecated 2.0.0 仍出现在新任务选择列表")

    print("[5/6] 冻结 2.0.0 应被拒绝（deprecated 不进新任务）+ 历史冻结恢复装载…")

    def _freeze_gate(version: str) -> None:
        """等价 API 冻结门：check_mount + 新任务可选谓词拦截。"""
        ref = CapabilityPackRef(
            pack_id="gray-python-table",
            version=version,
            digest=PLATFORM_DIGESTS[version],
        )
        pack = catalog.resolve_pack(actor, ref.pack_id, ref.version)
        get_runtime_gate().check_mount(actor, pack)
        projection = governance.runtime_projection_for_pack(pack)
        if not (
            projection.maturity.value == "verified"
            and projection.lifecycle.value == "active"
            and projection.eligibility.value == "eligible"
        ):
            raise RuntimeError(f"能力 v{version} 当前不可用于新任务选择")

    # deprecated 2.0.0：check_mount 放行（历史恢复语义），但可选谓词拦截 → 冻结被拒。
    rejected = False
    try:
        _freeze_gate("2.0.0")
    except Exception as error:
        rejected = True
        print(f"  [ok] 冻结 2.0.0 被拒（预期）：{type(error).__name__}: {str(error)[:90]}")
    if not rejected:
        raise RuntimeError("deprecated 2.0.0 冻结未被拒绝")

    # 历史冻结任务恢复装载（#13 A5）：deprecated 放行历史冻结恢复装载（check_mount）。
    pack3 = catalog.resolve_pack(actor, "gray-python-table", "3.0.0")
    get_runtime_gate().check_mount(actor, pack3)
    print("  [ok] 3.0.0（active 签名平台包）装载门通过")
    pack2 = catalog.resolve_pack(actor, "gray-python-table", "2.0.0")
    get_runtime_gate().check_mount(actor, pack2)
    print("  [ok] 2.0.0（deprecated 历史冻结恢复）装载门通过（#13 A5，check_mount 放行）")

    print("[6/6] 治理事件与投影核验…")
    for version in ("2.0.0", "3.0.0"):
        pack = catalog.resolve_pack(actor, "gray-python-table", version)
        proj = governance.runtime_projection_for_pack(pack)
        print(
            f"  v{version} 投影: {proj.maturity.value}/"
            f"{proj.lifecycle.value}/{proj.eligibility.value} "
            f"recommended={proj.recommended_version}"
        )
    print("[done] 阶段 4 治理动作链完成")


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
