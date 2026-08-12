"""
Slack 连接器（期4）：把分析报告推送到 Slack 频道（Incoming Webhook）。

仅在用户经 HITL 确认后调用（见前端 / output 节点）——外向推送是敏感动作。
频道由 Webhook URL 本身绑定，无需指定收件人。Webhook 地址来自 settings（.env）。
"""
from __future__ import annotations

import httpx

from src.config.settings import settings

# Slack 单条消息正文上限较大，但 mrkdwn 渲染过长体验差，做温和截断（可按需调整）。
_SLACK_MAX_CHARS = 3500


def is_slack_configured() -> bool:
    """Slack Webhook 是否已配置且启用（管理员可临时关闭而不必清空已保存的 Webhook）。"""
    if not settings.slack_enabled:
        return False
    return bool((settings.slack_webhook_url or "").strip())


def unavailable_reason() -> str:
    """区分"管理员临时关闭"和"从未配置"两种不可用原因，避免误导。"""
    if not settings.slack_enabled:
        return "Slack 通知已被管理员停用（配置中心可重新启用）"
    return "Slack 未配置（需在 .env 设置 SLACK_WEBHOOK_URL）"


def build_message(title: str, body: str) -> str:
    """拼装 Slack mrkdwn 文本：标题 + 报告正文（截断）。"""
    text = f"*{title}*\n\n{body or ''}".strip()
    if len(text) > _SLACK_MAX_CHARS:
        text = text[:_SLACK_MAX_CHARS] + "\n…（已截断，完整报告见附件/下载）"
    return text


async def send_report(title: str, body: str) -> None:
    """推送报告到 Slack 频道。失败时抛出异常由调用方处理。"""
    if not is_slack_configured():
        raise RuntimeError(unavailable_reason())
    payload = {"text": build_message(title, body)}
    # Slack Webhook 为公网地址；默认 client 读取系统代理（国内访问通常需代理）
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(settings.slack_webhook_url.strip(), json=payload)
    # Slack 成功返回 200 且响应体为 "ok"
    if resp.status_code != 200 or resp.text.strip().lower() != "ok":
        raise RuntimeError(f"Slack 返回异常：HTTP {resp.status_code} {resp.text[:200]}")
