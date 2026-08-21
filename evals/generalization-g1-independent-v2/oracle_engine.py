# -*- coding: utf-8 -*-
"""只从重开的冻结来源推导业务期望。"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from source_io import read_source


def derive_rows(case_definition: dict, root: Path) -> list[dict[str, str]]:
    loaded = [
        read_source(root / "sources" / source["filename"], source["format"])
        for source in case_definition["sources"]
    ]
    recipe = case_definition["recipe"]
    primary = [dict(row) for row in loaded[0][recipe["table"]]]
    if recipe["kind"] == "reconcile_net_filter":
        corrections = loaded[1]["corrections"]
        by_id = {row["record_id"]: row for row in primary}
        for correction in corrections:
            if correction["revision"] == "2":
                by_id[correction["record_id"]] = dict(correction)
        primary = list(by_id.values())
    threshold = Decimal(recipe["threshold"])
    selected = [
        row for row in primary
        if row["status"] == "open" and Decimal(row["amount"]) >= threshold
    ]
    output = []
    columns = recipe["columns"]
    for row in selected:
        net = Decimal(row["amount"]) - Decimal(row["used"])
        output.append({
            columns[0]: row["record_id"],
            columns[1]: row["item"],
            columns[2]: f"{net:.2f}",
        })
    output.sort(
        key=lambda row: (Decimal(row[columns[2]]), row[columns[0]]),
        reverse=bool(recipe["descending"]),
    )
    if recipe["descending"]:
        # reverse 会同时反转编号；净值相同时恢复编号升序。
        output.sort(key=lambda row: row[columns[0]])
        output.sort(key=lambda row: Decimal(row[columns[2]]), reverse=True)
    return output
