#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MediaCrawler CDP 反检测模式：本机浏览器探测 + verify 分支，单测。

运行：python scripts/test_mc_cdp_verify.py
不联网：monkeypatch shutil.which / Path.exists，不依赖本机是否真的装了浏览器。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.routes import config_routes as cr


def test_detect_local_browser_via_which():
    with patch.object(cr.shutil, "which", side_effect=lambda exe: r"C:\fake\chrome.exe" if exe == "chrome" else None):
        assert cr._detect_local_browser() == r"C:\fake\chrome.exe"


def test_detect_local_browser_via_path_candidate():
    with patch.object(cr.shutil, "which", return_value=None), \
         patch.object(cr.Path, "exists", lambda self: str(self) == cr._BROWSER_PATH_CANDIDATES[0]):
        assert cr._detect_local_browser() == cr._BROWSER_PATH_CANDIDATES[0]


def test_detect_local_browser_not_found():
    with patch.object(cr.shutil, "which", return_value=None), \
         patch.object(cr.Path, "exists", lambda self: False):
        assert cr._detect_local_browser() is None


def test_verify_target_mc_cdp_disabled():
    orig = cr.settings.mc_enable_cdp_mode
    cr.settings.mc_enable_cdp_mode = False
    try:
        detail = asyncio.run(cr._verify_target("mc_cdp"))
        assert "未启用" in detail
    finally:
        cr.settings.mc_enable_cdp_mode = orig


def test_verify_target_mc_cdp_enabled_found():
    orig = cr.settings.mc_enable_cdp_mode
    cr.settings.mc_enable_cdp_mode = True
    try:
        with patch.object(cr, "_detect_local_browser", return_value=r"C:\fake\chrome.exe"):
            detail = asyncio.run(cr._verify_target("mc_cdp"))
        assert "chrome.exe" in detail
    finally:
        cr.settings.mc_enable_cdp_mode = orig


def test_verify_target_mc_cdp_enabled_not_found():
    orig = cr.settings.mc_enable_cdp_mode
    cr.settings.mc_enable_cdp_mode = True
    try:
        with patch.object(cr, "_detect_local_browser", return_value=None):
            try:
                asyncio.run(cr._verify_target("mc_cdp"))
                raised = False
            except RuntimeError:
                raised = True
        assert raised
    finally:
        cr.settings.mc_enable_cdp_mode = orig


def main():
    tests = [
        test_detect_local_browser_via_which,
        test_detect_local_browser_via_path_candidate,
        test_detect_local_browser_not_found,
        test_verify_target_mc_cdp_disabled,
        test_verify_target_mc_cdp_enabled_found,
        test_verify_target_mc_cdp_enabled_not_found,
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
