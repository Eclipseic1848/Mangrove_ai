# -*- coding: utf-8 -*-
"""#16 AC07-11 阶段 3 复验：everything-mcp 平台快照独立 Layout 密码学复验。

从发布事件动态取签名事务参数（subject/signature/pubkey/run_id），不硬编码：
  1. 读 capability_governance_events 中最近一条 platform_published（2026.7.4）；
  2. 读对应平台验证运行 run_id（六步全绿 + 签名）；
  3. verify_local 独立 Layout 复验（主体哈希绑定主布局）。

用法：
  python scripts/ac07_11_verify_platform_signatures.py
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api.capability_governance_runtime import get_platform_signing_runtime
from src.capability_governance.oci_signing import OciSigningRequest
from src.config.settings import settings

PACK_ID = "gray-everything-mcp"
VERSION = "2026.7.4"


def _publication() -> dict:
    with sqlite3.connect(
        f"file:{settings.webui_db_path}?mode=ro", uri=True
    ) as con:
        row = con.execute(
            "SELECT payload_json FROM capability_governance_events "
            "WHERE event_type='platform_published' AND pack_id=? AND version=? "
            "ORDER BY occurred_at DESC, rowid DESC LIMIT 1",
            (PACK_ID, VERSION),
        ).fetchone()
        if row is None:
            raise RuntimeError("找不到 platform_published 事件")
        return json.loads(row[0])


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    publication = _publication()
    subject = publication["platform_digest"]
    signature = publication["signing_signature_digest"]
    pubkey = publication["signing_public_key_sha256"]
    run_id = publication["platform_validation_run_id"]
    print(
        f"[info] 发布事件：subject={subject[:24]}… sign={signature[:24]}… "
        f"pubkey={pubkey[:12]}… run={run_id[:20]}…"
    )
    runtime = get_platform_signing_runtime()
    output_layout = (
        Path(settings.capability_platform_oci_layout_path) / "signed" / run_id
    )
    request = OciSigningRequest(
        transaction_id=f"verify-ac07-11-{VERSION}",
        source_layout=Path(settings.capability_platform_oci_layout_path),
        source_reference=subject,
        output_layout=output_layout,
        output_reference=subject,
        registry_repository="mangrove/platform-snapshots",
        subject_digest=subject,
        public_key_path=Path(settings.capability_platform_signing_public_key),
    )
    try:
        verified = runtime.verify_local(request)
    except Exception as error:
        print(f"[FAIL] 复验异常: {error}")
        return 1
    checks = {
        "subject_digest": verified.subject_digest == subject,
        "signature_digest": verified.signature_digest == signature,
        "public_key": verified.public_key_sha256 == pubkey,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    print(
        f"[{status}] 独立 Layout 复验 "
        f"subject={verified.subject_digest[:16]}… "
        f"sign={verified.signature_digest[:16]}… "
        f"pubkey={verified.public_key_sha256[:12]}… checks={checks}"
    )
    print("ALL VERIFY PASS" if status == "PASS" else "VERIFY FAILED")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
