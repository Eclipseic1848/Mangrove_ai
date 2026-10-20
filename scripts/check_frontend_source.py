"""前端构建输入摘要门：未列入精确变更清单的差异一律拒绝。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys


ROOT_INPUTS = {
    "index.html", "package.json", "package-lock.json", "npm-shrinkwrap.json",
    "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb", ".browserslistrc",
    "browserslist",
}
TEXT_SUFFIXES = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts",
    ".json", ".jsonc", ".html", ".css", ".scss", ".sass", ".less",
    ".md", ".txt", ".yaml", ".yml", ".lock",
}
HASH = re.compile(r"[0-9a-f]{64}")


def input_path(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if any(part in {"dist", "node_modules"} for part in parts):
        return False
    return (
        len(parts) > 1 and parts[0] in {"src", "public"}
        or len(parts) == 1 and (
            name in ROOT_INPUTS
            or name == ".env" or name.startswith(".env.")
            or bool(re.fullmatch(r"tsconfig[^/]*\.json", name))
            or bool(re.fullmatch(r"(?:vite|postcss|tailwind)\.config\.[a-z]+", name))
        )
    )


def validate_path(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name or path.is_absolute() or str(path) != name
        or any(part in {".", ".."} for part in path.parts)
        or any(character in name for character in "\\:<>\"|?*\x00")
        or not input_path(name)
    ):
        raise ValueError(f"非法构建输入路径：{name!r}")


def snapshot(directory: Path) -> dict[str, str]:
    root = directory.resolve(strict=True)
    if not root.is_dir() or not (root / "src").is_dir():
        raise ValueError("前端根目录必须含 src 目录")
    for name in ("index.html", "package.json"):
        if not (root / name).is_file():
            raise ValueError(f"缺少构建输入：{name}")
    result = {}

    def add(path: Path) -> None:
        name = path.relative_to(root).as_posix()
        validate_path(name)
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"构建输入禁止链接或越界：{name}")
        raw = path.read_bytes()
        if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith(".env") or path.name in {".browserslistrc", "browserslist"}:
            raw = raw.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
        result[name] = hashlib.sha256(raw).hexdigest()

    for path in root.iterdir():
        if input_path(path.name) and (path.is_file() or path.is_symlink()):
            add(path)

    def unreadable(error: OSError) -> None:
        raise error

    for name in ("src", "public"):
        tree = root / name
        if not tree.exists():
            continue
        for current, directories, files in os.walk(tree, followlinks=False, onerror=unreadable):
            directories[:] = [item for item in directories if item not in {"dist", "node_modules"}]
            for path in [Path(current), *(Path(current) / item for item in directories)]:
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or not path.resolve().is_relative_to(root):
                    raise ValueError(f"构建输入目录禁止链接或越界：{path.relative_to(root).as_posix()}")
            for filename in files:
                add(Path(current) / filename)
    return dict(sorted(result.items()))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 中有重复键：{key}")
        result[key] = value
    return result


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("摘要和变更清单必须是 JSON 对象")
    for name in value:
        validate_path(name)
    return value


def check(baseline_path: Path, directory: Path, planned_path: Path) -> dict:
    baseline, planned = load(baseline_path), load(planned_path)
    if not {"index.html", "package.json"} <= baseline.keys():
        raise ValueError("基线缺少入口或包配置")
    for name, digest in baseline.items():
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ValueError(f"基线摘要无效：{name}")
    for name, change in planned.items():
        if not isinstance(change, dict) or set(change) != {"before", "after"}:
            raise ValueError(f"变更必须提供 before/after：{name}")
        if any(value is not None and (not isinstance(value, str) or not HASH.fullmatch(value)) for value in change.values()):
            raise ValueError(f"变更摘要无效：{name}")
        if change["before"] == change["after"]:
            raise ValueError(f"变更前后不能相同：{name}")
    current = snapshot(directory)
    actual = {
        name: {"before": baseline.get(name), "after": current.get(name)}
        for name in sorted(baseline.keys() | current.keys())
        if baseline.get(name) != current.get(name)
    }
    if actual != planned:
        paths = sorted(name for name in actual.keys() | planned.keys() if actual.get(name) != planned.get(name))
        raise ValueError("构建输入与精确变更清单不符：" + "、".join(paths))
    return {"passed": True, "checked_files": len(current), "planned_changes": len(planned)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("snapshot")
    capture.add_argument("directory", type=Path)
    capture.add_argument("--output", type=Path)
    verify = commands.add_parser("check")
    verify.add_argument("baseline", type=Path)
    verify.add_argument("directory", type=Path)
    verify.add_argument("planned", type=Path)
    args = parser.parse_args()
    try:
        value = snapshot(args.directory) if args.command == "snapshot" else check(args.baseline, args.directory, args.planned)
        output = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if args.command == "snapshot" and args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
        return 0
    except (OSError, ValueError) as error:
        print(f"前端源码门未通过：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
