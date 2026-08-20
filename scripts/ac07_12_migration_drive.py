# -*- coding: utf-8 -*-
"""#17 AC07-12 阶段 1 驱动：生产库副本迁移演练（AC2）。

在副本上执行：备份 → 前向迁移 → 重复迁移（重放）→ 旧数据零改写核验 →
恢复演练。全程不触碰生产库本体。

用法：
  python scripts/ac07_12_migration_drive.py
"""
from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.capability_governance.sqlite_repository import (
    migrate_capability_governance,
)
from src.config.settings import settings

# 治理表（迁移应零改写的旧数据面）
_GOVERNANCE_TABLES = (
    "capability_pack_versions",
    "capability_governance_events",
    "capability_validation_runs",
    "capability_supply_chain_evidence",
    "capability_platform_validation_runs",
)


def _table_fingerprint(db: Path) -> dict[str, tuple[int, str]]:
    """每张治理表的 (行数, 全部内容 sha256)。"""
    result: dict[str, tuple[int, str]] = {}
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        for table in _GOVERNANCE_TABLES:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is None:
                continue
            rows = con.execute(
                f"SELECT group_concat(rowid||':'||payload_json,'|') FROM {table}"
            ).fetchone()
            count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            digest = hashlib.sha256(
                (rows[0] or "").encode("utf-8")
            ).hexdigest()
            result[table] = (count, digest)
    return result


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    source = Path(settings.webui_db_path).resolve()
    evidence = (
        PROJECT_ROOT / "data/capability-governance/evidence/ac07-12-migration"
    )
    if evidence.exists():
        shutil.rmtree(evidence, ignore_errors=True)
    evidence.mkdir(parents=True, exist_ok=True)
    replica = evidence / "replica.db"
    backup = evidence / "replica-before-migration.db"

    print("[1/6] 复制生产库到演练副本…")
    shutil.copy2(source, replica)
    print(f"  [ok] 副本 {replica}（{replica.stat().st_size} 字节）")

    print("[2/6] 迁移前指纹基线…")
    before = _table_fingerprint(replica)
    for table, (count, digest) in before.items():
        print(f"  {table}: {count} 行 | {digest[:16]}…")

    print("[3/6] 前向迁移（副本）…")
    made_backup = migrate_capability_governance(replica, backup)
    print(f"  [ok] 迁移完成；恢复点备份 {made_backup}")

    print("[4/6] 重复迁移（重放）…")
    replay_backup = migrate_capability_governance(replica, backup)
    if replay_backup != made_backup:
        raise RuntimeError("重复迁移返回了不同备份路径（恢复点被覆盖）")
    print("  [ok] 重放返回同一恢复点（不覆盖，幂等）")

    print("[5/6] 旧数据零改写核验…")
    after = _table_fingerprint(replica)
    changed = False
    for table in before:
        if before[table] != after.get(table):
            changed = True
            print(f"  [FAIL] {table} 指纹变化：{before[table]} -> {after.get(table)}")
    if changed:
        raise RuntimeError("迁移改写旧数据（零改写要求失败）")
    print("  [ok] 全部治理表行数与内容指纹一致（零改写）")

    print("[6/6] 恢复演练（用恢复点备份还原副本）…")
    restored = evidence / "restored.db"
    with sqlite3.connect(backup, timeout=30) as source_con:
        with sqlite3.connect(restored, timeout=30) as dest_con:
            source_con.backup(dest_con)
    restored_fp = _table_fingerprint(restored)
    for table in before:
        if before[table] != restored_fp.get(table):
            raise RuntimeError(f"恢复后 {table} 与迁移前不一致")
    print("  [ok] 恢复后指纹与迁移前完全一致（恢复演练通过）")
    print("[done] 阶段 1 迁移演练完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
