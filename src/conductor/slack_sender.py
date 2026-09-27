"""
用户授权的 Slack 正文和文件发送；未授权旧调用仍拒绝。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import httpx

from src.config.settings import settings
from src.external_readonly import reject_external_write

# Slack 单条消息正文上限较大，但 mrkdwn 渲染过长体验差，做温和截断（可按需调整）。
_SLACK_MAX_CHARS = 3500


def is_slack_configured() -> bool:
    """Slack Webhook 是否已配置且启用（管理员可临时关闭而不必清空已保存的 Webhook）。"""
    if not settings.slack_enabled:
        return False
    return bool(settings.slack_webhook_url.strip() or (settings.slack_bot_token.strip() and settings.slack_channel_id.strip()))


def unavailable_reason() -> str:
    return "Slack 未启用或配置不完整，请在平台配置的通知分类中检查"


def build_message(title: str, body: str) -> str:
    """拼装 Slack mrkdwn 文本：标题 + 报告正文（截断）。"""
    text = f"*{title}*\n\n{body or ''}".strip()
    if len(text) > _SLACK_MAX_CHARS:
        text = text[:_SLACK_MAX_CHARS] + "\n…（已截断，完整报告见附件/下载）"
    return text


async def _api(client, method, payload, before_send=None, *, token=None):
    if before_send:
        before_send()
    response = await client.post('https://slack.com/api/' + method, json=payload,
                                headers={'Authorization': 'Bearer ' + (token if token is not None else settings.slack_bot_token.strip())})
    response.raise_for_status()
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError('Slack 拒绝请求，请检查权限和频道配置')
    return data


async def verify_connection() -> None:
    """Bot 模式只验证身份；Webhook 验证需要单独确认发送测试消息。"""
    if not settings.slack_enabled or not settings.slack_bot_token.strip():
        raise ValueError('请配置 Bot Token 后检查连接；Webhook 可在任务中选择发送正文验证')
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        await _api(client, 'auth.test', {})


async def send_report(title: str, body: str, attachments: list[str] | None = None, *, authorized: bool = False, before_send=None) -> None:
    if not authorized:
        reject_external_write()
    if not is_slack_configured():
        raise ValueError(unavailable_reason())
    # 一次发送冻结目标和凭据，热更新不能把在途文件改投其他频道。
    token, channel, webhook = settings.slack_bot_token.strip(), settings.slack_channel_id.strip(), settings.slack_webhook_url.strip()
    paths = [Path(path) for path in attachments or []]
    text = f'{title}\n\n{body}'.strip()
    if len(text) > 39000 or sum(path.stat().st_size for path in paths) > 20 * 1024 * 1024:
        raise ValueError('内容或附件过大，请减少发送内容')
    if paths and not (token and channel):
        raise ValueError('Slack 文件发送需要 Bot Token 和目标频道 ID，Webhook 不支持文件上传')
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        if paths:
            files = []
            for path in paths:
                result = await _api(client, 'files.getUploadURLExternal', {'filename': path.name, 'length': path.stat().st_size}, before_send, token=token)
                url = urlparse(result['upload_url'])
                # 上传地址来自 Slack API，仍只允许 Slack HTTPS 域名，且不转发 Bot Token。
                if url.scheme != 'https' or not url.hostname or not (url.hostname == 'slack.com' or url.hostname.endswith('.slack.com')) or url.username or url.password:
                    raise ValueError('Slack 返回了不允许的上传地址')
                if before_send:
                    before_send()
                response = await client.post(result['upload_url'], content=path.read_bytes())
                response.raise_for_status()
                files.append({'id': result['file_id'], 'title': path.name})
            await _api(client, 'files.completeUploadExternal', {'files': files, 'channel_id': channel, 'initial_comment': text}, before_send, token=token)
        elif token and channel:
            await _api(client, 'chat.postMessage', {'channel': channel, 'text': text}, before_send, token=token)
        else:
            url = urlparse(webhook)
            if url.scheme != 'https' or url.hostname not in {'hooks.slack.com', 'hooks.slack-gov.com'} or not url.path.startswith('/services/') or url.username or url.password:
                raise ValueError('请输入 Slack 官方 HTTPS Webhook 地址')
            if before_send:
                before_send()
            response = await client.post(webhook, json={'text': text})
            response.raise_for_status()
            if response.text.strip() != 'ok':
                raise RuntimeError('Slack 未确认接收消息')
