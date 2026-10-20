# -*- coding: utf-8 -*-
"""在断网能力宿主中对 CSV 做确定性的分组求和。"""
from __future__ import annotations

import csv
from decimal import Decimal, localcontext
import io
import json
import re
import sys


def summarize(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"csv", "group_field", "value_field"}:
        raise ValueError("需要 csv、group_field、value_field 三个参数")
    if any(not isinstance(value, str) for value in payload.values()):
        raise ValueError("参数必须为字符串")
    if len(payload["csv"].encode("utf-8")) > 60_000:
        raise ValueError("CSV 超过大小限制")
    reader = csv.DictReader(io.StringIO(payload["csv"].removeprefix("\ufeff")), strict=True)
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not fields or any(not name for name in fields):
        raise ValueError("CSV 缺少有效且唯一的列名")
    if payload["group_field"] not in fields or payload["value_field"] not in fields:
        raise ValueError("指定列不存在")
    totals, row_count = {}, 0
    # 数值最多 24 位整数、12 位小数；64 位精度足以容纳本工具的行数上限。
    with localcontext() as context:
        context.prec = 64
        for row in reader:
            row_count += 1
            if row_count > 10_000:
                raise ValueError("CSV 超过行数限制")
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV 行与表头列数不一致")
            group = row[payload["group_field"]].strip()
            value = row[payload["value_field"]].strip()
            if not group or not re.fullmatch(r"[+-]?\d{1,24}(?:\.\d{1,12})?", value):
                raise ValueError("分组值为空或数值格式无效")
            totals[group] = totals.get(group, Decimal(0)) + Decimal(value)
    return {
        "groups": {key: format(value, "f") for key, value in sorted(totals.items())},
        "row_count": row_count,
        "tool_version": "1.0.0",
    }


def main() -> int:
    if sys.argv[1:] == ["--health"]:
        print("ok")
        return 0
    try:
        if len(sys.argv) != 2:
            raise ValueError("只接受一个 JSON 参数")
        result = summarize(json.loads(sys.argv[1]))
    except (ValueError, csv.Error):
        # 不回显参数和原始数据，避免错误日志泄露业务正文。
        print(json.dumps({"error": "输入不符合 CSV 分组求和要求"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
