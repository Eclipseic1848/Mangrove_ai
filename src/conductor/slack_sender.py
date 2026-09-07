"""
保留旧 Slack 调用接口；外部消息投递在共享入口拒绝。
"""
from __future__ import annotations

from src.config.settings import settings
from src.external_readonly import reject_external_write

# Slack 单条消息正文上限较大，但 mrkdwn 渲染过长体验差，做温和截断（可按需调整）。
_SLACK_MAX_CHARS = 3500


def is_slack_configured() -> bool:
    """Slack Webhook 是否已配置且启用（管理员可临时关闭而不必清空已保存的 Webhook）。"""
    if not settings.slack_enabled:
        return False
    return bool((settings.slack_webhook_url or "").strip())


def unavailable_reason() -> str:
    """历史配置不能重新开放外部消息投递。"""
    return "平台遵守外部只读边界，不支持 Slack 投递"


def build_message(title: str, body: str) -> str:
    """拼装 Slack mrkdwn 文本：标题 + 报告正文（截断）。"""
    text = f"*{title}*\n\n{body or ''}".strip()
    if len(text) > _SLACK_MAX_CHARS:
        text = text[:_SLACK_MAX_CHARS] + "\n…（已截断，完整报告见附件/下载）"
    return text


async def send_report(title: str, body: str) -> None:
    """保留旧调用接口，测试消息同样不能绕过外部只读边界。"""
    reject_external_write()
