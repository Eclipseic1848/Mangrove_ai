#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库巡检配置项「验证」按钮对应的 _verify_target library_dedup 分支，单测。

背景：VERIFY_TARGET（前端）此前没给这4个key配目标，点"验证"会因为
isSlowVerifyTarget(undefined) 抛异常而静默无反应；补全前端映射后，
这里验证后端 _verify_target("library_dedup") 分支本身的回读逻辑正确。

运行：python scripts/test_library_dedup_verify.py
不联网：只读 settings 当前值拼消息，无外部调用。
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.routes import config_routes as cr


def test_verify_target_library_dedup_disabled():
    orig = cr.settings.library_dedup_scan_enabled
    cr.settings.library_dedup_scan_enabled = False
    try:
        detail = asyncio.run(cr._verify_target("library_dedup"))
        assert "未启用" in detail
    finally:
        cr.settings.library_dedup_scan_enabled = orig


def test_verify_target_library_dedup_enabled():
    orig_enabled = cr.settings.library_dedup_scan_enabled
    orig_interval = cr.settings.library_dedup_scan_interval_hours
    orig_stale = cr.settings.library_stale_draft_days
    orig_max = cr.settings.library_dedup_scan_max_merges_per_run
    cr.settings.library_dedup_scan_enabled = True
    cr.settings.library_dedup_scan_interval_hours = 12
    cr.settings.library_stale_draft_days = 45
    cr.settings.library_dedup_scan_max_merges_per_run = 3
    try:
        detail = asyncio.run(cr._verify_target("library_dedup"))
        assert "12" in detail
        assert "45" in detail
        assert "3" in detail
        assert "已启用" in detail
    finally:
        cr.settings.library_dedup_scan_enabled = orig_enabled
        cr.settings.library_dedup_scan_interval_hours = orig_interval
        cr.settings.library_stale_draft_days = orig_stale
        cr.settings.library_dedup_scan_max_merges_per_run = orig_max


def main():
    tests = [
        test_verify_target_library_dedup_disabled,
        test_verify_target_library_dedup_enabled,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
    if failed:
        print(f"\n{failed}/{len(tests)} 失败")
        sys.exit(1)
    print(f"\n全部 {len(tests)} 项通过")


if __name__ == "__main__":
    main()
