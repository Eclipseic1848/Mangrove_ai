# -*- coding: utf-8 -*-
"""用当前独立 Verifier 重算既有正式运行，不重新调用模型。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from common import CASE_FILE, PROTOTYPE_ROOT, load_bakeoff_case
from tool_host import verify_candidate


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _model_input_hash(case: dict[str, Any]) -> str:
    # expected 不进入 Agent 上下文；单独散列 Goal 与来源可证明重算没有换题。
    payload = {"goal": case["goal"], "sources": case["sources"]}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="append", required=True, type=Path)
    args = parser.parse_args()
    all_results: list[dict[str, Any]] = []
    source_summaries: list[str] = []
    for summary_path in args.summary:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        source_summaries.append(str(summary_path))
        for original in summary["results"]:
            result = dict(original)
            run_dir = Path(result["run_dir"]) if result.get("run_dir") else None
            if run_dir and run_dir.is_dir():
                case = load_bakeoff_case(result["case_id"])
                verification = verify_candidate(case, run_dir)
                result["passed"] = bool(verification["passed"])
                result["errors"] = verification["errors"]
            all_results.append(result)
    counts: dict[str, Counter[str]] = {}
    for item in all_results:
        candidate_counts = counts.setdefault(item["candidate"], Counter())
        if item["passed"]:
            candidate_counts[item["case_id"]] += 1
    cases = json.loads(CASE_FILE.read_text(encoding="utf-8"))["cases"]
    report = {
        "schema_version": "stage1-formal-revalidation-v1",
        "source_summaries": source_summaries,
        "fixture_sha256": _sha256_file(CASE_FILE),
        "verifier_sha256": _sha256_file(PROTOTYPE_ROOT / "tool_host.py"),
        "model_input_hashes": {
            case["case_id"]: _model_input_hash(case) for case in cases
        },
        "candidate_totals": {
            candidate: {
                "passed_runs": sum(counter.values()),
                "total_runs": sum(
                    1 for item in all_results if item["candidate"] == candidate
                ),
                "case_pass_counts": dict(counter),
            }
            for candidate, counter in counts.items()
        },
        "results": all_results,
    }
    target = (
        PROTOTYPE_ROOT
        / "runs"
        / f"formal-revalidation-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    target.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(target),
                "candidate_totals": report["candidate_totals"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
