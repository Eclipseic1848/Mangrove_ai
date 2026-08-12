#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""邮件连接器联网验证（真实发送一封测试邮件）。

用法：
    python scripts/verify_email.py                # 发给 SMTP_USER 自己（推荐自测）
    python scripts/verify_email.py to@example.com # 发给指定收件人

前置：在 .env 配好 SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_USE_SSL。
凭证只读自 .env，不在命令行传递；本脚本如实打印发送结果。
"""
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings
from src.conductor.email_sender import is_email_configured, parse_recipients, send_report


def main():
    if not is_email_configured():
        print("❌ SMTP 未配置：请先在 .env 填好 SMTP_HOST / SMTP_USER / SMTP_PASSWORD 等。")
        sys.exit(1)

    # 收件人：命令行参数优先，否则发给自己（SMTP_USER）
    arg = sys.argv[1] if len(sys.argv) > 1 else (settings.smtp_from or settings.smtp_user)
    to = parse_recipients(arg)
    if not to:
        print(f"❌ 收件人无效：{arg!r}")
        sys.exit(1)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = f"【Mangrove 邮件连接器自测】{now}"
    body = (
        "这是 Mangrove（红树林）邮件连接器的联网自测邮件。\n\n"
        f"发送时间：{now}\n"
        f"SMTP 服务器：{settings.smtp_host}:{settings.smtp_port}"
        f"（{'SSL' if settings.smtp_use_ssl else 'STARTTLS'}）\n\n"
        "若你收到本邮件，说明 SMTP 配置正确，报告邮件发送功能可用。"
    )
    print(f"准备发送 → {', '.join(to)}")
    print(f"  via {settings.smtp_host}:{settings.smtp_port} ({'SSL' if settings.smtp_use_ssl else 'STARTTLS'})")
    try:
        n = send_report(to, subject, body)
        print(f"✅ 已发送成功（{n} 位收件人）。请到收件箱/垃圾箱确认是否收到。")
    except Exception as e:
        print(f"❌ 发送失败：{type(e).__name__}: {e}")
        print("  常见原因：授权码错误（QQ/163 用授权码而非登录密码）、端口与 SSL 不匹配"
              "（465→SSL=True，587→SSL=False）、服务器未开启 SMTP 服务、网络/代理拦截。")
        sys.exit(1)


if __name__ == "__main__":
    main()
