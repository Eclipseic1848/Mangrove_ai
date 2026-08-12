#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Slack 连接器单元测试（离线：配置判断 / 消息拼装 / 推送-mock httpx）。

运行：python scripts/test_slack.py
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor import slack_sender as ss
from src.config import settings


def test_is_slack_configured():
    old = settings.slack_webhook_url
    try:
        settings.slack_webhook_url = ""
        assert not ss.is_slack_configured()
        settings.slack_webhook_url = "https://hooks.slack.com/services/x/y/z"
        assert ss.is_slack_configured()
    finally:
        settings.slack_webhook_url = old


def test_build_message():
    msg = ss.build_message("标题", "正文内容")
    assert msg.startswith("*标题*")
    assert "正文内容" in msg
    # 超长截断
    long = ss.build_message("T", "啊" * 5000)
    assert len(long) <= ss._SLACK_MAX_CHARS + 40
    assert "已截断" in long


class _FakeResp:
    def __init__(self, status=200, text="ok"):
        self.status_code = status
        self.text = text


class _FakeClient:
    captured = {}

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        _FakeClient.captured["url"] = url
        _FakeClient.captured["json"] = json
        return _FakeResp(200, "ok")


def test_send_report_ok():
    old = settings.slack_webhook_url
    real = ss.httpx.AsyncClient
    try:
        settings.slack_webhook_url = "https://hooks.slack.com/services/a/b/c"
        ss.httpx.AsyncClient = _FakeClient
        asyncio.run(ss.send_report("报告标题", "报告正文"))
        assert _FakeClient.captured["url"] == "https://hooks.slack.com/services/a/b/c"
        assert "*报告标题*" in _FakeClient.captured["json"]["text"]
    finally:
        ss.httpx.AsyncClient = real
        settings.slack_webhook_url = old


def test_send_report_guard():
    old = settings.slack_webhook_url
    try:
        settings.slack_webhook_url = ""
        try:
            asyncio.run(ss.send_report("t", "b"))
        except RuntimeError:
            pass
        else:
            raise AssertionError("未配置应抛 RuntimeError")
    finally:
        settings.slack_webhook_url = old


def test_outputformat_slack_registered():
    from src.conductor.task_spec import OutputFormat, TaskSpec
    spec = TaskSpec.from_draft({"intent": "x", "outputs": ["report_md", "slack"]})
    assert OutputFormat.SLACK in spec.outputs
    assert spec.needs_slack()


def main():
    tests = [
        test_is_slack_configured,
        test_build_message,
        test_send_report_ok,
        test_send_report_guard,
        test_outputformat_slack_registered,
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
