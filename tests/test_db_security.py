# -*- coding: utf-8 -*-
"""数据库安全校验测试（Phase 3 Task 3 TDD）——主机白名单/黑名单、sqlite 路径安全。
"""
from __future__ import annotations

import ipaddress

import pytest

from src.connectors.db_security import (
    SqlitePathError,
    validate_db_host,
    validate_sqlite_path,
)

# 模拟 DNS resolver：把已知测试域名映射到公网 IP
_MOCK_IPS = {
    "db.example.com": ["93.184.216.34"],
    "allowed.example.com": ["93.184.216.34"],
    "unauthorized.example.com": ["198.51.100.1"],
}
_MOCK_RESOLVER = lambda host, port=None: _MOCK_IPS.get(host, [])


# ---------------- DB 主机校验 ----------------


class TestValidateDbHost:

    def test_public_ip_allowed(self):
        t = validate_db_host("93.184.216.34", 3306)
        assert t.host == "93.184.216.34"

    def test_loopback_allowed_by_default(self):
        """DB 场景默认允许 loopback（与 HTTP 不同）。"""
        t = validate_db_host("127.0.0.1", 5432)
        assert t.port == 5432

    def test_private_ip_allowed_default(self):
        t = validate_db_host("10.0.0.5", 3306)
        assert t.host == "10.0.0.5"

    def test_cloud_metadata_hard_blocked(self):
        """T7：云元数据三 IP 硬黑名单不可放行。"""
        for ip in ["169.254.169.254", "100.100.100.200"]:
            with pytest.raises(ValueError, match="云元数据|黑名单"):
                validate_db_host(ip, 3306)

    def test_ipv6_metadata_blocked(self):
        with pytest.raises(ValueError, match="云元数据|黑名单"):
            validate_db_host("fd00:ec2::254", 3306)

    def test_port_whitelist_violation(self):
        """非白名单端口拒绝。"""
        with pytest.raises(ValueError, match="端口|3306,5432"):
            validate_db_host("127.0.0.1", 6379)  # Redis port, IP literal skips DNS

    def test_host_allowlist_enforced(self):
        """allowlist 非空时仅放行清单内主机（非 allowlist 内直接 ValueError，不发包）。"""
        with pytest.raises(ValueError, match="主机.*白名单"):
            validate_db_host("unauthorized.example.com", 3306,
                             allowlist={"allowed.example.com"})

    def test_host_allowlist_match(self):
        t = validate_db_host("allowed.example.com", 5432,
                             allowlist={"allowed.example.com"},
                             resolver=_MOCK_RESOLVER)
        assert t.host == "allowed.example.com"

    def test_port_override(self):
        t = validate_db_host("127.0.0.1", 9999,
                             allowed_ports={3306, 5432, 9999})
        assert t.port == 9999


# ---------------- sqlite 路径安全 ----------------


class TestSqlitePath:
    def test_simple_relpath(self):
        p = validate_sqlite_path("orders.db")
        assert p.name == "orders.db"

    def test_subdir_relpath(self):
        p = validate_sqlite_path("reports/sales.db")
        assert "reports" in str(p.parent)

    def test_absolute_path_rejected(self):
        with pytest.raises(SqlitePathError, match="绝对路径|相对路径"):
            validate_sqlite_path("/etc/passwd")

    def test_windows_absolute_rejected(self):
        with pytest.raises(SqlitePathError, match="绝对路径|盘符|相对路径"):
            validate_sqlite_path("C:\\Windows\\System32\\config\\SAM")

    def test_dot_dot_rejected(self):
        with pytest.raises(SqlitePathError, match="穿越|.."):
            validate_sqlite_path("../../../data/webui.db")

    def test_platform_db_blacklisted(self):
        """T8：平台自用库硬黑名单。"""
        for name in ["webui.db", "app.db", "scheduler.db", "checkpoints.sqlite"]:
            with pytest.raises(SqlitePathError, match="平台自用|禁止|reserved"):
                validate_sqlite_path(name)
