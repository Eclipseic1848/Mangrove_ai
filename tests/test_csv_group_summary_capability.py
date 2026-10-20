"""业务能力样本及调用契约回归，不访问用户资料或网络。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.capability_adapters import load_runtime_manifests
from src.capability_catalog.reuse import ToolNeed, match_installed_tools, validate_reuse_call
from src.capability_catalog.models import CatalogActor
from src.conversation_steering import CapabilityMaturity, CapabilityPack, ProcedureScope

ROOT = Path(__file__).resolve().parents[1] / "examples/capabilities/csv-group-summary"
spec = importlib.util.spec_from_file_location("csv_group_summary", ROOT / "table_summary.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_business_variants_preserve_decimal_totals():
    for content, group, value, expected in (
        ("部门,金额\n研发,0.1\n市场,12.50\n研发,0.2\n", "部门", "金额", {"研发": "0.3", "市场": "12.50"}),
        ("类别,件数\n办公,8\n设备,-2\n办公,4\n", "类别", "件数", {"办公": "12", "设备": "-2"}),
        ("部门,金额\n", "部门", "金额", {}),
    ):
        result = tool.summarize({"csv": content, "group_field": group, "value_field": value})
        assert result["groups"] == expected


@pytest.mark.parametrize("content", [
    "部门,金额\n研发,NaN\n", "部门,金额\n研发,Infinity\n", "部门,金额\n,1\n",
    "部门,金额\n研发,1,2\n", "部门,金额\n研发\n", "部门,部门\n研发,1\n", "部门,金额\n研发,1e1000\n",
])
def test_invalid_data_is_rejected(content):
    with pytest.raises(ValueError):
        tool.summarize({"csv": content, "group_field": "部门", "value_field": "金额"})


def test_cli_health_and_error_do_not_echo_source():
    assert subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "table_summary.py"), "--health"], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip() == "ok"
    result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "table_summary.py"), '{"csv":"私有正文"}'], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 2 and "私有正文" not in result.stdout + result.stderr


def test_two_needs_match_the_same_exact_contract():
    from types import SimpleNamespace
    contract = (ROOT / "reuse-contract.json").read_text(encoding="utf-8")
    pack = CapabilityPack(pack_id="csv-group-summary", version="1.0.0", digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PERSONAL, owner_id="owner", maturity=CapabilityMaturity.DRAFT,
        manifest=(("reuse_contract", contract), ("license", "MIT")),
        source_provenance=("https://github.com/Eclipseic1848/Mangrove_ai",))
    catalog = SimpleNamespace(list_visible_packs=lambda actor: (pack,))
    runtime = load_runtime_manifests((ROOT,))[0].manifest
    for purpose in ("按部门统计费用", "按类别统计数量"):
        need = ToolNeed(purpose=purpose, operations=("group_sum",), input_formats=("csv",), output_formats=("json",))
        result = match_installed_tools(catalog, CatalogActor(owner_id="owner", role="admin"), need)
        assert result["matches"][0]["ref"] == {"pack_id": pack.pack_id, "version": pack.version, "digest": pack.digest}
        validate_reuse_call(pack, runtime, [json.dumps({"csv": "部门,金额\n研发,1\n", "group_field": "部门", "value_field": "金额"})], None)


def test_live_report_checks_types_as_well_as_numeric_values():
    from scripts.verify_capability_business_reuse import check_result
    check_result({"groups": {"研发": "0.3"}, "row_count": 2}, {"研发": "0.3"}, 2)
    for result in (
        {"groups": {"研发": 0.3}, "row_count": 2},
        {"groups": {"研发": "0.3"}, "row_count": 2.0},
        {"groups": {"研发": "0.3"}, "row_count": 2, "tool_version": "1.0.0"},
    ):
        with pytest.raises(AssertionError):
            check_result(result, {"研发": "0.3"}, 2)
