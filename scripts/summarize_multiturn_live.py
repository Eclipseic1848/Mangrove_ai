"""离线汇总真实多轮记录；格式、结构值与额外文字审读分开计数。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from scripts.evaluate_multiturn_live import parse_reply, scenarios, long_scenarios


def _reject_nonfinite(value):
    raise ValueError("JSON不允许非有限数值")


def summarize(reports):
    cases, counts, seen = [], Counter(), set()
    formats = Counter()
    plans = {report.get("planned_cases", 40) for report in reports}
    if len(plans) != 1 or any(type(n) is not int or n <= 0 for n in plans):
        raise ValueError("报告必须属于同一场景计划，且计划数量为正整数")
    planned_cases = plans.pop()
    # 旧报告没有逐例轮数，只认可生成器中已知的标准场景；不从实跑轮数反推完整性。
    known_turns = {case["name"]: len(case["turns"]) for case in scenarios() + long_scenarios()}
    for report in reports:
        for case in report["cases"]:
            if case["name"] in seen:
                raise ValueError("重复场景不得重复计入分母")
            seen.add(case["name"])
            planned_turns = case.get("planned_turns", known_turns.get(case["name"]))
            if planned_turns is not None and (type(planned_turns) is not int or planned_turns <= 0):
                raise ValueError("场景计划轮数必须为正整数")
            rows = []
            for index, turn in enumerate(case["turns"], 1):
                row = {"turn": index, "request_status": turn["status"]}
                # 值可从代码围栏中读取，不代表遵守了用户“只输出JSON”的格式要求。
                row["reply_format"] = "unknown"
                if "reply" in turn:
                    try:
                        json.loads(turn["reply"], parse_constant=_reject_nonfinite)
                        row["reply_format"] = "plain_json"
                    except ValueError:
                        row["reply_format"] = "wrapped_or_invalid"
                formats[row["reply_format"]] += 1
                if "reply" not in turn:
                    row["value_status"] = "unknown"
                else:
                    try:
                        value = parse_reply(turn["reply"])
                        row["extra_text_review"] = False
                    except (ValueError, IndexError):
                        blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", turn["reply"], re.DOTALL)
                        row["extra_text_review"] = True
                        try:
                            value = json.loads(blocks[0]) if len(blocks) == 1 else None
                        except ValueError:
                            value = None
                    row["value_status"] = "match" if value == turn["expected"] else "needs_review"
                counts[row["value_status"]] += 1
                if row.get("extra_text_review"):
                    counts["extra_text_review"] += 1
                rows.append(row)
            cases.append({"name": case["name"], "complete": len(rows) == planned_turns and all(r["value_status"] == "match" for r in rows), "turns": rows})
    return {"planned_cases": planned_cases, "started_cases": len(cases),
            "complete_value_matching_cases": sum(c["complete"] for c in cases),
            "complete_plain_json_matching_cases": sum(c["complete"] and all(r["reply_format"] == "plain_json" for r in c["turns"]) for c in cases),
            "reply_format_counts": dict(formats), "turn_counts": dict(counts), "cases": cases,
            "limitations": ["结构值匹配不代表额外说明正确，标记项仍需审读", "不覆盖执行复用、补采、恢复或用户验收"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = summarize([json.loads(p.read_text(encoding="utf-8")) for p in args.reports])
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
