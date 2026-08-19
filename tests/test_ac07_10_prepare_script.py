# -*- coding: utf-8 -*-
"""AC07-10 S5：真实注册脚本（源码确定性/样例校验/dry-run 零写/归档确定性）。"""
from __future__ import annotations

import json
import sys
import tarfile

from src.config.settings import settings

from scripts import prepare_ac07_10_packs as script


class TestS5ToolSource:
    def test_versions_have_real_content_difference(self) -> None:
        v2 = script._tool_source("2.0.0")
        v3 = script._tool_source("3.0.0")
        # 2.0.0 输出带版本标记；3.0.0 额外支持 ignore_empty_rows。
        assert '"tool_version": "2.0.0"' in v2
        assert "ignore_empty_rows" not in v2
        assert '"tool_version": "3.0.0"' in v3
        assert "ignore_empty_rows" in v3

    def test_version_3_ignore_empty_rows_semantics(self, tmp_path) -> None:
        source = script._tool_source("3.0.0")
        tool = tmp_path / "table_summary.py"
        tool.write_text(source, encoding="utf-8")
        result = script._run_tool(
            tool,
            {
                "csv": "部门,金额\n研发,10\n,99\n市场,20\n",
                "group_field": "部门",
                "value_field": "金额",
                "ignore_empty_rows": True,
            },
        )
        assert result == {
            "groups": {"市场": 20.0, "研发": 10.0},
            "row_count": 3,
            "tool_version": "3.0.0",
        }


class TestS5BuildPack:
    def test_real_sample_validation_passes(self, tmp_path) -> None:
        """构建内含真实样例校验：校验失败会直接抛错。"""
        pack = script._build_pack(tmp_path / "v2", "2.0.0")
        assert pack["archive_sha256"].startswith("sha256:")
        assert pack["version"] == "2.0.0"

    def test_digest_deterministic_across_rebuilds(self, tmp_path) -> None:
        """同版本重建 digest 相同；不同版本 digest 不同（真实差异）。"""
        a = script._build_pack(tmp_path / "a", "2.0.0")
        b = script._build_pack(tmp_path / "b", "2.0.0")
        c = script._build_pack(tmp_path / "c", "3.0.0")
        assert a["archive_sha256"] == b["archive_sha256"]
        assert a["archive_sha256"] != c["archive_sha256"]

    def test_archive_mtime_zero_and_no_links(self, tmp_path) -> None:
        pack = script._build_pack(tmp_path / "v2", "2.0.0")
        with tarfile.open(pack["archive"]) as bundle:
            for member in bundle.getmembers():
                assert member.mtime == 0
                assert not member.issym()


class TestS5DryRun:
    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch, capsys) -> None:
        """dry-run 零写：OCI 目录与数据库路径都不产生。"""
        monkeypatch.setattr(
            sys,
            "argv",
            ["prepare_ac07_10_packs.py", "--owner", "u_test"],
        )
        monkeypatch.setattr(
            settings, "capability_oci_layout_path", str(tmp_path / "oci")
        )
        monkeypatch.setattr(
            settings, "webui_db_path", str(tmp_path / "webui.db")
        )
        assert script.main() == 0
        assert not (tmp_path / "oci").exists()
        assert not (tmp_path / "webui.db").exists()
        plan = json.loads(capsys.readouterr().out)
        assert plan["dry_run"] is True
        assert plan["owner_id"] == "u_test"
        versions = [item["version"] for item in plan["packs"]]
        assert versions == ["2.0.0", "3.0.0"]
        for item in plan["packs"]:
            assert item["scope"] == "personal"
            assert item["maturity"] == "draft"
            assert item["archive_sha256"].startswith("sha256:")
