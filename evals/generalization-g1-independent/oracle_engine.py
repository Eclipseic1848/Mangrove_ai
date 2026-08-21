# -*- coding: utf-8 -*-
"""从冻结逻辑表和声明式推导规格重算功能题期望。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def _decimal(value: str) -> Decimal:
    return Decimal(str(value))


def _format_decimal(value: Decimal, places: int) -> str:
    quantum = Decimal(1).scaleb(-places)
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), f".{places}f")


def _matches(row: dict[str, str], condition: dict) -> bool:
    actual = row.get(condition["field"], "")
    expected = str(condition.get("value", ""))
    operation = condition["op"]
    if operation == "eq":
        return actual == expected
    if operation == "ne":
        return actual != expected
    if operation == "in":
        return actual in {str(value) for value in condition["values"]}
    if operation == "not_in":
        return actual not in {str(value) for value in condition["values"]}
    if operation in {"gt", "ge", "lt", "le"}:
        left = _decimal(actual)
        right = _decimal(expected)
        return {
            "gt": left > right,
            "ge": left >= right,
            "lt": left < right,
            "le": left <= right,
        }[operation]
    if operation == "before":
        return date.fromisoformat(actual) < date.fromisoformat(expected)
    if operation == "on_or_before":
        return date.fromisoformat(actual) <= date.fromisoformat(expected)
    if operation == "after":
        return date.fromisoformat(actual) > date.fromisoformat(expected)
    if operation == "between":
        return str(condition["start"]) <= actual <= str(condition["end"])
    if operation == "field_ne":
        return actual != row.get(condition["other_field"], "")
    raise ValueError(f"未知过滤操作：{operation}")


def _apply_filters(rows: list[dict[str, str]], conditions: list[dict]) -> list[dict[str, str]]:
    return [row for row in rows if all(_matches(row, condition) for condition in conditions)]


def _compute(row: dict[str, str], field: str, expression: dict) -> None:
    operation = expression["op"]
    places = int(expression.get("places", 2))
    if operation == "subtract":
        value = _decimal(row[expression["a"]]) - _decimal(row[expression["b"]])
        row[field] = _format_decimal(value, places)
    elif operation == "multiply":
        value = _decimal(row[expression["a"]]) * _decimal(row[expression["b"]])
        row[field] = _format_decimal(value, places)
    elif operation == "divide_pct":
        value = _decimal(row[expression["a"]]) / _decimal(row[expression["b"]]) * Decimal(100)
        row[field] = _format_decimal(value, places)
    elif operation == "max_volume":
        volume = (
            _decimal(row[expression["length"]])
            * _decimal(row[expression["width"]])
            * _decimal(row[expression["height"]])
            / _decimal(expression["divisor"])
        )
        value = max(_decimal(row[expression["actual"]]), volume)
        row[field] = _format_decimal(value, places)
    elif operation == "min":
        value = min(_decimal(row[expression["a"]]), _decimal(row[expression["b"]]))
        row[field] = _format_decimal(value, places)
    elif operation == "max_zero_subtract":
        value = max(
            Decimal(0),
            _decimal(row[expression["target"]])
            - _decimal(row[expression["current"]])
            - _decimal(row[expression["inbound"]]),
        )
        row[field] = _format_decimal(value, places)
    elif operation == "days_overdue":
        value = date.fromisoformat(expression["as_of"]) - date.fromisoformat(row[expression["due"]])
        row[field] = str(value.days)
    elif operation == "compare_zero":
        value = _decimal(row[expression["field"]])
        row[field] = expression["positive"] if value > 0 else expression["negative"] if value < 0 else expression["zero"]
    else:
        raise ValueError(f"未知计算操作：{operation}")


def _apply_computed(rows: list[dict[str, str]], computed: dict[str, dict]) -> list[dict[str, str]]:
    for row in rows:
        for field, expression in computed.items():
            _compute(row, field, expression)
    return rows


def _sort_rows(rows: list[dict[str, str]], sorting: list[dict]) -> list[dict[str, str]]:
    result = list(rows)
    for rule in reversed(sorting):
        field = rule["field"]
        kind = rule.get("type", "string")
        if kind == "number":
            key = lambda row, name=field: _decimal(row[name])
        else:
            key = lambda row, name=field: row[name]
        result.sort(key=key, reverse=rule.get("direction") == "desc")
    return result


def _project(rows: list[dict[str, str]], columns: list[str]) -> list[dict[str, str]]:
    return [{column: row[column] for column in columns} for row in rows]


def _rows(catalog: dict, reference: dict) -> list[dict[str, str]]:
    return deepcopy(catalog[reference["source"]]["tables"][reference["table"]]["rows"])


def derive(catalog: dict, specification: dict) -> dict:
    kind = specification["kind"]
    filters_after = specification.get("filters", [])
    if kind == "extract":
        rows = _rows(catalog, specification)
    elif kind == "group_sum":
        source_rows = _apply_filters(_rows(catalog, specification), specification.get("filters", []))
        filters_after = []
        groups: dict[tuple[str, ...], dict[str, str]] = {}
        group_fields = specification["group_by"]
        for source_row in source_rows:
            key = tuple(source_row[field] for field in group_fields)
            if key not in groups:
                groups[key] = {field: source_row[field] for field in group_fields}
                for output_field in specification["sums"]:
                    groups[key][output_field] = "0"
            for output_field, sum_spec in specification["sums"].items():
                total = _decimal(groups[key][output_field]) + _decimal(source_row[sum_spec["field"]])
                groups[key][output_field] = _format_decimal(total, int(sum_spec.get("places", 2)))
        rows = list(groups.values())
    elif kind == "overlay":
        rows = _rows(catalog, specification["base"])
        overrides = _rows(catalog, specification["override"])
        keys = specification["keys"]
        index = {tuple(row[key] for key in keys): row for row in rows}
        for override in overrides:
            target = index[tuple(override[key] for key in keys)]
            for field in specification["overlay_fields"]:
                target[field] = override[field]
    elif kind == "dedupe_latest":
        combined: list[dict[str, str]] = []
        for reference in specification["inputs"]:
            combined.extend(_rows(catalog, reference))
        keys = specification["keys"]
        timestamp = specification["timestamp"]
        latest: dict[tuple[str, ...], dict[str, str]] = {}
        for row in combined:
            key = tuple(row[field] for field in keys)
            if key not in latest or row[timestamp] > latest[key][timestamp]:
                latest[key] = row
        rows = list(latest.values())
    elif kind == "join":
        left = _rows(catalog, specification["left"])
        right = _rows(catalog, specification["right"])
        right_index = {row[specification["right_key"]]: row for row in right}
        rows = []
        for left_row in left:
            right_row = right_index.get(left_row[specification["left_key"]])
            if right_row is None:
                continue
            merged = dict(left_row)
            for output_field, right_field in specification.get("right_map", {}).items():
                merged[output_field] = right_row[right_field]
            rows.append(merged)
    elif kind == "union":
        rows = []
        for reference in specification["inputs"]:
            rows.extend(_rows(catalog, reference))
    else:
        raise ValueError(f"未知推导类型：{kind}")

    rows = _apply_filters(rows, filters_after)
    rows = _apply_computed(rows, specification.get("computed", {}))
    rows = _apply_filters(rows, specification.get("post_filters", []))
    rows = _sort_rows(rows, specification.get("sort", []))
    columns = specification["project"]
    return {"columns": columns, "rows": _project(rows, columns)}
