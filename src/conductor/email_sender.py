"""
SMTP 连接检查与经过任务授权的报告发送。
"""
from __future__ import annotations

import re
import smtplib
import ssl
import mimetypes
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

from src.config.settings import settings
from src.external_readonly import reject_external_write

# 邮箱地址的宽松校验（够用即可，不追求 RFC 完整）
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_email_configured() -> bool:
    """SMTP 是否已配置且启用（主机 + 账号缺一不可；管理员可临时关闭而不必清空已保存的凭证）。"""
    if not settings.smtp_enabled:
        return False
    return bool((settings.smtp_host or "").strip() and (settings.smtp_user or "").strip())


def unavailable_reason() -> str:
    """区分"管理员临时关闭"和"从未配置"两种不可用原因，避免误导。"""
    if not settings.smtp_enabled:
        return "邮件发送已关闭，请管理员在平台配置的通知分类中启用"
    return "SMTP 未配置，请管理员在平台配置的通知分类中配置"


def parse_recipients(raw: Optional[str]) -> List[str]:
    """把 '逗号/分号/空格分隔' 的收件人串解析为去重的合法邮箱列表。"""
    if not raw:
        return []
    parts = re.split(r"[,;\s]+", raw.strip())
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if p and _EMAIL_RE.match(p) and p not in out:
            out.append(p)
    return out


def verify_connection() -> None:
    """连通性自检：连接 SMTP 并登录，但**不发送任何邮件**（无副作用）。失败抛异常由调用方处理。"""
    if not is_email_configured():
        raise RuntimeError(unavailable_reason())
    host, port = settings.smtp_host.strip(), int(settings.smtp_port)
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_password)
    else:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)


def send_report(
    to: List[str], subject: str, body: str, attachments: Optional[List[str]] = None,
    *, authorized: bool = False, before_send=None,
) -> int:
    """仅由任务通知服务授权；旧调用和历史批准标记默认仍拒绝。"""
    if not authorized:
        reject_external_write()
    if not to or len(to) > 20 or any(not _EMAIL_RE.fullmatch(address) or any(c in address for c in '\r\n,;<>') for address in to):
        raise ValueError("收件人地址无效")
    if any(c in subject for c in '\r\n'):
        raise ValueError("邮件主题不能包含换行")
    if not is_email_configured():
        raise ValueError(unavailable_reason())
    config = settings.model_copy()
    message = EmailMessage()
    message['From'] = config.smtp_from.strip() or config.smtp_user.strip()
    message['To'] = ', '.join(dict.fromkeys(to))
    message['Subject'] = subject
    message.set_content(body)
    total = 0
    for filename in attachments or []:
        path = Path(filename)
        total += path.stat().st_size
        if total > 20 * 1024 * 1024:
            raise ValueError("附件总大小超过 20 MB，请减少附件后重新选择发送")
        content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        main, sub = content_type.split('/', 1)
        message.add_attachment(path.read_bytes(), maintype=main, subtype=sub, filename=path.name)
    context = ssl.create_default_context()
    factory = smtplib.SMTP_SSL if config.smtp_use_ssl else smtplib.SMTP
    options = {'context': context} if config.smtp_use_ssl else {}
    if before_send:
        before_send()
    with factory(config.smtp_host.strip(), int(config.smtp_port), timeout=30, **options) as smtp:
        if not config.smtp_use_ssl:
            smtp.starttls(context=context)
        smtp.login(config.smtp_user, config.smtp_password)
        if before_send:
            before_send()
        refused = smtp.send_message(message, to_addrs=list(dict.fromkeys(to)))
        if refused:
            raise RuntimeError("部分收件人未被接受；请核对投递结果，不要直接重发全部收件人")
    return len(set(to))
