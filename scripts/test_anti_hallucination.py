#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Anti-hallucination prompt quality tests.
Verify that analysis/checker prompts contain required anti-fabrication instructions.

Usage: python scripts/test_anti_hallucination.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor.prompts import (
    ANALYZE_ARTICLE_SYSTEM,
    ANALYZE_SUMMARY_SYSTEM,
    ANALYZE_VOC_SYSTEM,
    CHECKER_SYSTEM,
)

# Required anti-hallucination rules per prompt:
# Each rule maps to keywords that must appear in the prompt text.
_ARTICLE_RULES = {
    "stick-to-source": ["忠于原文"],
    "half-vs-full-time": ["半场", "终场"],  # MUST distinguish half-time from full-time
    "no-fabrication": ["不杜撰"],
    "mark-uncertainty": ["不确定"],
}

_SUMMARY_RULES = {
    "stick-to-source": ["忠于原文", "不杜撰"],
    "mark-uncertainty": ["不确定"],
}

_VOC_RULES = {
    "stick-to-source": ["忠于原文", "不杜撰"],
    "mark-uncertainty": ["不确定"],
}

_CHECKER_RULES = {
    "stick-to-source": ["忠于原文"],
    "cross-check": ["数据支撑", "原文"],
    "detect-fabrication": ["杜撰", "臆测"],
}


def _check(name, text, rules):
    failures = []
    for rule, keywords in rules.items():
        hits = [k for k in keywords if k in text]
        if not hits:
            failures.append(f"  MISS [{rule}]: keywords {keywords} not found")
        else:
            print(f"  OK   [{rule}]: found {hits}")
    return failures


def test_article_prompt_anti_hallucination():
    """ARTICLE prompt must contain anti-hallucination instructions."""
    print(f"\n-- ANALYZE_ARTICLE_SYSTEM ({len(ANALYZE_ARTICLE_SYSTEM)} chars) --")
    failures = _check("ARTICLE", ANALYZE_ARTICLE_SYSTEM, _ARTICLE_RULES)
    assert not failures, "\n".join(failures)


def test_summary_prompt_anti_hallucination():
    """SUMMARY (fallback) prompt must contain anti-hallucination instructions."""
    print(f"\n-- ANALYZE_SUMMARY_SYSTEM ({len(ANALYZE_SUMMARY_SYSTEM)} chars) --")
    failures = _check("SUMMARY", ANALYZE_SUMMARY_SYSTEM, _SUMMARY_RULES)
    assert not failures, "\n".join(failures)


def test_voc_prompt_anti_hallucination():
    """VOC prompt must contain anti-hallucination instructions."""
    print(f"\n-- ANALYZE_VOC_SYSTEM ({len(ANALYZE_VOC_SYSTEM)} chars) --")
    failures = _check("VOC", ANALYZE_VOC_SYSTEM, _VOC_RULES)
    assert not failures, "\n".join(failures)


def test_checker_has_fact_check():
    """Checker prompt must cross-validate facts against source data."""
    print(f"\n-- CHECKER_SYSTEM ({len(CHECKER_SYSTEM)} chars) --")
    failures = _check("CHECKER", CHECKER_SYSTEM, _CHECKER_RULES)
    assert not failures, "\n".join(failures)


def test_article_distinguishes_half_and_full_time():
    """ARTICLE prompt MUST distinguish half-time score from full-time result.

    This is the regression test for the bug: LLM took half-time 1-0
    as the final result and fabricated an entire match report.
    """
    text = ANALYZE_ARTICLE_SYSTEM
    has_half = "半场" in text
    has_full = ("终场" in text) or ("最终" in text and "比分" in text)
    has_warning = any(w in text for w in ["混淆", "区分", "勿将"])
    print(f"\n-- Score distinction check --")
    print(f"  half-time: {has_half}, full-time: {has_full}, distinguish/warn: {has_warning}")
    assert has_half, "MUST mention half-time to prevent confusing partial scores"
    assert has_full or has_warning, "MUST mention full-time or warn against confusing partial scores"


def main():
    tests = [
        test_article_prompt_anti_hallucination,
        test_summary_prompt_anti_hallucination,
        test_voc_prompt_anti_hallucination,
        test_checker_has_fact_check,
        test_article_distinguishes_half_and_full_time,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}")
            print(str(e))
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
