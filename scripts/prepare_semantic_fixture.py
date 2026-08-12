#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从本地私有文本样本生成可提交的脱敏 fixture。

只做操作者明确声明的逐字替换，并在输出后检查原字面量已消失；复杂二进制格式必须另走
人工合成或专用工具流程。
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "semantic_harness"
SUPPORTED_SUFFIXES = {".csv", ".tsv", ".json", ".jsonl", ".txt", ".md", ".markdown"}


def _ensure_within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    resolved_root = root.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"{label} 必须位于 {resolved_root}")
    return resolved


def _validate_text_format(text: str, suffix: str) -> None:
    if suffix == ".json":
        json.loads(text)
    elif suffix == ".jsonl":
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"JSONL 第 {line_number} 行无效：{exc}") from exc
    elif suffix in {".csv", ".tsv"}:
        delimiter = "," if suffix == ".csv" else "\t"
        list(csv.reader(text.splitlines(), delimiter=delimiter))


def _parse_replacements(items: Iterable[str]) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"替换规则必须是 OLD=NEW：{item}")
        old, new = item.split("=", 1)
        if not old:
            raise ValueError("替换规则的 OLD 不得为空")
        if old == new:
            raise ValueError(f"替换前后不得相同：{old}")
        replacements[old] = new
    if not replacements:
        raise ValueError("至少需要一条 --replace 规则")
    return replacements


def prepare_fixture(
    *,
    source: Path,
    output: Path,
    replacements: Mapping[str, str],
    confirmed: bool,
    fixture_root: Path = DEFAULT_FIXTURE_ROOT,
    overwrite: bool = False,
) -> Path:
    """生成脱敏文本和旁路清单，返回清单路径。"""

    if not confirmed:
        raise ValueError("必须显式确认已经人工复核脱敏结果")

    private_root = fixture_root / "private"
    public_root = fixture_root / "public"
    source_path = _ensure_within(source, private_root, "source")
    output_path = _ensure_within(output, public_root, "output")
    if source_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"暂不支持二进制或复杂格式：{source_path.suffix}")
    if output_path.suffix.lower() != source_path.suffix.lower():
        raise ValueError("脱敏流程不得顺便改变文件格式")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not replacements:
        raise ValueError("至少需要一条替换规则")

    manifest_path = output_path.with_suffix(output_path.suffix + ".fixture.json")
    if not overwrite and (output_path.exists() or manifest_path.exists()):
        raise FileExistsError("输出或 fixture 清单已存在；如确认覆盖请使用 --overwrite")

    text = source_path.read_text(encoding="utf-8")
    missing = [old for old in replacements if old not in text]
    if missing:
        raise ValueError(f"源文件未命中 {len(missing)} 条替换规则")
    sanitized = text
    for old, new in replacements.items():
        if not old or old == new:
            raise ValueError("替换规则必须包含不同的非空 OLD/NEW")
        sanitized = sanitized.replace(old, new)

    remaining = [old for old in replacements if old in sanitized]
    if remaining:
        raise ValueError(f"输出仍包含原始敏感字面量：{len(remaining)} 项")
    _validate_text_format(sanitized, source_path.suffix.lower())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(sanitized, encoding="utf-8", newline="\n")
    output_hash = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "format": source_path.suffix.lower().lstrip("."),
        "output_file": output_path.name,
        "output_sha256": output_hash,
        "replacement_count": len(replacements),
        "manual_review_confirmed": True,
        "manual_review_still_required_before_commit": True,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace", action="append", default=[], metavar="OLD=NEW")
    parser.add_argument("--confirm-deidentified", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    replacements = _parse_replacements(args.replace)
    manifest_path = prepare_fixture(
        source=args.source,
        output=args.output,
        replacements=replacements,
        confirmed=args.confirm_deidentified,
        overwrite=args.overwrite,
    )
    print(f"已生成脱敏 fixture：{manifest_path}")
    print("提交前仍须人工检查全部可见与隐藏内容。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
