"""明确否定的时间窗不能被重新解释为正向采集期限。"""
import socket

import pytest

from src.collectors._recency import parse_time_range


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("时效语义测试禁止网络请求")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.mark.parametrize("text", [
    "最新发布（不设近7天或近30天硬性时间窗，按本次检索候选中核实发布日期倒序取前3条）",
    "最新，不另设近7天或近30天的硬时间窗",
    "最新，不限制近7天时间窗口",
    "最新，不限近30天时间窗",
    "不设置近7天时间窗，按发布日期倒序",
    "最新（按核实发布日期倒序，不设近7天/近30天硬时间窗）",
    "最新（按核实发布日期倒序，不设近7天／近30天硬时间窗）",
])
def test_explicitly_negated_window_does_not_become_a_hard_limit(text):
    window = parse_time_range(text)
    assert window is None or (window.days is None and window.label is None)


@pytest.mark.parametrize("text", [
    "不设近7天或近30天硬性时间窗，范围为近90天",
    "近90天；不另设近7天硬性时间窗",
    "最新（不限制近7天时间窗口），但只采近90天",
    "不设近7天时间窗但只采近90天",
])
def test_separate_positive_window_survives_negated_window(text):
    window = parse_time_range(text)
    assert window.days == 90 and window.label == "year"


@pytest.mark.parametrize("text,days,label", [
    ("近7天", 7, "week"),
    ("近30天", 30, "month"),
    ("近90天", 90, "year"),
    ("近三天", 3, "week"),
    ("不要近7天数据", 7, "week"),
    ("不设条数限制，近7天", 7, "week"),
    ("不限制数量，近7天时间窗", 7, "week"),
    ("不限制条数但只采近7天时间窗", 7, "week"),
    ("最新", None, None),
])
def test_unrelated_or_ambiguous_negation_preserves_existing_behavior(text, days, label):
    window = parse_time_range(text)
    assert window.days == days and window.label == label
