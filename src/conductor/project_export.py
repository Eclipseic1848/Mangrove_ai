"""同一项目结果快照导出文件，不发布正式交付。"""
from __future__ import annotations

import json
from pathlib import Path

import xlsxwriter


PROJECT_COLUMNS = {
    "project_id": "项目标识", "project_name": "项目名称", "owner": "业主", "region": "地区",
    "construction_content": "建设内容", "amount": "金额", "amount_unit": "金额单位",
    "publication_date": "发布时间", "current_stage": "当前阶段", "source_ids": "来源编号",
    "source_links": "原文链接", "missing_fields": "未披露字段",
    "field_variants": "多公告字段差异",
    "field_review_notes": "字段核对说明",
    "stage_note": "阶段核对说明", "amount_scope_note": "项目级金额说明", "announcement_count": "独立公告数",
}


def write_workbook(path: Path, sheets: list[tuple[str, list[dict], dict[str, str]]]) -> None:
    """所有来源文本显式写作字符串，长正文分段保留。"""
    long_texts = []
    with xlsxwriter.Workbook(path, {"strings_to_formulas": False, "strings_to_urls": False}) as workbook:
        wrap = workbook.add_format({"text_wrap": True, "valign": "top"})
        header = workbook.add_format({"bold": True, "bg_color": "#E7F1EF", "text_wrap": True})
        for title, rows, defaults in sheets:
            columns = list(dict.fromkeys([*defaults, *(key for row in rows for key in row)]))
            sheet = workbook.add_worksheet(title)
            sheet.write_row(0, 0, [defaults.get(key, key) for key in columns], header)
            sheet.freeze_panes(1, 0)
            sheet.set_column(0, len(columns) - 1, 24, wrap)
            for row_index, row in enumerate(rows, 1):
                for column_index, key in enumerate(columns):
                    value = row.get(key)
                    if value is None:
                        value = "未披露"
                    if isinstance(value, (list, dict)):
                        value = json.dumps(value, ensure_ascii=False, default=str)
                    if isinstance(value, str):
                        if len(value.encode("utf-16-le")) > 65000:
                            identity = f"{title}:{row_index}:{key}"
                            long_texts.extend((identity, index // 15000 + 1, value[index:index + 15000])
                                              for index in range(0, len(value), 15000))
                            value = f"完整内容见长文本：{identity}"
                        sheet.write_string(row_index, column_index, value, wrap)
                    else:
                        sheet.write(row_index, column_index, value, wrap)
            if rows:
                sheet.autofilter(0, 0, len(rows), len(columns) - 1)
        if long_texts:
            sheet = workbook.add_worksheet("长文本")
            sheet.write_row(0, 0, ["来源单元格", "分段序号", "原文"], header)
            for index, row in enumerate(long_texts, 1):
                sheet.write_string(index, 0, row[0])
                sheet.write_number(index, 1, row[1])
                sheet.write_string(index, 2, row[2])


def _text(value) -> str:
    if value is None or value == "":
        return "未披露"
    if isinstance(value, list):
        value = "；".join(map(str, value))
    return str(value).replace("|", "\\|").replace("\n", " ")


def export_projects(state: dict, out_dir: Path) -> dict:
    spec = state["task_spec"]
    scope = spec.project_collection
    result = state.get("project_result") or {}
    projects = result.get("projects") or []
    sources = result.get("sources") or []
    quality = state.get("quality") or {}
    count = len(projects)
    target = scope.target_count
    target_met = count >= target
    passed = (count > 0 and quality.get("passed") is True and quality.get("review_complete") is True
              and (target_met or scope.allow_shortfall))
    quality = {**quality, "passed": passed}
    coverage = {**(result.get("coverage") or {}), "target_count": target,
        "verified_projects": count, "target_met": target_met, "shortfall": max(0, target - count),
        "complete": target_met and passed, "allow_shortfall": scope.allow_shortfall,
        "result_verified": passed}
    source_by_id = {source.get("source_id", index): source for index, source in enumerate(sources, 1)}
    rows = [{**project, "source_links": [source_by_id[source_id].get("url")
             for source_id in project.get("source_ids") or [] if source_id in source_by_id]}
            for project in projects]
    for row in rows:
        row.setdefault("announcement_count", len(set(row["source_links"])))
    payload = {**result, "schema_version": "project-results-v1", "projects": rows, "sources_file": "sources.json",
        "scope": scope.model_dump(mode="json"), "coverage": coverage,
        "source_checks": quality.get("source_checks") or [], "quality": quality}
    payload.pop("sources", None)
    outputs = {}
    for key, filename, value in (("json", "data.json", payload), ("sources_file", "sources.json", sources),
                                 ("trace_file", "trace.json", state.get("trace") or [])):
        path = out_dir / filename
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        outputs[key] = str(path)
    checks = {check.get("source_id"): check for check in quality.get("source_checks") or []}
    source_rows = [{**source, "source_id": source.get("source_id", index),
        "body_status": (source.get("metadata") or {}).get("body_status") or "未标注",
        "check": checks.get(source.get("source_id", index)),
        "review_scope": ("正文未取得或尚不足，仅为线索，不计作已确认项目；内容和状态见sources.json"
            if (source.get("metadata") or {}).get("body_status") in ("unknown", "insufficient", "snippet", "unread", "not_attempted", "list_excerpt")
            else "核验范围见check；已取得的内容和状态见sources.json，未作全文背书")}
        for index, source in enumerate(sources, 1)]
    explanations = [{"key": key, "value": value} for key, value in coverage.items()]
    explanations += [{"key": "排除来源", "value": value} for value in result.get("excluded") or []]
    evidence = [{"project_id": project.get("project_id"), **item}
                for project in projects for item in project.get("evidence") or []]
    if "xlsx" in spec.outputs:
        path = out_dir / "data.xlsx"
        display_rows = [{**project, **{key: "多公告存在差异，见差异列"
            for key in project.get("field_variants") or {} if project.get(key) is None}} for project in rows]
        for project, row in zip(rows, display_rows):
            if project.get("current_stage") is None and "current_stage" not in (project.get("field_variants") or {}):
                row["current_stage"] = "待核实"
            if project.get("amount") is None and "amount" not in (project.get("field_variants") or {}):
                row["amount"] = "项目级金额未确认"
        write_workbook(path, [("项目清单", display_rows, PROJECT_COLUMNS),
            ("来源核对", source_rows, {"source_id": "来源编号", "title": "标题", "url": "链接",
                "body_status": "正文取得状态", "content": "已取得的来源内容"}),
            ("证据明细", evidence, {"project_id": "项目标识", "source_id": "来源编号", "field": "字段", "quote": "原文依据"}),
            ("缺口说明", explanations, {"key": "项目", "value": "说明"})])
        outputs["xlsx"] = str(path)
    status = "已有结果核验通过" if passed else "已有结果尚未通过完整核验"
    gap = f"缺少 {max(0, target - count)} 个，不能称目标数量已达成。" if not target_met else "已达到目标数量。"
    merged_count = sum(row["announcement_count"] > 1 for row in rows)
    single_count = sum(row["announcement_count"] == 1 for row in rows)
    amount_pending = sum(row.get("amount") is None for row in rows)
    stage_pending = sum(row.get("current_stage") is None or bool(row.get("stage_note")) for row in rows)
    report = [f"# 项目发现与分析报告：{spec.intent}", "", f"- 已确认独立项目：{count}/{target}；{gap}",
        f"- {status}；核验范围是项目引用字段，已取得的正文、搜索线索及状态随 sources.json 保留。搜索线索不计作已确认项目。",
        f"- 公告归并：实际归并项目 {merged_count} 个；仅取得单份公告 {single_count} 个。",
        f"- 字段口径：项目级金额未确认 {amount_pending} 个，不代表标包金额未披露；当前阶段待核实 {stage_pending} 个，历史公告状态不能直接代表当前阶段。",
        "- 原文未披露字段标注“未披露”；核对说明见Excel/JSON，不推断金额、日期或项目阶段。本报告尚非正式交付。", "",
        "## 项目索引", "", "| 项目名称 | 原文链接 |", "| --- | --- |"]
    for project in rows:
        name = _text(project.get("project_name"))
        if len(name) > 100:
            name = name[:100] + "…"
        links = project.get("source_links") or []
        report.append("| " + name + " | " + _text(links[:1]) + " |")
    if coverage.get("review_mode_note"):
        report += ["", "核验范围：" + str(coverage["review_mode_note"])]
    if not projects:
        report += ["", "未取得可确认的符合范围项目；不能从本次检索结果推断官网没有相关项目。"]
    conflicts = [project["project_name"] for project in projects if project.get("field_variants")]
    if conflicts:
        report += ["", "多公告字段差异已保留：" + "；".join(conflicts)
                   + "。为空的差异字段尚不能确定，各版本及对应公告见 Excel/JSON。"]
    if state.get("analysis"):
        report += ["", "## 分析", "", state["analysis"]]
    discovery = coverage.get("discovery") or {}
    report += ["", "## 覆盖与局限", "",
        f"- 保留来源 {len(sources)} 条；排除记录 {len(result.get('excluded') or [])} 条；"
        f"尚未完成核对 {coverage.get('pending_sources', 0)} 条。",
        f"- 检索停止原因：{_text(coverage.get('search_stop_reason') or discovery.get('stop_reason') or coverage.get('stop_reason'))}；"
        f"核对停止原因：{_text(coverage.get('extraction_stop_reason'))}。",
        "- 检索覆盖不等于官网项目全量；完整字段、证据与缺口明细见 Excel 和 JSON。"]
    report_text = "\n".join(report) + "\n"
    path = out_dir / "report.md"
    path.write_text(report_text, encoding="utf-8")
    outputs.update(report_md=str(path), report_text=report_text)
    formats = "Excel、JSON和报告" if "xlsx" in outputs else "JSON和报告"
    message = f"已确认 {count}/{target} 个独立项目；{gap}\n{status}。已生成{formats}，来源内容及状态可下载；尚非正式交付。"
    return {"outputs": outputs, "reply": message, "error": None if passed else message,
        "grade": {"level": ("已核验" if target_met else "部分结果已核验") if passed else "未完成",
                  "score": quality.get("score"), "items": count, "collector": state.get("collector_used")},
        "project_result": {**result, "coverage": coverage}, "quality": {**quality, "passed": passed}}
