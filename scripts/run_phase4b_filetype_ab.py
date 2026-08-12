#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""比较 filetype 与 filetype+Magika 的常见文件格式识别效果。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import filetype  # noqa: E402
from magika import Magika  # noqa: E402
from openpyxl import Workbook  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402


def _filetype_label(path: Path) -> str:
    kind = filetype.guess(str(path))
    return kind.extension if kind else "unknown"


def _combined_label(path: Path, magika: Magika) -> str:
    baseline = _filetype_label(path)
    if baseline != "unknown":
        return baseline
    result = magika.identify_path(path)
    label = result.output.label
    return label if label not in {"txt", "unknown", "undefined"} else "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "docs"
        / "plans"
        / "phase4b-batch2-results"
        / "filetype-ab.json",
    )
    args = parser.parse_args()
    magika = Magika()
    rows = []
    with TemporaryDirectory() as directory:
        root = Path(directory)
        csv_path = root / "sample.csv"
        csv_path.write_text("姓名,费用\n谢超群,100\n", encoding="utf-8")
        json_path = root / "sample.json"
        json_path.write_text(
            json.dumps(
                [{"姓名": f"人员{index}", "费用": index} for index in range(30)],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        xlsx_path = root / "sample.xlsx"
        workbook = Workbook()
        workbook.active.append(["姓名", "费用"])
        workbook.active.append(["谢超群", 100])
        workbook.save(xlsx_path)
        parquet_path = root / "sample.parquet"
        pq.write_table(
            pa.table({"姓名": ["谢超群"], "费用": [100]}),
            parquet_path,
        )
        for expected, path in (
            ("csv", csv_path),
            ("json", json_path),
            ("xlsx", xlsx_path),
            ("parquet", parquet_path),
        ):
            baseline = _filetype_label(path)
            combined = _combined_label(path, magika)
            rows.append(
                {
                    "format": expected,
                    "filetype": baseline,
                    "filetype_plus_magika": combined,
                    "filetype_correct": baseline == expected,
                    "combined_correct": combined == expected,
                }
            )
    output = {
        "case_count": len(rows),
        "filetype_correct": sum(item["filetype_correct"] for item in rows),
        "combined_correct": sum(item["combined_correct"] for item in rows),
        "adoption": "supplement_text_only",
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
