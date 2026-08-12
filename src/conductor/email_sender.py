"""
邮件连接器（期4）：把分析报告通过 SMTP 发送给收件人。

仅在用户经 HITL 确认后调用（见前端 / output 节点）——发邮件是外向敏感动作。
零第三方依赖，使用标准库 smtplib + email.message。SMTP 连接参数来自 settings（.env）。
"""
from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

from src.config.settings import settings

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
        return "邮件发送已被管理员停用（配置中心可重新启用）"
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
    """发送报告邮件，返回成功投递的收件人数。SMTP 失败时抛出异常由调用方处理。"""
    if not to:
        raise ValueError("收件人为空")
    if not is_email_configured():
        raise RuntimeError(unavailable_reason())

    sender = (settings.smtp_from or settings.smtp_user).strip()
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body or "（报告内容见附件）")

    # 附件（如 report.md / data.json）：按文件名以文本附上
    for path_str in attachments or []:
        path = Path(path_str)
        if not path.is_file():
            continue
        data = path.read_bytes()
        # 报告/数据均为文本，统一按 text 子类型附件发送
        subtype = "markdown" if path.suffix.lower() == ".md" else "plain"
        msg.add_attachment(
            data, maintype="text", subtype=subtype, filename=path.name
        )

    host, port = settings.smtp_host.strip(), int(settings.smtp_port)
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    return len(to)
