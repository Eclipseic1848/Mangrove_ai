# -*- coding: utf-8 -*-
"""SSRF 防护测试（plan.md 第 8.1 节 / Phase 2 Task 8）。

覆盖：
- scheme 白名单（仅 http/https）
- userinfo 拒绝
- 硬黑名单：loopback / link-local / multicast / unspecified / reserved / 云元数据
- 私网（含 CGN）默认拒绝，allow_private=True 放行
- DNS 解析到危险 IP 拒绝（mock resolver）
- IPv6 / 自定义端口
- HttpSecurityGuard 封装
"""
from __future__ import annotations

import pytest

from src.connectors.http_security import (
    HttpSecurityGuard,
    SsrfError,
    default_resolver,
    validate_http_target,
)


# 用公网 IP 字面量避免真连 DNS（IP 字面量不走 resolver）
PUB = "8.8.8.8"
PUB6 = "2606:4700:4700::1111"  # Cloudflare DNS，公网全局单播


class TestScheme:
    def test_https_allowed(self):
        t = validate_http_target(f"https://{PUB}/path")
        assert t.scheme == "https"
        assert t.host == PUB
        assert t.port == 443

    def test_http_allowed(self):
        t = validate_http_target(f"http://{PUB}/path")
        assert t.scheme == "http"
        assert t.port == 80

    def test_ftp_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target(f"ftp://{PUB}/x")
        assert "scheme" in str(exc.value)

    def test_file_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("file:///etc/passwd")
        assert "scheme" in str(exc.value)

    def test_gopher_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target(f"gopher://{PUB}/x")
        assert "scheme" in str(exc.value)

    def test_invalid_port_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target(f"http://{PUB}:abc/x")
        assert "端口" in str(exc.value)

    def test_missing_host_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http:///path")
        assert "host" in str(exc.value)


class TestUserinfo:
    def test_userpass_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target(f"http://user:pass@{PUB}/x")
        assert "userinfo" in str(exc.value)

    def test_user_only_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target(f"http://user@{PUB}/x")
        assert "userinfo" in str(exc.value)


class TestHardBlacklist:
    def test_loopback_v4_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://127.0.0.1/x")
        assert "loopback" in str(exc.value)

    def test_loopback_v6_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://[::1]/x")
        assert "loopback" in str(exc.value)

    def test_cloud_metadata_aws_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://169.254.169.254/latest/meta-data/")
        assert "cloud_metadata" in str(exc.value)

    def test_cloud_metadata_aliyun_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://100.100.100.200/x")
        assert "cloud_metadata" in str(exc.value)

    def test_link_local_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://169.254.1.1/x")
        assert "link_local" in str(exc.value)

    def test_unspecified_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://0.0.0.0/x")
        assert "unspecified" in str(exc.value)

    def test_multicast_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://224.0.0.1/x")
        assert "multicast" in str(exc.value)

    def test_metadata_not_allowed_even_with_allow_private(self):
        # 硬黑名单不可被 allow_private 放行
        with pytest.raises(SsrfError):
            validate_http_target("http://169.254.169.254/x", allow_private=True)

    def test_loopback_not_allowed_even_with_allow_private(self):
        with pytest.raises(SsrfError):
            validate_http_target("http://127.0.0.1/x", allow_private=True)


class TestPrivate:
    def test_private_10_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://10.0.0.1/x")
        assert "私网" in str(exc.value)

    def test_private_192_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://192.168.1.1/x")
        assert "私网" in str(exc.value)

    def test_private_172_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://172.16.0.1/x")
        assert "私网" in str(exc.value)

    def test_cgn_rejected(self):
        # 100.64/10 运营商 NAT，is_private 未覆盖但按私网拒绝
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://100.64.0.1/x")
        assert "私网" in str(exc.value)

    def test_private_allowed_with_flag(self):
        t = validate_http_target("http://10.0.0.1/x", allow_private=True)
        assert t.host == "10.0.0.1"
        assert t.ips == ("10.0.0.1",)

    def test_cgn_allowed_with_flag(self):
        t = validate_http_target("http://100.64.0.1/x", allow_private=True)
        assert t.host == "100.64.0.1"

    def test_loopback_not_private_even_with_flag(self):
        # allow_private 只放行私网，不放行 loopback（硬黑名单）
        with pytest.raises(SsrfError):
            validate_http_target("http://127.0.0.1/x", allow_private=True)


class TestDnsResolution:
    def test_dns_to_private_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target(
                "http://example.com/x", resolver=lambda h: ["10.0.0.1"]
            )
        assert "私网" in str(exc.value)

    def test_dns_to_loopback_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target(
                "http://example.com/x", resolver=lambda h: ["127.0.0.1"]
            )
        assert "loopback" in str(exc.value)

    def test_dns_to_mixed_one_private_rejected(self):
        # 多 IP 中有一个私网就拒绝（防 DNS 混淆）
        with pytest.raises(SsrfError):
            validate_http_target(
                "http://example.com/x",
                resolver=lambda h: [PUB, "10.0.0.1"],
            )

    def test_dns_to_metadata_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target(
                "http://example.com/x",
                resolver=lambda h: ["169.254.169.254"],
            )
        assert "cloud_metadata" in str(exc.value)

    def test_dns_to_public_allowed(self):
        t = validate_http_target(
            "http://example.com/x", resolver=lambda h: ["93.184.216.34"]
        )
        assert t.host == "example.com"
        assert t.ips == ("93.184.216.34",)

    def test_dns_fail_rejected(self):
        with pytest.raises(SsrfError) as exc:
            validate_http_target("http://example.com/x", resolver=lambda h: [])
        assert "DNS" in str(exc.value)

    def test_dns_all_public_allowed(self):
        t = validate_http_target(
            "http://example.com/x",
            resolver=lambda h: [PUB, "1.1.1.1"],
        )
        assert len(t.ips) == 2


class TestPortAndIpv6:
    def test_custom_port(self):
        t = validate_http_target(f"http://{PUB}:8080/x")
        assert t.port == 8080

    def test_https_custom_port(self):
        t = validate_http_target(f"https://{PUB}:8443/x")
        assert t.port == 8443

    def test_ipv6_public(self):
        t = validate_http_target(f"http://[{PUB6}]/x")
        assert t.port == 80
        assert t.host == PUB6

    def test_ipv6_https(self):
        t = validate_http_target(f"https://[{PUB6}]/x")
        assert t.port == 443


class TestGuard:
    def test_guard_default_blocks_private(self):
        guard = HttpSecurityGuard()
        with pytest.raises(SsrfError):
            guard.validate("http://10.0.0.1/x")

    def test_guard_allow_private(self):
        guard = HttpSecurityGuard(allow_private=True)
        t = guard.validate("http://10.0.0.1/x")
        assert t.host == "10.0.0.1"

    def test_guard_allowlisted_private_host(self):
        guard = HttpSecurityGuard(
            private_host_allowlist=("api.internal",),
            resolver=lambda h: ["10.0.0.8"],
        )
        target = guard.validate(
            "http://api.internal:8080/items",
        )
        assert target.host == "api.internal"

    def test_guard_allowlist_does_not_allow_loopback(self):
        guard = HttpSecurityGuard(private_host_allowlist=("127.0.0.1",))
        with pytest.raises(SsrfError):
            guard.validate("http://127.0.0.1:8080/items")

    def test_guard_public_passes(self):
        guard = HttpSecurityGuard()
        t = guard.validate(f"https://{PUB}/x")
        assert t.scheme == "https"

    def test_guard_inject_resolver(self):
        guard = HttpSecurityGuard(resolver=lambda h: ["10.0.0.1"])
        with pytest.raises(SsrfError):
            guard.validate("http://example.com/x")

    def test_guard_allows_proxy_fake_ip_only_for_trusted_https_host(self):
        guard = HttpSecurityGuard(
            proxy_fake_ip_host_allowlist=("api.deepseek.com",),
            resolver=lambda _host: ["198.18.0.159"],
        )

        target = guard.validate("https://api.deepseek.com/chat/completions")

        assert target.ips == ("198.18.0.159",)

    @pytest.mark.parametrize(
        ("url", "resolved_ip"),
        [
            ("http://api.deepseek.com/chat/completions", "198.18.0.159"),
            ("https://api.deepseek.com/chat/completions", "192.168.1.20"),
            ("https://attacker.example/chat/completions", "198.18.0.159"),
        ],
    )
    def test_guard_proxy_fake_ip_exception_stays_narrow(self, url, resolved_ip):
        guard = HttpSecurityGuard(
            proxy_fake_ip_host_allowlist=("api.deepseek.com",),
            resolver=lambda _host: [resolved_ip],
        )

        with pytest.raises(SsrfError):
            guard.validate(url)


def test_default_resolver_ip_literal():
    # IP 字面量不经 DNS（getaddrinfo 直接返回），确定性验证
    result = default_resolver("8.8.8.8")
    assert isinstance(result, list)
    assert "8.8.8.8" in result
