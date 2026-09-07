"""
保留 SMTP 无邮件连接自检；外部报告投递在共享入口拒绝。
"""
from __future__ import annotations

import re
import smtplib
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
        return "历史 SMTP 连接开关关闭；平台不支持邮件投递"
    return "SMTP 未配置（需在 .env 设置 SMTP_HOST / SMTP_USER 等）"


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
    to: List[str], subject: str, body: str, attachments: Optional[List[str]] = None
) -> int:
    """保留旧调用接口，但确认标记也不能重新开启外部投递。"""
    reject_external_write()
