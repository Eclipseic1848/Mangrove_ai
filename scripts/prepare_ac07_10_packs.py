# -*- coding: utf-8 -*-
"""#15 AC07-10 纵切面：注册真实 Python 表格 Tool 个人 draft 两版本。

2.0.0：输出增加 tool_version 字段（与 legacy 1.0.0 内容有真实差异）；
3.0.0：新增可选 ignore_empty_rows（跳过分组字段为空的行）。

必须显式 --apply 才写本机 OCI 目录与生产库；执行前自动备份 data/webui.db。
dry-run 只构建临时产物并输出计划，零写入。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.capability_adapters import CapabilityRuntimeManifest
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
)
from src.config.settings import settings
from src.conversation_steering import CapabilityMaturity, CapabilityPack, ProcedureScope

ARTIFACT_TYPE = "application/vnd.mangrove.capability.pack.v1"
LAYER_MEDIA_TYPE = "application/vnd.mangrove.capability.archive.v1+tar"
VERSIONS = ("2.0.0", "3.0.0")


def _tool_source(version: str) -> str:
    """真实工具源码；两版本内容有真实差异（功能等价微调）。"""
    ignore_block = (
        "    if bool(payload.get(\"ignore_empty_rows\", False)) "
        "and not str(row[group_field]).strip():\n        continue\n"
        if version == "3.0.0"
        else ""
    )
    return (
        "# -*- coding: utf-8 -*-\n"
        "import csv\n"
        "import io\n"
        "import json\n"
        "import sys\n"
        "\n"
        "if sys.argv[1:] == [\"--health\"]:\n"
        '    print("ok")\n'
        "    raise SystemExit(0)\n"
        "payload = json.loads(sys.argv[1])\n"
        'group_field = str(payload["group_field"])\n'
        'value_field = str(payload["value_field"])\n'
        'rows = list(csv.DictReader(io.StringIO(str(payload["csv"]))))\n'
        "totals = {}\n"
        "for row in rows:\n"
        + ignore_block
        + '    key = str(row[group_field]).strip()\n'
        "    totals[key] = totals.get(key, 0.0) + float(str(row[value_field]).replace(\",\", \"\"))\n"
        'print(json.dumps({"groups": totals, "row_count": len(rows), "tool_version": '
        f'"{version}"' "}, ensure_ascii=False, sort_keys=True))\n"
    )


def _run_tool(script: Path, payload: dict) -> dict:
    completed = subprocess.run(
        (sys.executable, str(script), json.dumps(payload, ensure_ascii=False)),
        cwd=script.parent,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"工具无法运行：{(completed.stderr or completed.stdout)[-1000:]}"
        )
    return json.loads(completed.stdout)


def _build_pack(root: Path, version: str) -> dict[str, str]:
    """构建一版本：真实源码 + manifest + 真实样例校验 + 确定性归档。"""
    root.mkdir(parents=True)
    script = root / "table_summary.py"
    script.write_text(_tool_source(version), encoding="utf-8")
    manifest = CapabilityRuntimeManifest.model_validate(
        {
            "schema_version": 1,
            "name": "python-table-summary",
            "version": version,
            "kind": "python",
            "purpose": "按指定字段对 CSV 行进行确定性分组汇总",
            "entrypoint": {
                "program": "python",
                "arguments": ["table_summary.py"],
                "timeout_seconds": 30,
            },
            "healthcheck": {
                "program": "python",
                "arguments": ["table_summary.py", "--health"],
                "timeout_seconds": 10,
            },
            "permissions": ["process:child", "network:none"],
        }
    )
    (root / "mangrove-capability.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    base_sample = {
        "csv": "部门,金额\n研发,10\n市场,20\n研发,5\n",
        "group_field": "部门",
        "value_field": "金额",
    }
    expected = {"groups": {"市场": 20.0, "研发": 15.0}, "row_count": 3, "tool_version": version}
    result = _run_tool(script, base_sample)
    if result != expected:
        raise RuntimeError(f"{version} 基础样例校验失败：{result}")
    if version == "3.0.0":
        # 3.0.0 专属微调的独立真实样例。
        sample = {
            "csv": "部门,金额\n研发,10\n,99\n市场,20\n",
            "group_field": "部门",
            "value_field": "金额",
            "ignore_empty_rows": True,
        }
        result = _run_tool(script, sample)
        if result != {
            "groups": {"市场": 20.0, "研发": 10.0},
            "row_count": 3,
            "tool_version": "3.0.0",
        }:
            raise RuntimeError(f"3.0.0 ignore_empty_rows 样例校验失败：{result}")
    # 归档必须用固定名 mangrove-capability.tar：既有 _expand_capability_archive
    # 只展开该名字（ac06 同约定），带版本后缀不会被物化解压，Sidecar 无法装载。
    archive = root.parent / "mangrove-capability.tar"
    _archive(root, archive)
    # 归档字节指纹（dry-run 展示用）；权威能力身份是 OCI push 后
    # descriptor 的 manifest digest。
    archive_sha256 = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    return {
        "pack_id": "gray-python-table",
        "version": version,
        "archive_sha256": archive_sha256,
        "display_name": "Python 表格汇总",
        "kind": "tool",
        "purpose": manifest.purpose,
        "source": "Mangrove 内置确定性 Tool",
        "archive": str(archive),
    }


def _archive(root: Path, destination: Path) -> None:
    """生成内容稳定且不携带链接的能力归档（mtime=0 确定性，同 AC-06）。"""
    with tarfile.open(destination, "w") as bundle:
        for source in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            relative = source.relative_to(root).as_posix()
            if source.is_symlink() and source.resolve(strict=True).is_dir():
                raise RuntimeError("能力包不得包含目录链接")
            if source.is_dir():
                info = tarfile.TarInfo(relative)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = 0
                bundle.addfile(info)
                continue
            resolved = source.resolve(strict=True)
            if root.resolve() not in resolved.parents:
                raise RuntimeError("能力包文件链接越过构建根目录")
            payload = resolved.read_bytes()
            info = tarfile.TarInfo(relative)
            info.size = len(payload)
            info.mode = 0o755 if relative.endswith(".py") else 0o644
            info.mtime = 0
            bundle.addfile(info, io.BytesIO(payload))


def _consistent_backup(db_path: Path, backup: Path) -> None:
    """SQLite 在线一致性备份（带走 WAL 内容，不用裸文件拷贝）。"""
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _remove_oci_tag(store: OrasOciLayoutStore, artifact_name: str, version: str) -> None:
    """从 OCI layout index.json 删除指定 tag 条目（--replace 重建用，先备份）。"""
    index_path = Path(store._layout_path) / "index.json"
    if not index_path.is_file():
        return
    backup = index_path.with_name("index.json.before-replace")
    backup.write_text(index_path.read_text(encoding="utf-8"), encoding="utf-8")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    kept = [
        manifest
        for manifest in index.get("manifests", [])
        if manifest.get("annotations", {}).get(
            "org.opencontainers.image.ref.name", ""
        )
        != f"{artifact_name}:{version}"
    ]
    if len(kept) != len(index.get("manifests", [])):
        index["manifests"] = kept
        index_path.write_text(
            json.dumps(index, indent=2), encoding="utf-8"
        )


def _apply_pack(
    pack: dict[str, str], *, owner_id: str, replace: bool = False
) -> dict[str, str]:
    """推送真实 OCI + 目录登记个人 draft + 治理 registered 事件。"""
    db_path = str(Path(settings.webui_db_path).resolve())
    store = OrasOciLayoutStore(
        settings.capability_oci_layout_path,
        layout_id="mangrove-capabilities",
    )
    if replace:
        # 归档名修正后的重建：先删旧 tag，避免 push_file「同版本不同内容」拒绝。
        _remove_oci_tag(store, pack["pack_id"], pack["version"])
    descriptor = store.push_file(
        Path(pack["archive"]),
        artifact_name=pack["pack_id"],
        version=pack["version"],
        artifact_type=ARTIFACT_TYPE,
        layer_media_type=LAYER_MEDIA_TYPE,
    )
    actor = CatalogActor(owner_id=owner_id, role="user")
    catalog = CapabilityCatalog(SqliteCapabilityCatalogRepository(db_path))
    if replace:
        # 目录行不可覆盖：删旧行后重建（事件流保留旧 registered 作失败留痕）。
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "DELETE FROM capability_pack_versions "
                "WHERE owner_key=? AND pack_id=? AND version=?",
                (owner_id, pack["pack_id"], pack["version"]),
            )
    catalog.register_pack(
        actor,
        CapabilityPack(
            pack_id=pack["pack_id"],
            version=pack["version"],
            digest=descriptor.digest,
            scope=ProcedureScope.PERSONAL,
            maturity=CapabilityMaturity.DRAFT,
            owner_id=owner_id,
            manifest=(
                ("display_name", pack["display_name"]),
                ("kind", pack["kind"]),
                ("purpose", pack["purpose"]),
            ),
            source_provenance=(pack["source"], descriptor.reference),
            created_by="ac07-10-preparation",
        ),
    )
    governance = CapabilityGovernance(
        catalog,
        SqliteCapabilityGovernanceRepository(db_path),
    )
    governance.register_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id=pack["pack_id"],
            version=pack["version"],
            digest=descriptor.digest,
        ),
        idempotency_key=(
            f"ac07-10-register:{pack['version']}"
            + (":v2" if replace else "")
        ),
    )
    return {**pack, "digest": descriptor.digest, "reference": descriptor.reference}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="写入本机 OCI 目录和生产库")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="归档名修正后的重建：删旧 OCI tag 与目录行再注册（事件流保留旧记录）",
    )
    parser.add_argument(
        "--owner",
        required=True,
        help="个人能力 Owner（真实用户 ID，如 liyi = u_9505fd620899）",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="mangrove-ac07-10-") as temporary:
        workspace = Path(temporary)
        packs = [_build_pack(workspace / version, version) for version in VERSIONS]
        plan = {
            "schema_version": 1,
            "owner_id": args.owner,
            "dry_run": not args.apply,
            "packs": [
                {
                    "pack_id": item["pack_id"],
                    "version": item["version"],
                    "archive_sha256": item["archive_sha256"],
                    "scope": "personal",
                    "maturity": "draft",
                    "display_name": item["display_name"],
                }
                for item in packs
            ],
        }
        if not args.apply:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        db_path = Path(settings.webui_db_path).resolve()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = db_path.parent / f"webui-before-ac07-10-{stamp}.db"
        _consistent_backup(db_path, backup)
        results = [
            _apply_pack(item, owner_id=args.owner, replace=args.replace)
            for item in packs
        ]
        plan["packs"] = [
            {**item, "reference": item.get("reference")} for item in results
        ]
        plan["backup"] = str(backup)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
