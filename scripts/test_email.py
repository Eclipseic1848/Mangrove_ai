#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""邮件连接器单元测试（离线：收件人解析 / 配置判断 / 邮件构造，mock SMTP 不真发）。

运行：python scripts/test_email.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor import email_sender as es
from src.config import settings


def test_parse_recipients():
    assert es.parse_recipients(None) == []
    assert es.parse_recipients("") == []
    # 逗号/分号/空格混合分隔 + 去重 + 过滤非法
    got = es.parse_recipients("a@x.com, b@y.com; a@x.com  not-an-email c@z.cn")
    assert got == ["a@x.com", "b@y.com", "c@z.cn"], got


def test_is_email_configured():
    old_h, old_u = settings.smtp_host, settings.smtp_user
    try:
        settings.smtp_host, settings.smtp_user = "", ""
        assert not es.is_email_configured()
        settings.smtp_host, settings.smtp_user = "smtp.x.com", ""
        assert not es.is_email_configured()      # 缺账号
        settings.smtp_host, settings.smtp_user = "smtp.x.com", "me@x.com"
        assert es.is_email_configured()
    finally:
        settings.smtp_host, settings.smtp_user = old_h, old_u


class _FakeSMTP:
    """捕获 send_message 的假 SMTP（上下文管理器）。"""
    sent = {}

    def __init__(self, host, port, timeout=None):
        _FakeSMTP.sent["host"] = host
        _FakeSMTP.sent["port"] = port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pwd):
        _FakeSMTP.sent["login"] = user

    def send_message(self, msg):
        _FakeSMTP.sent["msg"] = msg


def test_send_report_builds_message(monkeypatch_ssl=True):
    # 配置 + 替换 SMTP_SSL 为假实现，验证邮件被正确构造与“发送”
    old = (settings.smtp_host, settings.smtp_user, settings.smtp_password,
           settings.smtp_from, settings.smtp_use_ssl)
    real_ssl = es.smtplib.SMTP_SSL
    try:
        settings.smtp_host = "smtp.test.com"
        settings.smtp_user = "me@test.com"
        settings.smtp_password = "pwd"
        settings.smtp_from = ""
        settings.smtp_use_ssl = True
        es.smtplib.SMTP_SSL = _FakeSMTP

        n = es.send_report(["to1@x.com", "to2@y.com"], "测试主题", "正文内容")
        assert n == 2
        msg = _FakeSMTP.sent["msg"]
        assert msg["Subject"] == "测试主题"
        assert msg["From"] == "me@test.com"            # smtp_from 空 → 回退 smtp_user
        assert msg["To"] == "to1@x.com, to2@y.com"
        assert _FakeSMTP.sent["host"] == "smtp.test.com"
        assert _FakeSMTP.sent["login"] == "me@test.com"
    finally:
        es.smtplib.SMTP_SSL = real_ssl
        (settings.smtp_host, settings.smtp_user, settings.smtp_password,
         settings.smtp_from, settings.smtp_use_ssl) = old


def test_send_report_guards():
    # 无收件人 → 抛错
    try:
        es.send_report([], "s", "b")
    except ValueError:
        pass
    else:
        raise AssertionError("空收件人应抛 ValueError")


def test_outputformat_email_registered():
    from src.conductor.task_spec import OutputFormat, TaskSpec
    spec = TaskSpec.from_draft({"intent": "x", "outputs": ["report_md", "email"],
                               "email_to": "a@x.com"})
    assert OutputFormat.EMAIL in spec.outputs
    assert spec.needs_email()
    assert spec.email_to == "a@x.com"


def main():
    tests = [
        test_parse_recipients,
        test_is_email_configured,
        test_send_report_builds_message,
        test_send_report_guards,
        test_outputformat_email_registered,
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
