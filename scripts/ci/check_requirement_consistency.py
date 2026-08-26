# -*- coding: utf-8 -*-
"""确认快速 CI 依赖沿用主依赖清单中的精确版本。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)(?P<extras>\[[^]]+\])?==(?P<version>[^;\s]+)$"
)


def _pins(path: Path, *, strict: bool) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = PIN_PATTERN.fullmatch(line)
        if match is None:
            if strict:
                raise ValueError(
                    f"{path}:{line_number}: CI 清单只允许精确版本: {line}"
                )
            name_match = re.match(r"^(?P<name>[A-Za-z0-9_.-]+)", line)
            if name_match is None:
                continue
            name = (
                name_match.group("name")
                .lower()
                .replace("_", "-")
                .replace(".", "-")
            )
            pins[name] = line
            continue
        name = match.group("name").lower().replace("_", "-").replace(".", "-")
        if name in pins and pins[name] != line:
            raise ValueError(f"{path}:{line_number}: 重复且冲突的依赖: {line}")
        pins[name] = line
    return pins


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--subset", type=Path, required=True)
    args = parser.parse_args()
    try:
        base = _pins(args.base, strict=False)
        subset = _pins(args.subset, strict=True)
        mismatches = [
            pin for name, pin in subset.items() if base.get(name) != pin
        ]
    except (OSError, UnicodeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    if mismatches:
        for mismatch in mismatches:
            print(f"CI 依赖与 requirements.txt 不一致: {mismatch}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "passed", "matched_requirements": len(subset)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
