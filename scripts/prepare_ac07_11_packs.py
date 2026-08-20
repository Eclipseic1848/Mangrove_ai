# -*- coding: utf-8 -*-
"""#16 AC07-11 阶段 1：登记 everything-mcp 个人 draft（Owner=liyi）。

everything-mcp@2026.7.4 归档已存在于 OCI 布局（legacy 平台行，digest
dce5be51…，AC-06 冻结）。本脚本不复用 push（不重复打包），只做：
  1. 在线一致性备份 data/webui.db；
  2. 从 OCI index.json 解析 gray-everything-mcp:2026.7.4 的 manifest digest；
  3. 目录登记个人行（scope=PERSONAL, maturity=DRAFT, owner=liyi）——
     与平台 legacy 行（owner=__platform__）同版本不同 owner，唯一键不冲突；
  4. 治理 registered 事件（actor=liyi，幂等键唯一）；
  5. 核验：目录行 + 事件 + 投影。

用法：
  python scripts/prepare_ac07_11_packs.py            # dry-run 展示
  python scripts/prepare_ac07_11_packs.py --apply    # 写入生产库
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    CatalogActor,
    SqliteCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityGovernance,
    SqliteCapabilityGovernanceRepository,
)
from src.config.settings import settings
from src.conversation_steering import (
    CapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)

PACK_ID = "gray-everything-mcp"
VERSION = "2026.7.4"
OWNER_ID = "u_9505fd620899"  # liyi（super_admin）
OCI_REF = f"{PACK_ID}:{VERSION}"


def _consistent_backup(db_path: Path, backup: Path) -> None:
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _oci_digest() -> str:
    """从 OCI index.json 解析 everything-mcp:2026.7.4 的 manifest digest。"""
    index_path = Path(settings.capability_oci_layout_path) / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for manifest in index.get("manifests", []):
        annotations = manifest.get("annotations", {})
        if annotations.get("org.opencontainers.image.ref.name") == OCI_REF:
            return manifest["digest"]
    raise RuntimeError(f"OCI 布局中找不到 {OCI_REF}")


def _plan(owner_id: str, digest: str) -> dict:
    return {
        "pack_id": PACK_ID,
        "version": VERSION,
        "owner_id": owner_id,
        "digest": digest,
        "scope": "personal",
        "maturity": "draft",
        "action": "目录登记个人行 + registered 事件（不 push，复用现有 OCI 归档）",
    }


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="写入本机 OCI 目录和生产库")
    args = parser.parse_args()

    digest = _oci_digest()
    plan = _plan(OWNER_ID, digest)
    print("[plan]", json.dumps(plan, ensure_ascii=False, indent=2))

    db_path = Path(settings.webui_db_path).resolve()
    # 幂等预检：同 owner 同版本目录行已存在时停止（不重复登记）。
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        row = con.execute(
            "SELECT digest FROM capability_pack_versions "
            "WHERE owner_key=? AND pack_id=? AND version=?",
            (OWNER_ID, PACK_ID, VERSION),
        ).fetchone()
    if row is not None:
        print(f"[skip] 个人行已存在（digest={row[0][:24]}…），无需登记")
        return 0

    if not args.apply:
        print("[dry-run] 未加 --apply，不写入")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = Path("data/backups") / f"webui-before-ac07-11-stage1-{stamp}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    _consistent_backup(db_path, backup)
    print(f"[backup] {backup}")

    actor = CatalogActor(owner_id=OWNER_ID, role="user")
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(str(db_path))
    )
    catalog.register_pack(
        actor,
        CapabilityPack(
            pack_id=PACK_ID,
            version=VERSION,
            digest=digest,
            scope=ProcedureScope.PERSONAL,
            maturity=CapabilityMaturity.DRAFT,
            owner_id=OWNER_ID,
            manifest=(
                ("display_name", "Everything MCP（协议测试服务器）"),
                ("kind", "mcp_local"),
                ("purpose", "验证并调用本地 MCP 标准工具协议"),
            ),
            source_provenance=(
                "npm:@modelcontextprotocol/server-everything@2026.7.4",
                OCI_REF,
            ),
            created_by="ac07-11-preparation",
        ),
    )
    governance = CapabilityGovernance(
        catalog,
        SqliteCapabilityGovernanceRepository(str(db_path)),
    )
    event = governance.register_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id=PACK_ID,
            version=VERSION,
            digest=digest,
        ),
        idempotency_key=f"ac07-11-register:{VERSION}",
    )
    print(f"[ok] registered {event.event_type}（actor={event.actor_id}，scope={event.target.scope.value}）")

    # 核验
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        row = con.execute(
            "SELECT owner_key, scope, version, digest FROM capability_pack_versions "
            "WHERE pack_id=? ORDER BY owner_key",
            (PACK_ID,),
        ).fetchall()
    for r in row:
        print(f"  目录行: owner={r[0]} | {r[1]} | {r[2]} | {r[3][:24]}…")
    print("[done] 阶段 1 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
