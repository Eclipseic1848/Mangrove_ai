# -*- coding: utf-8 -*-
"""确认快速 CI 依赖沿用权威依赖分组中的精确版本。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


REFERENCE_PATTERN = re.compile(r"^-(?P<kind>[cr])\s+(?P<target>\S+)$")


def _merge_pin(pins: dict[str, str], name: str, pin: str, location: str) -> None:
    existing = pins.get(name)
    if existing is not None and existing != pin:
        raise ValueError(f"{location}: 重复且冲突的依赖: {existing} != {pin}")
    pins[name] = pin


def _safe_reference(path: Path, target_text: str, line_number: int) -> Path:
    target = Path(target_text)
    root = path.parent.resolve()
    if target.is_absolute() or target.suffix.lower() != ".txt":
        raise ValueError(f"{path}:{line_number}: 依赖引用必须是同目录树内的相对 .txt 文件")
    resolved = (root / target).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{path}:{line_number}: 依赖引用禁止目录穿越") from exc
    if not resolved.is_file():
        raise ValueError(f"{path}:{line_number}: 依赖引用不存在: {target_text}")
    return resolved


def _pins(
    path: Path,
    *,
    allow_references: bool,
    stack: tuple[Path, ...] = (),
) -> dict[str, str]:
    path = path.resolve()
    if path in stack:
        raise ValueError(f"依赖引用形成循环: {path}")
    pins: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            reference = REFERENCE_PATTERN.fullmatch(line)
            if not allow_references or reference is None:
                raise ValueError(
                    f"{path}:{line_number}: 权威清单只允许精确版本或安全的 -c/-r 引用: {line}"
                )
            referenced_path = _safe_reference(
                path, reference.group("target"), line_number
            )
            referenced_pins = _pins(
                referenced_path,
                allow_references=True,
                stack=(*stack, path),
            )
            for name, pin in referenced_pins.items():
                _merge_pin(pins, name, pin, f"{path}:{line_number}")
            continue
        specifiers = list(requirement.specifier)
        if (
            requirement.url is not None
            or len(specifiers) != 1
            or specifiers[0].operator != "=="
            or "*" in specifiers[0].version
        ):
            raise ValueError(
                f"{path}:{line_number}: 权威清单只允许精确版本或安全的 -c/-r 引用: {line}"
            )
        name = canonicalize_name(requirement.name)
        _merge_pin(pins, name, line, f"{path}:{line_number}")
    return pins


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, action="append", required=True)
    parser.add_argument("--subset", type=Path, required=True)
    args = parser.parse_args()
    try:
        base: dict[str, str] = {}
        for base_path in args.base:
            for name, pin in _pins(base_path, allow_references=True).items():
                existing = base.get(name)
                if existing is not None and existing != pin:
                    raise ValueError(
                        f"跨分组版本冲突: {existing} != {pin}"
                    )
                base[name] = pin
        subset = _pins(args.subset, allow_references=False)
        mismatches = [
            pin for name, pin in subset.items() if base.get(name) != pin
        ]
    except (OSError, UnicodeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    if mismatches:
        for mismatch in mismatches:
            print(f"CI 依赖与权威分组不一致: {mismatch}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "passed", "matched_requirements": len(subset)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
