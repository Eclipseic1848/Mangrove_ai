# -*- coding: utf-8 -*-
from src.data_prep.document_models import ExtractedTable
from src.services.document_table_recipe import normalize_merged_tables


def test_table_recipe_removes_only_exact_repeated_headers_and_keeps_totals():
    table = ExtractedTable(
        table_id="merged-1",
        name="合并表",
        artifact_id="artifact-a",
        page=1,
        columns=["来源表", "来源页", "列1", "列2"],
        rows=[
            {"来源表": "A", "来源页": 1, "列1": "项目", "列2": "金额"},
            {"来源表": "A", "来源页": 1, "列1": "服务", "列2": "100"},
            {"来源表": "A", "来源页": 1, "列1": "合计", "列2": "100"},
            {"来源表": "B", "来源页": 2, "列1": "项目", "列2": "金额"},
            {"来源表": "B", "来源页": 2, "列1": "产品", "列2": "200"},
        ],
    )

    result = normalize_merged_tables([table])

    assert result.tables[0].columns == [
        "来源表", "来源页", "_行类型", "项目", "金额",
    ]
    assert len(result.tables[0].rows) == 3
    assert result.tables[0].rows[1]["_行类型"] == "合计"
    assert result.audit["rules"][0]["removed_repeated_headers"] == 2
    assert result.audit["rules"][0]["reversible_from"].endswith(
        "extracted_tables_raw.json"
    )


def test_table_recipe_does_not_remove_repeated_numeric_business_rows():
    table = ExtractedTable(
        table_id="merged-2",
        name="合并表",
        artifact_id="artifact-a",
        page=1,
        columns=["来源表", "来源页", "列1", "列2"],
        rows=[
            {"来源表": "A", "来源页": 1, "列1": "服务", "列2": "100"},
            {"来源表": "B", "来源页": 2, "列1": "服务", "列2": "100"},
        ],
    )

    result = normalize_merged_tables([table])

    assert result.tables[0].rows == table.rows
    assert result.audit["rules"][0]["applied"] is False
