"""产出节点：生成 Markdown 报告 / JSON 落盘，并处理入库（HITL 门控）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from src.config.settings import PROJECT_ROOT, settings

from ..anomaly import detect_anomalies
from ..db_writer import write_items
from ..email_sender import is_email_configured, parse_recipients, send_report
from ..email_sender import unavailable_reason as email_unavailable_reason
from ..slack_sender import is_slack_configured
from ..slack_sender import unavailable_reason as slack_unavailable_reason
from ..slack_sender import send_report as send_slack
from ..state import ConductorState
from ..task_spec import OutputFormat, TaskSpec

logger = logging.getLogger(__name__)


def _assemble_report(spec: TaskSpec, state: ConductorState) -> str:
    """拼装最终 Markdown 报告：元信息 + 分析正文 + 数据附录。"""
    data = state.get("cleaned_dataset", [])
    analysis = state.get("analysis")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# 数据采集分析报告：{spec.intent}",
        "",
        f"- 生成时间：{now}",
        f"- 目标平台：{', '.join(spec.platforms) or '通用网页'}",
        f"- 关键词：{', '.join(spec.keywords) or '—'}",
        f"- 数据类型：{spec.data_type.value}",
        f"- 采集引擎：{state.get('collector_used') or '—'}",
        f"- 采集条数：{len(data)}",
        "",
    ]
    coverage = state.get("target_coverage") or {}
    if coverage:
        lines += [
            f"- 目标校验：{coverage.get('verified', 0)}/{coverage.get('total', 0)}",
            f"- 视频证据就绪：{coverage.get('evidence_ready', 0)}/{coverage.get('total', 0)}",
            "",
        ]
    bundles = state.get("evidence_bundles") or []
    if bundles:
        lines += ["## 视频证据来源", ""]
        for bundle in bundles:
            target = bundle.get("target") or {}
            kinds = "、".join(source.get("type", "") for source in bundle.get("sources") or []) or "无"
            status = "已就绪" if bundle.get("ready") else f"不足：{bundle.get('reason') or '未知原因'}"
            lines.append(f"- {target.get('requested_url') or '视频目标'}：{status}（来源：{kinds}）")
        lines.append("")
    if analysis:
        lines += ["## 分析", "", analysis, ""]
    # 数据附录（截断展示，避免报告过长）
    lines += ["## 数据附录（节选）", ""]
    for i, item in enumerate(data[:20], 1):
        title = item.get("title") or item.get("url") or f"条目{i}"
        url = item.get("url", "")
        snippet = (item.get("content") or "").strip().replace("\n", " ")[:300]
        lines.append(f"{i}. **{title}**")
        if url:
            lines.append(f"   - 链接：{url}")
        if snippet:
            lines.append(f"   - 摘录：{snippet}")
    lines.append("")
    return "\n".join(lines)


def _assemble_grade(state: ConductorState) -> Dict[str, Any]:
    """任务质量分级：以 Checker 分数为主，叠加采集健康度。"""
    q = state.get("quality") or {}
    score = q.get("score")
    n = len(state.get("cleaned_dataset") or [])
    if score is None:
        level = "未评估"
    elif score >= 85:
        level = "A 优秀"
    elif score >= 70:
        level = "B 良好"
    elif score >= 50:
        level = "C 中等"
    else:
        level = "D 待改进"
    return {"level": level, "score": score, "items": n, "collector": state.get("collector_used")}


async def output_node(state: ConductorState) -> Dict[str, Any]:
    spec = state["task_spec"]
    task_id = state.get("task_id") or datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir: Path = PROJECT_ROOT / "downloads" / task_id
    out_dir.mkdir(parents=True, exist_ok=True)

    data = state.get("cleaned_dataset", [])
    outputs: Dict[str, Any] = {}

    # 1) JSON 落盘（始终保留原始结构化数据）
    json_path = out_dir / "data.json"
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    outputs["json"] = str(json_path)

    evidence_bundles = state.get("evidence_bundles") or []
    if evidence_bundles:
        evidence_path = out_dir / "evidence.json"
        evidence_path.write_text(json.dumps(evidence_bundles, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs["evidence_json"] = str(evidence_path)

    # 2) Markdown 报告
    report_text = _assemble_report(spec, state)
    report_path = out_dir / "report.md"
    report_path.write_text(report_text, encoding="utf-8")
    outputs["report_md"] = str(report_path)
    outputs["report_text"] = report_text

    # 2.5) 执行轨迹落盘（可观测性）：截至 output 之前各节点的耗时与摘要
    trace = state.get("trace") or []
    if trace:
        trace_path = out_dir / "trace.json"
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs["trace_file"] = str(trace_path)

    # 3) 入库（HITL 门控）：未确认则挂起，由前端按钮触发实际写入
    if spec.needs_db_write():
        if state.get("approved_db_write"):
            try:
                n = write_items(task_id, data, source=spec.db_target or spec.intent)
                outputs["db"] = f"已写入 {n} 条到本地 SQLite"
            except Exception as e:
                outputs["db"] = f"入库失败：{e}"
        else:
            outputs["db_pending"] = True

    # 3.5) 邮件发送（HITL 门控）：未确认则挂起，由前端按钮触发实际发送
    if spec.needs_email():
        recipients = parse_recipients(spec.email_to)
        if not is_email_configured():
            outputs["email"] = f"{email_unavailable_reason()}，已跳过发送"
        elif not recipients:
            outputs["email"] = "未识别到有效收件人邮箱，已跳过发送"
        elif state.get("approved_email"):
            try:
                subject = f"【数据采集分析报告】{spec.intent}"[:120]
                attaches = [p for p in (outputs.get("report_md"), outputs.get("json")) if p]
                send_report(recipients, subject, report_text, attachments=attaches)
                outputs["email"] = f"已发送报告邮件至 {', '.join(recipients)}"
            except Exception as e:
                outputs["email"] = f"邮件发送失败：{e}"
        else:
            outputs["email_pending"] = True
            outputs["email_to"] = recipients

    # 3.6) Slack 推送（HITL 门控）：未确认则挂起，由前端按钮触发实际推送
    if spec.needs_slack():
        if not is_slack_configured():
            outputs["slack"] = f"{slack_unavailable_reason()}，已跳过推送"
        elif state.get("approved_slack"):
            try:
                title = f"数据采集分析报告：{spec.intent}"
                await send_slack(title, state.get("analysis") or report_text)
                outputs["slack"] = "已推送报告到 Slack 频道"
            except Exception as e:
                outputs["slack"] = f"Slack 推送失败：{e}"
        else:
            outputs["slack_pending"] = True

    # 4) 用户回复
    reply_lines = (["未生成视频内容结论：未取得足够的可验证证据。"] if state.get("analysis_source") == "video_evidence_blocked" else [f"已完成采集与分析，共 {len(data)} 条数据（采集引擎：{state.get('collector_used') or '—'}）。"])
    # 采集过程说明（如小红书登录过期、已改用全网兜底等），置顶告知用户
    for note in state.get("collector_notes") or []:
        reply_lines.append(note)
    if OutputFormat.REPORT_MD in spec.outputs:
        reply_lines.append("已生成 Markdown 分析报告。")
    if outputs.get("db_pending"):
        reply_lines.append("⚠️ 你要求入库——这是敏感操作，请点击下方「确认入库」按钮后我再写入数据库。")
    elif outputs.get("db"):
        reply_lines.append(outputs["db"])
    if outputs.get("email_pending"):
        reply_lines.append(
            f"⚠️ 你要求发邮件给 {', '.join(outputs.get('email_to') or [])}——这是外向敏感操作，"
            "请点击下方「确认发送邮件」按钮后我再发送。"
        )
    elif outputs.get("email"):
        reply_lines.append(outputs["email"])
    if outputs.get("slack_pending"):
        reply_lines.append("⚠️ 你要求推送到 Slack——这是外向敏感操作，请点击下方「确认推送 Slack」按钮后我再推送。")
    elif outputs.get("slack"):
        reply_lines.append(outputs["slack"])

    # 质量评估（Checker）：通过给分，未通过附问题清单（仅提示，不重跑）
    quality = state.get("quality")
    if quality:
        if quality.get("passed"):
            reply_lines.append(f"✅ 质量自评：{quality.get('score')} 分。{quality.get('summary') or ''}".rstrip())
        else:
            issues = quality.get("issues") or []
            tip = f"⚠️ 质量自评：{quality.get('score')} 分，建议关注："
            tip += "；".join(issues[:3]) if issues else (quality.get("summary") or "质量偏低")
            reply_lines.append(tip)

    # 质量分级 + 执行轨迹（可观测性）
    grade = _assemble_grade(state)
    grade_line = f"📊 质量分级：{grade['level']}"
    if grade["score"] is not None:
        grade_line += f"（{grade['score']} 分）"
    grade_line += f" · 采集 {grade['items']} 条 · 引擎 {grade['collector'] or '—'}"
    reply_lines.append(grade_line)
    if trace:
        reply_lines.append("🔍 执行轨迹：" + " → ".join(f"{t['node']}({t['ms']}ms)" for t in trace))

    # 异常检测（可观测性）：确定性规则标出本次运行的异常信号
    anomalies = detect_anomalies(state)
    grade["anomalies"] = anomalies
    if anomalies:
        reply_lines.append("🚨 异常检测：" + "；".join(anomalies))

    # 自学习增强：本次复用的已学模板因使用统计达标转正 / 不达标淘汰
    tstatus = state.get("template_status")
    if tstatus:
        if tstatus.get("status") == "active":
            reply_lines.append(f"⭐ 已学模板「{tstatus.get('slug')}」表现达标，已从草稿转正为正式模板。")
        elif tstatus.get("status") == "retired":
            reply_lines.append(f"🗑️ 已学模板「{tstatus.get('slug')}」质量持续偏低，已淘汰、后续不再复用。")

    # 自学习阶段2：Checker 通过且走兜底时已在 checker 节点自动沉淀模板
    saved = state.get("template_saved")
    if saved:
        reply_lines.append(f"📑 已自动沉淀模板「{saved.get('title')}」（草稿，多次达标后自动转正），下次同类任务自动复用。")
    # 阶段1 兜底：走了通用兜底但未自动沉淀（Checker 关闭/未通过）时，提议手动沉淀
    elif (
        settings.template_learning_enabled
        and state.get("analysis_source") == "fallback"
        and state.get("analysis")
    ):
        reply_lines.append(
            "💡 本次用的是通用结构。若你常做这类任务，可点下方「沉淀为模板」让我记住它的报告结构，下次自动复用。"
        )
        outputs["template_suggest"] = True

    return {"outputs": outputs, "reply": "\n".join(reply_lines), "grade": grade}
