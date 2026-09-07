#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""教训库/巡检报告认证入口单测：本人库使用登录身份，巡检报告仍限管理员。

运行：python scripts/test_library_route_permissions.py
不起真实 HTTP 服务，直接内省路由函数签名里 Depends() 包的目标函数，
这是本项目既有测试风格（无 TestClient），比起 HTTP 层测试更快也不需要建用户/发 token。
"""
import inspect
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.auth import get_current_user, require_admin
from src.api.routes import lessons_routes, library_dedup_routes


def _depends_target(func, param_name: str):
    """取出某个路由函数指定参数上 Depends(...) 包的目标可调用对象。"""
    sig = inspect.signature(func)
    default = sig.parameters[param_name].default
    return default.dependency


def test_list_lessons_requires_authenticated_owner():
    assert _depends_target(lessons_routes.list_lessons, "user") is get_current_user


def test_remove_lesson_requires_authenticated_owner():
    # #117 允许本人管理原件；具体 Owner/平台删除权限由库层核验。
    assert _depends_target(lessons_routes.remove_lesson, "user") is get_current_user


def test_list_scan_log_requires_admin():
    assert _depends_target(library_dedup_routes.list_scan_log, "admin") is require_admin


def main():
    tests = [
        test_list_lessons_requires_authenticated_owner,
        test_remove_lesson_requires_authenticated_owner,
        test_list_scan_log_requires_admin,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
