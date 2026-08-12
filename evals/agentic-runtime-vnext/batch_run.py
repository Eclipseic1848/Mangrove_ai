# -*- coding: utf-8 -*-
"""连续运行冻结语料，并生成不进入 Git 的阶段 1 赛马摘要。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any

from common import CASE_FILE, PROTOTYPE_ROOT, REPO_ROOT, model_config
from tui import CANDIDATES, execute, initial_state


FORMAL_CASES = (
    "p0-01-pdf-table-to-csv",
    "p0-02-word-clauses-to-txt",
    "p0-03-excel-sheet-selection",
    "p0-06-ambiguous-target",
    "p0-08-tool-failure-replan",
    "p0-10-prompt-injection-isolation",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def run(candidate: str, repeats: int) -> tuple[dict[str, Any], Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = PROTOTYPE_ROOT / "runs" / f"formal-{stamp}-{candidate}"
    report_dir.mkdir(parents=True, exist_ok=False)
    config = model_config()
    results: list[dict[str, Any]] = []
    for repeat in range(1, repeats + 1):
        for case_id in FORMAL_CASES:
            started = time.monotonic()
            print(
                json.dumps(
                    {
                        "event": "case.started",
                        "candidate": candidate,
                        "case_id": case_id,
                        "repeat": repeat,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            try:
                state, run_dir = execute(initial_state(candidate, case_id))
                verification_path = run_dir / "verification.json"
                verification = (
                    json.loads(verification_path.read_text(encoding="utf-8"))
                    if verification_path.is_file()
                    else {"passed": False, "errors": ["缺少验证报告"]}
                )
                result = {
                    "candidate": candidate,
                    "case_id": case_id,
                    "repeat": repeat,
                    "passed": state.verification_passed is True,
                    "status": state.status.value,
                    "tool_calls": state.tool_calls,
                    "clarification_required": state.clarification_required,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "errors": verification.get("errors", []),
                    "run_dir": str(run_dir),
                }
            except Exception as exc:
                # 单条路线失败不能中断整场赛马，否则会掩盖后续用例的真实表现。
                result = {
                    "candidate": candidate,
                    "case_id": case_id,
                    "repeat": repeat,
                    "passed": False,
                    "status": "runner_error",
                    "tool_calls": 0,
                    "clarification_required": False,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "run_dir": None,
                }
            results.append(result)
            print(
                json.dumps({"event": "case.completed", **result}, ensure_ascii=False),
                flush=True,
            )
    by_case = Counter()
    for item in results:
        if item["passed"]:
            by_case[item["case_id"]] += 1
    summary = {
        "schema_version": "stage1-formal-summary-v1",
        "candidate": candidate,
        "repeats": repeats,
        "fixture_sha256": _sha256(CASE_FILE),
        "common_prompt_source_sha256": _sha256(PROTOTYPE_ROOT / "common.py"),
        "tool_host_sha256": _sha256(PROTOTYPE_ROOT / "tool_host.py"),
        "adapter_sha256": _sha256(
            PROTOTYPE_ROOT
            / "adapters"
            / (
                "pi_adapter.mjs"
                if candidate == "pi"
                else f"{candidate}_adapter.py"
            )
        ),
        "git_commit": _git_commit(),
        "model": config["model"],
        "base_url": config["base_url"],
        "total_runs": len(results),
        "passed_runs": sum(1 for item in results if item["passed"]),
        "all_cases_three_of_three": all(
            by_case[case_id] == repeats for case_id in FORMAL_CASES
        ),
        "case_pass_counts": dict(by_case),
        "results": results,
    }
    target = report_dir / "batch-summary.json"
    target.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return summary, target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=CANDIDATES, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        raise ValueError("repeats 必须大于 0")
    summary, target = run(args.candidate, args.repeats)
    print(
        json.dumps(
            {
                "event": "batch.completed",
                "candidate": args.candidate,
                "passed_runs": summary["passed_runs"],
                "total_runs": summary["total_runs"],
                "all_cases_three_of_three": summary["all_cases_three_of_three"],
                "summary": str(target),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if summary["all_cases_three_of_three"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
