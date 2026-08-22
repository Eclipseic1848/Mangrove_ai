# -*- coding: utf-8 -*-
"""从冻结源重新计算 G1-03 期望。"""
from decimal import Decimal
from pathlib import Path

from artifact_io import read_source


def derive(case: dict, root: Path) -> list[dict[str, str]]:
    loaded = [read_source(root / "sources" / src["filename"], src["format"]) for src in case["sources"]]
    rows = [dict(row) for row in loaded[0]["authoritative"]]
    if case["recipe"]["compound"]:
        by_id = {row["unit_id"]: row for row in rows}
        for row in loaded[1]["approved_amendment"]:
            if row["version"] == "3": by_id[row["unit_id"]] = dict(row)
        rows = list(by_id.values())
    threshold = Decimal(case["recipe"]["threshold"]); columns = case["columns"]
    output = [{columns[0]: row["unit_id"], columns[1]: row["label"], columns[2]: f"{Decimal(row['quota'])-Decimal(row['spent']):.2f}"} for row in rows if row["state"] == "ready" and Decimal(row["quota"]) >= threshold]
    output.sort(key=lambda row: row[columns[0]])
    output.sort(key=lambda row: Decimal(row[columns[2]]), reverse=case["recipe"]["descending"])
    return output
