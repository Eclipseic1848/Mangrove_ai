# -*- coding: utf-8 -*-
"""检查受版本控制的文本文件能否严格按 UTF-8 解码。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def _tracked_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def _binary_attribute_paths(root: Path, paths: list[Path]) -> set[Path]:
    relative = [path.relative_to(root).as_posix() for path in paths]
    result = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "text"],
        cwd=root,
        input=b"\0".join(item.encode("utf-8") for item in relative) + b"\0",
        capture_output=True,
        check=True,
    )
    fields = [field for field in result.stdout.split(b"\0") if field]
    return {
        root / fields[index].decode("utf-8")
        for index in range(0, len(fields), 3)
        if fields[index + 2] == b"unset"
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--paths", nargs="*")
    args = parser.parse_args()
    root = args.root.resolve()
    explicit_paths = args.paths is not None
    paths = [root / item for item in args.paths] if explicit_paths else _tracked_paths(root)
    binary_paths = set() if explicit_paths else _binary_attribute_paths(root, paths)

    failures: list[str] = []
    checked = 0
    for path in paths:
        try:
            if path in binary_paths:
                continue
            payload = path.read_bytes()
            # 与 Git 的默认二进制启发式一致；显式 -text 文件已在上面排除。
            if not explicit_paths and b"\0" in payload[:8192]:
                continue
            payload.decode("utf-8", errors="strict")
            checked += 1
        except (OSError, UnicodeError) as exc:
            failures.append(f"{path.relative_to(root)}: {exc}")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(json.dumps({"status": "passed", "checked_files": checked}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
