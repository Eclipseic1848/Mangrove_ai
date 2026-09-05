"""维护者本机首次初始化命令；不迁移数据库，不修改已有超级管理员。"""
from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
import warnings

from src.api.auth import hash_password
from src.api.store import WebUIStore
from src.database_migrations import SchemaNotCurrentError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="显式初始化首位超级管理员")
    parser.add_argument("--database", required=True, help="已显式迁移到当前版本的 WebUI 数据库")
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", default="")
    args = parser.parse_args(argv)
    username = args.username.strip()
    if len(username) < 2:
        parser.error("用户名至少 2 位")
    # 不接受命令行密码或管道回退，避免密码出现在进程参数、历史和回显中。
    if not sys.stdin.isatty():
        parser.error("请在交互终端运行，以隐藏输入管理员密码")
    try:
        store = WebUIStore(args.database)
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("管理员密码（至少 12 位）：")
            if len(password) < 12:
                parser.error("管理员密码至少 12 位")
            if password != getpass.getpass("再次输入密码："):
                parser.error("两次密码不一致")
        user = store.bootstrap_super_admin(username, hash_password(password), args.display_name)
    except getpass.GetPassWarning:
        parser.exit(1, "初始化失败：终端无法隐藏密码输入，请更换交互终端。\n")
    except sqlite3.IntegrityError:
        parser.exit(1, "初始化失败：用户名已存在，不会提升或覆盖已有账号。\n")
    except (SchemaNotCurrentError, ValueError) as exc:
        parser.exit(1, f"初始化失败：{exc}\n")
    print(f"超级管理员已初始化：{user['user_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
