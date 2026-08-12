# -*- coding: utf-8 -*-
"""HTTP 安全预检：SSRF 防护（plan.md 第 8.1 节 / Phase 2 Task 8）。

设计原则：
- 每次请求和每次重定向都重新校验目标 URL（Task 9 的 connector 在重定向每跳调用本模块）
- 只允许 HTTP/HTTPS
- 拒绝 URL userinfo（嵌入式凭证，易泄漏到日志）
- 解析 host 全部 A/AAAA 记录，逐个校验（防 DNS 绕过）
- 硬黑名单（不可放行）：loopback / link-local / multicast / reserved / unspecified / 云元数据
- 私网（含 CGN）默认拒绝；allow_private=True 时放行（管理员白名单场景）

用标准库 ipaddress + urllib.parse + socket，不引入新依赖。
DNS rebinding 的连接级 IP pin 由 Task 9 的 connector 在 transport 层处理；
本模块只做请求前的目标校验，返回解析到的 IP 供 Task 9 pin。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


class SsrfError(ValueError):
    """SSRF 防护拒绝。"""


# 云元数据端点（硬黑名单，不可放行）
# 169.254.169.254：AWS / Azure / GCP / 阿里云 IPv4
# fd00:ec2::254：AWS IPv6
# 100.100.100.200：阿里云元数据
_CLOUD_METADATA_IPS: frozenset[str] = frozenset({
    "169.254.169.254",
    "fd00:ec2::254",
    "100.100.100.200",
})

# CGN 网段 100.64.0.0/10（运营商级 NAT）-- Python ipaddress.is_private 未覆盖，
# 但其属于内网性质，归入私网判定（allow_private 可放行）
_CGN_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# Clash 等透明代理的 Fake-IP 默认使用 RFC 2544 基准测试网段。只有调用方同时冻结可信
# HTTPS 域名时才允许该网段，不能把它当成普通私网白名单。
_PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")

# 允许的 scheme
_ALLOWED_SCHEMES = frozenset({"http", "https"})


HostResolver = Callable[[str], List[str]]
"""DNS 解析器：host -> IP 字符串列表。可注入便于测试。"""


def default_resolver(host: str) -> List[str]:
    """用 socket.getaddrinfo 解析 host 到去重 IP 列表（保留出现顺序）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    seen: List[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.append(ip)
    return seen


def _is_ip_literal(host: str) -> bool:
    """host 是否为 IP 字面量（IPv4 或 IPv6，urlsplit 已去方括号）。"""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _hard_blacklist_category(ip_str: str) -> Optional[str]:
    """返回硬黑名单类别（不可放行），None 表示非硬黑名单。

    硬黑名单：cloud_metadata / loopback / link_local / multicast /
    unspecified / reserved / invalid
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return "invalid"
    # 云元数据优先报错（比 link_local 更精确）
    if ip_str in _CLOUD_METADATA_IPS:
        return "cloud_metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_reserved:
        return "reserved"
    return None


def _is_private_ip(ip_str: str) -> bool:
    """是否私网或 CGN（allow_private=True 可放行）。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.is_private:
        return True
    if ip.version == 4 and ip in _CGN_NETWORK:
        return True
    return False


@dataclass(frozen=True)
class ValidatedTarget:
    """SSRF 校验通过的目标。ips 供 Task 9 做 DNS rebinding 防护 pin。"""
    url: str
    scheme: str
    host: str            # urlsplit hostname（小写，已去 IPv6 方括号）
    port: int
    ips: Tuple[str, ...]  # DNS 解析到的全部 IP（IP 字面量时仅一个）


def validate_http_target(
    url: str,
    *,
    allow_private: bool = False,
    allow_proxy_fake_ip: bool = False,
    resolver: Optional[HostResolver] = None,
) -> ValidatedTarget:
    """校验 HTTP 目标 URL。

    通过返回 ValidatedTarget；违反任何规则抛 SsrfError。
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SsrfError(
            f"不允许的 scheme: {scheme or '空'}（仅允许 http/https）"
        )
    if parts.username or parts.password:
        raise SsrfError("URL 不允许嵌入用户名密码（userinfo）")

    host = parts.hostname
    if not host:
        raise SsrfError("URL 缺少 host")
    host = host.lower()

    try:
        port = parts.port
    except ValueError as exc:
        raise SsrfError(f"URL 端口非法: {parts.netloc}") from exc
    port = port or (443 if scheme == "https" else 80)

    # IP 字面量直接校验；域名解析全部 A/AAAA
    if _is_ip_literal(host):
        ips: List[str] = [host]
    else:
        resolver_fn = resolver or default_resolver
        ips = resolver_fn(host)
        if not ips:
            raise SsrfError(f"DNS 解析失败: {host}")

    # 硬黑名单逐个校验
    for ip in ips:
        category = _hard_blacklist_category(ip)
        if category is not None:
            raise SsrfError(f"目标 IP {ip} 命中黑名单: {category}")

    # 私网默认拒绝
    if not allow_private:
        for ip in ips:
            parsed_ip = ipaddress.ip_address(ip)
            is_allowed_proxy_fake_ip = (
                allow_proxy_fake_ip
                and parsed_ip.version == 4
                and parsed_ip in _PROXY_FAKE_IP_NETWORK
            )
            if _is_private_ip(ip) and not is_allowed_proxy_fake_ip:
                raise SsrfError(
                    f"目标 IP {ip} 为私网地址（需 allow_private 放行）"
                )

    return ValidatedTarget(
        url=url, scheme=scheme, host=host, port=port, ips=tuple(ips)
    )


@dataclass
class HttpSecurityGuard:
    """SSRF 守卫：封装 allow_private/resolver 配置。

    Task 9 的 connector 在每次请求和每次重定向后调用 validate()；
    也可包装成 httpx event hook（request 事件每跳触发）。
    """
    allow_private: bool = False
    private_host_allowlist: Tuple[str, ...] = ()
    proxy_fake_ip_host_allowlist: Tuple[str, ...] = ()
    resolver: Optional[HostResolver] = None

    def validate(self, url: str) -> ValidatedTarget:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        allow_private = self.allow_private or host in {
            item.strip().lower() for item in self.private_host_allowlist if item.strip()
        }
        allow_proxy_fake_ip = parts.scheme.lower() == "https" and host in {
            item.strip().lower()
            for item in self.proxy_fake_ip_host_allowlist
            if item.strip()
        }
        return validate_http_target(
            url,
            allow_private=allow_private,
            allow_proxy_fake_ip=allow_proxy_fake_ip,
            resolver=self.resolver,
        )


__all__ = [
    "HostResolver",
    "HttpSecurityGuard",
    "SsrfError",
    "ValidatedTarget",
    "default_resolver",
    "validate_http_target",
]
