#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Slack 连接器联网验证（真实推送一条测试消息到 Slack 频道）。

用法：python scripts/verify_slack.py
前置：在 .env 配好 SLACK_WEBHOOK_URL（Slack App → Incoming Webhooks 创建）。
国内访问 hooks.slack.com 通常需要代理；默认 client 读取系统代理。
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor.slack_sender import is_slack_configured, send_report


def main():
    if not is_slack_configured():
        print("❌ Slack 未配置：请先在 .env 填好 SLACK_WEBHOOK_URL。")
        sys.exit(1)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"Mangrove Slack 连接器自测 {now}"
    body = "这是 Mangrove（红树林）Slack 连接器的联网自测消息。\n若你在频道里看到本条，说明 Slack 推送功能可用。"
    print(f"准备推送到 Slack 频道（{title}）...")
    try:
        asyncio.run(send_report(title, body))
        print("✅ 推送成功，请到对应 Slack 频道确认。")
    except Exception as e:
        print(f"❌ 推送失败：{type(e).__name__}: {e}")
        print("  常见原因：Webhook URL 错误/失效、网络无法访问 hooks.slack.com（需代理）。")
        sys.exit(1)


if __name__ == "__main__":
    main()
