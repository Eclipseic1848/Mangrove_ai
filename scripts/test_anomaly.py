#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""执行异常检测单元测试（detect_anomalies，确定性规则）。

运行：python scripts/test_anomaly.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor.anomaly import detect_anomalies
from src.conductor.task_spec import TaskSpec


def _state(**kw):
    base = {"task_spec": TaskSpec(intent="x", max_items=50),
            "cleaned_dataset": [{"content": "a"}] * 10,
            "collector_used": "firecrawl", "quality": {"score": 90, "passed": True},
            "trace": [{"node": "collect", "ms": 1000}]}
    base.update(kw)
    return base


def test_no_anomaly():
    assert detect_anomalies(_state()) == []


def test_zero_items():
    a = detect_anomalies(_state(cleaned_dataset=[]))
    assert any("0 条" in x for x in a), a


def test_too_few_items():
    # max_items=50，仅 5 条 < 10（20%）→ 偏少
    a = detect_anomalies(_state(cleaned_dataset=[{"content": "a"}] * 5))
    assert any("偏少" in x for x in a), a


def test_quality_failed():
    a = detect_anomalies(_state(quality={"score": 40, "passed": False}))
    assert any("质量评估未通过" in x for x in a), a


def test_error():
    a = detect_anomalies(_state(error="采集器全挂"))
    assert any("流程报错" in x for x in a), a


def test_fallback_collector():
    a = detect_anomalies(_state(collector_used="browser"))
    assert any("末端兜底" in x for x in a), a


def test_slow_node():
    a = detect_anomalies(_state(trace=[{"node": "collect", "ms": 95000}]))
    assert any("耗时异常" in x for x in a), a


def test_multiple_anomalies():
    a = detect_anomalies(_state(cleaned_dataset=[], quality={"score": 30, "passed": False},
                                collector_used="simple_http"))
    assert len(a) >= 3, a


def main():
    tests = [
        test_no_anomaly, test_zero_items, test_too_few_items, test_quality_failed,
        test_error, test_fallback_collector, test_slow_node, test_multiple_anomalies,
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
