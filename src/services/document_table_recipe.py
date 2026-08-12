# -*- coding: utf-8 -*-
"""文档合并表的确定性语义归一；原表必须另行保留以便回放。"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Sequence

from src.data_prep.document_models import ExtractedTable


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def _unique_headers(values: Sequence[str]) -> list[str]:
    counts: Counter[str] = Counter()
    result = []
    for index, value in enumerate(values, start=1):
        base = value or f"列{index}"
        counts[base] += 1
        result.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return result


def _header_like(signature: Sequence[str]) -> bool:
    """只把短文本多列行当作表头，避免删除恰好重复的业务数据。"""
    nonempty = [value for value in signature if value]
    if len(nonempty) < 2 or any(len(value) > 40 for value in nonempty):
        return False
    return not any(
        re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?%?", value)
        for value in nonempty
    )


@dataclass(frozen=True)
class TableRecipeResult:
    tables: list[ExtractedTable]
    audit: dict[str, Any]


def normalize_merged_tables(
    tables: Sequence[ExtractedTable],
) -> TableRecipeResult:
    """自动去除跨文件完全相同的重复表头；合计行只标记、不删除。"""
    normalized: list[ExtractedTable] = []
    audits: list[dict[str, Any]] = []
    for table in tables:
        value_columns = [
            column for column in table.columns
            if column not in {"来源表", "来源页"}
        ]
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in table.rows:
            by_source[_text(row.get("来源表"))].append(row)
        first_signatures = [
            tuple(_text(row.get(column)) for column in value_columns)
            for rows in by_source.values()
            for row in rows[:1]
        ]
        common_signature = None
        if first_signatures:
            signature, occurrences = Counter(first_signatures).most_common(1)[0]
            if occurrences >= 2 and _header_like(signature):
                common_signature = signature

        if common_signature is None:
            normalized.append(table)
            audits.append({
                "table_id": table.table_id,
                "applied": False,
                "reason": "未发现至少两份来源共有的完全相同表头",
                "input_rows": len(table.rows),
                "output_rows": len(table.rows),
            })
            continue

        headers = _unique_headers(list(common_signature))
        output_rows: list[dict[str, Any]] = []
        removed_headers = 0
        total_rows = 0
        for row in table.rows:
            signature = tuple(_text(row.get(column)) for column in value_columns)
            if signature == common_signature:
                removed_headers += 1
                continue
            values = {
                header: row.get(column)
                for header, column in zip(headers, value_columns)
            }
            first_value = _text(next(iter(values.values()), "")).casefold()
            row_kind = (
                "合计"
                if re.match(r"^(合计|总计|小计|total)\b", first_value)
                else "数据"
            )
            total_rows += row_kind == "合计"
            output_rows.append({
                "来源表": row.get("来源表"),
                "来源页": row.get("来源页"),
                "_行类型": row_kind,
                **values,
            })
        normalized.append(table.model_copy(update={
            "name": f"{table.name} · 已归一",
            "columns": ["来源表", "来源页", "_行类型", *headers],
            "rows": output_rows,
        }))
        audits.append({
            "table_id": table.table_id,
            "applied": True,
            "rule_id": "document_repeated_header_exact_v1",
            "input_rows": len(table.rows),
            "output_rows": len(output_rows),
            "removed_repeated_headers": removed_headers,
            "retained_total_rows": total_rows,
            "canonical_headers": headers,
            "header_signature_sha256": hashlib.sha256(
                json.dumps(
                    common_signature,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
            "reversible_from": "extraction/extracted_tables_raw.json",
        })
    return TableRecipeResult(
        tables=normalized,
        audit={
            "recipe_id": "document_table_semantic_normalization",
            "version": "1",
            "mode": "automatic",
            "rules": audits,
        },
    )
