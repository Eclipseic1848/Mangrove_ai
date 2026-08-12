# -*- coding: utf-8 -*-
"""数据库主机校验（Phase 3 Task 3 安全组件）。

复用 http_security 的 DNS resolver 与云元数据硬黑名单；
DB 场景与 HTTP 场景策略不同：默认允许私网/loopback（因为主用例就是连自有库）。
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set, Tuple

from src.config.settings import settings
from src.connectors.http_security import (
    _CLOUD_METADATA_IPS,
    default_resolver,
)

# 平台自用 sqlite 数据库文件名黑名单（T8）
_PLATFORM_DB_NAMES = {"webui.db", "app.db", "scheduler.db", "checkpoints.sqlite"}

# 平台自用数据库文件的近似哈希特征（额外校验层）
_PLATFORM_DB_PATTERNS = {
    "webui.db",
    "app.db",
    "scheduler.db",
    "checkpoints.db",
    "checkpoints.sqlite",
    "mangrove.db",
}


@dataclass
class ValidatedDbTarget:
    host: str
    port: int
    resolved_ips: list = None


class SqlitePathError(ValueError):
    """sqlite 路径违反安全策略（绝对路径、穿越、平台自用库）。"""


def validate_db_host(
    host: str,
    port: int,
    *,
    allowlist: Optional[Set[str]] = None,
    allowed_ports: Optional[Set[int]] = None,
    resolver=None,
) -> ValidatedDbTarget:
    """校验数据库主机与端口。

    策略（与 HTTP 不同）：
    - 默认允许 loopback/私网
    - 云元数据 IP 硬黑名单不可放行
    - 端口必须命中白名单（默认 3306,5432）
    - allowlist 非空时仅放行清单内主机
    """
    _resolver = resolver or default_resolver
    _allowlist = allowlist or set()
    _allowed_ports = allowed_ports or _parse_ports(settings.data_prep_db_allowed_ports)

    if _allowlist and host not in _allowlist:
        raise ValueError(
            f"主机 '{host}' 不在数据库主机白名单内（当前仅允许: {', '.join(sorted(_allowlist))})"
        )

    if port not in _allowed_ports:
        raise ValueError(
            f"数据库端口 {port} 不在允许范围内（允许: {_allowed_ports}）"
        )

    # IP 字面量直接校验
    try:
        ip_value = ipaddress.ip_address(host)
    except ValueError:
        ip_value = None  # 非 IP 字面量，走 DNS

    if ip_value is not None:
        _check_ip(ip_value)
        return ValidatedDbTarget(host=host, port=port, resolved_ips=[str(ip_value)])

    # 主机名 → DNS 解析
    ips = _resolver(host)
    if not ips:
        raise ValueError(f"无法解析数据库主机: {host}")
    for ip_str in ips:
        _check_ip(ipaddress.ip_address(ip_str))

    return ValidatedDbTarget(host=host, port=port, resolved_ips=ips)


def _check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """硬黑名单检测（云元数据三 IP 不可放行）。"""
    ip_str = str(ip)
    if ip_str in _CLOUD_METADATA_IPS:
        raise ValueError(f"拒绝连接到云元数据地址（硬黑名单）: {ip}")


def _parse_ports(raw: str) -> Set[int]:
    if not raw.strip():
        return set()
    return {int(p.strip()) for p in raw.split(",") if p.strip()}


def validate_sqlite_path(relpath: str, root: Optional[str] = None) -> Path:
    """校验 sqlite 相对路径安全。

    - 必须为相对路径，拒绝绝对路径与盘符路径
    - 拒绝 .. 穿越
    - 拒绝平台自用库文件名黑名单
    """
    import os

    p = Path(relpath)

    if p.is_absolute() or (len(relpath) >= 2 and relpath[1] == ":") or relpath.startswith(("\\", "/")):
        raise SqlitePathError(f"sqlite 路径必须为相对路径，拒绝: {relpath}")

    # .. 穿越检测
    for part in p.parts:
        if part == "..":
            raise SqlitePathError(f"sqlite 路径不允许上级目录穿越: {relpath}")

    # 平台自用库文件名黑名单
    if p.name.lower() in {n.lower() for n in _PLATFORM_DB_NAMES}:
        raise SqlitePathError(
            f"sqlite 文件 '{p.name}' 是平台自用数据库，禁止作为数据源访问。"
        )

    _root = root or settings.data_prep_db_sqlite_root
    return Path(_root) / p
