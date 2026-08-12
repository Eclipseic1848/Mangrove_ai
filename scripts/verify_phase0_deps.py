#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 0 依赖 PoC 验证（plan Phase 0：选型并验证解析依赖在目标 Python 下可用）。

16A 决策：由我跑。13B 决策：目标 Python 3.13。
本脚本先在当前解释器上探测各解析依赖的可用性与版本，输出兼容性清单。
3.13 装好后应复跑一次比对。

用法：
    python scripts/verify_phase0_deps.py
    E:\\python3.13\\python.exe scripts/verify_phase0_deps.py
"""
from __future__ import annotations

import importlib
import platform
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 依赖探测清单：模块名 -> (用途, 首版必需)
DEPS = [
    # 已在 requirements.txt 的核心依赖
    ("pydantic", "契约模型（Pydantic v2）", True),
    ("pandas", "表格处理", True),
    ("pyarrow", "Parquet 读写", True),
    ("openpyxl", "Excel 读写", True),
    ("sqlalchemy", "数据库 ORM/连接", True),
    ("pymysql", "MySQL 连接器", True),
    ("langgraph", "数据准备新图编排", True),
    # 待引入的解析依赖（Phase 2-4 用，Phase 0 验证可用性）
    ("pdfplumber", "PDF 数字文本/表格解析", False),
    ("pypdf", "PDF 基础解析（轻量备选）", False),
    ("docx", "DOCX 解析（python-docx）", False),
    ("psycopg2", "PostgreSQL 连接器", False),
    ("bs4", "HTML 解析（beautifulsoup4）", False),
    ("lxml", "XML/HTML 解析后端", False),
    # 媒体相关（Phase 4）
    ("ffmpeg", "音视频元数据（ffmpeg-python）", False),
]


def probe(module_name: str) -> dict:
    """探测单个模块：可用性、版本、路径。"""
    try:
        m = importlib.import_module(module_name)
        version = getattr(m, "__version__", None) or getattr(m, "VERSION", "?")
        path = getattr(m, "__file__", "?")
        return {"ok": True, "version": str(version), "path": str(path)}
    except ImportError as e:
        return {"ok": False, "version": None, "path": None, "error": str(e)}
    except Exception as e:  # noqa: BLE001 某些模块 import 时有副作用异常
        return {"ok": False, "version": None, "path": None, "error": f"{type(e).__name__}: {e}"}


def main() -> int:
    print("=" * 60)
    print("Phase 0 依赖 PoC 验证")
    print(f"Python: {sys.version.splitlines()[0]}  ({sys.executable})")
    print(f"Platform: {platform.platform()}")
    print("=" * 60)

    # 目标版本对齐检查（13B：目标 3.13）
    major, minor = sys.version_info[:2]
    target_ok = (major, minor) == (3, 13)
    target_note = "✓ 匹配目标 3.13" if target_ok else f"⚠ 当前 {major}.{minor}，目标 3.13（13B 决策）"
    print(target_note)
    print("-" * 60)

    required_missing = []
    optional_missing = []

    for name, usage, required in DEPS:
        r = probe(name)
        flag = "✓" if r["ok"] else "✗"
        req_tag = "[必需]" if required else "[可选]"
        if r["ok"]:
            print(f"{flag} {req_tag} {name:<16} {r['version']:<12} {usage}")
        else:
            err = r.get("error", "")
            print(f"{flag} {req_tag} {name:<16} 未安装         {usage}  ({err})")
            if required:
                required_missing.append(name)
            else:
                optional_missing.append(name)

    print("-" * 60)
    # 验证核心契约可导入（Phase 0 关键产物）
    try:
        from src.data_prep.models import DataPrepTaskSpec, RawArtifact, Recipe, QualityReport  # noqa: F401

        print("✓ 核心契约导入成功: DataPrepTaskSpec/RawArtifact/Recipe/QualityReport")
    except Exception as e:  # noqa: BLE001
        print(f"✗ 核心契约导入失败: {e}")
        required_missing.append("src.data_prep.models")

    print("-" * 60)
    if required_missing:
        print(f"必需依赖缺失 {len(required_missing)} 项: {', '.join(required_missing)}")
        print("  -> 阻塞 Phase 1，需先: pip install " + " ".join(required_missing))
    else:
        print("必需依赖全部就绪。")

    if optional_missing:
        print(f"可选解析依赖缺失 {len(optional_missing)} 项: {', '.join(optional_missing)}")
        print("  -> 不阻塞 Phase 0/1（互联网链路）；Phase 2-4 对应格式实现前补装。")

    return 1 if required_missing else 0


if __name__ == "__main__":
    sys.exit(main())
