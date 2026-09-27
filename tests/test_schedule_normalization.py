"""定时计划回执使用平台规范格式，不改变执行时刻或猜测无效时间。"""
import pytest
from datetime import datetime

from src.conductor.nodes.schedule import schedule_node
from src.conductor.task_spec import TaskSpec
from src.scheduler.cron import normalize_schedule, parse_schedule, compute_next_run


@pytest.mark.asyncio
async def test_schedule_reply_canonicalizes_bare_cron_without_changing_time():
    result = await schedule_node({"task_spec": TaskSpec(intent="每周一三五采集", schedule=" 30   9 * * 1,3,5 ")})
    assert result["schedule_request"] == "cron@30 9 * * 1,3,5"
    assert "cron@30 9 * * 1,3,5" in result["reply"]


@pytest.mark.parametrize("prefix", ["", "CRON@ ", " cron @  "])
@pytest.mark.parametrize("expression,expected", [
    ("0 9 * * *", "2026-09-25T09:00"),
    ("30 9 * * 1,3,5", "2026-09-25T09:30"),
    ("*/15 9 * * *", "2026-09-25T09:00"),
    ("15,45 9-10 * * *", "2026-09-25T09:15"),
    ("0 9 * * 0", "2026-09-27T09:00"),
    ("0 9 * * 7", "2026-09-27T09:00"),
    ("0 9 * * 1-5", "2026-09-25T09:00"),
    ("5 0 1 * *", "2026-10-01T00:05"),
    ("0 9 * 9 *", "2026-09-25T09:00"),
    ("*/20 * * * *", "2026-09-25T09:00"),
])
def test_cron_normalization_preserves_expected_next_run(prefix, expression, expected):
    normalized = normalize_schedule(prefix + expression.replace(" ", "  "))
    assert normalized == "cron@" + expression
    assert compute_next_run(parse_schedule(normalized), datetime(2026, 9, 25, 8, 59)) == datetime.fromisoformat(expected)


@pytest.mark.parametrize("raw,expected", [
    (" ONCE@2026-09-27T09:00+08:00 ", "once@2026-09-27T09:00+08:00"),
    ("once@2026-09-27T01:00Z", "once@2026-09-27T01:00Z"),
    (" once@2026-09-27T09:00 ", "once@2026-09-27T09:00"),
    ("EVERY@ 0060", "every@60"), ("every@1", "every@1"), ("every@3600", "every@3600"),
])
def test_once_and_interval_preserve_execution_semantics(raw, expected):
    assert normalize_schedule(raw) == expected
    assert parse_schedule(raw) == parse_schedule(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", "every@0", "every@-1", "every@abc", "once@2026-02-30T09:00",
    "cron@60 9 * * *", "cron@0 24 * * *", "cron@0 9 * * 8", "cron@0 9 * *", "weekly@Monday"])
async def test_invalid_schedule_requests_clarification_without_guessing(raw):
    result = await schedule_node({"task_spec": TaskSpec(intent="安排采集", schedule=raw)})
    assert result["schedule_request"] is None
    assert result["needs_clarification"] is True
