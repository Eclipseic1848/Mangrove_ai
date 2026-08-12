#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""共享 frontmatter 解析器单元测试（skills/ 与 data/templates/ 共用）。

运行：python scripts/test_frontmatter.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.memory._frontmatter import FrontmatterError, parse_frontmatter


def test_no_frontmatter_returns_none():
    assert parse_frontmatter("# 纯文档\n没有 frontmatter") is None


def test_valid_frontmatter_parses():
    raw = "---\ntitle: 示例\nkeywords: [a, b]\n---\n正文内容"
    result = parse_frontmatter(raw)
    assert result is not None
    meta, body = result
    assert meta["title"] == "示例"
    assert meta["keywords"] == ["a", "b"]
    assert body == "正文内容"


def test_malformed_yaml_raises():
    raw = "---\ntitle: {unbalanced\n---\n正文"
    try:
        parse_frontmatter(raw)
        assert False, "应抛出 FrontmatterError"
    except FrontmatterError:
        pass


def test_empty_body_after_frontmatter():
    raw = "---\ntitle: 示例\n---\n   \n"
    meta, body = parse_frontmatter(raw)
    assert meta["title"] == "示例"
    assert body == ""  # 调用方负责判断空正文是否跳过


def main():
    tests = [
        test_no_frontmatter_returns_none,
        test_valid_frontmatter_parses,
        test_malformed_yaml_raises,
        test_empty_body_after_frontmatter,
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
