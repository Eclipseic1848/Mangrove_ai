"""工作台定时需求的星期别名与范围回归。"""
from datetime import datetime, timedelta

import pytest

from src.scheduler.cron import compute_next_run, cron_matches, parse_schedule


@pytest.mark.parametrize("weekday, expected", [("1-7", {0, 1, 2, 3, 4, 5, 6}), ("5-7", {4, 5, 6}), ("0,7", {6}), ("7", {6}), ("*/7", {6})])
def test_sunday_alias_in_ranges_and_steps(weekday, expected):
    monday = datetime(2026, 9, 21, 9, 30)
    expr = f"30 9 * * {weekday}"
    assert {day for day in range(7) if cron_matches(expr, monday + timedelta(days=day))} == expected
    schedule = parse_schedule("cron@" + expr)
    assert compute_next_run(schedule, monday - timedelta(minutes=1)) == monday + timedelta(days=min(expected))


@pytest.mark.parametrize("weekday", ["17", "70", "0-70", "7-1", "8"])
def test_weekday_alias_does_not_rewrite_invalid_values(weekday):
    with pytest.raises(ValueError):
        parse_schedule(f"cron@30 9 * * {weekday}")
