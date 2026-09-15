"""只发布用户需要的执行事实，不透出推理、凭据或原始日志。"""
from langgraph.config import get_stream_writer
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

LABELS = {
    "intent": "理解需求", "planner": "制定处理计划", "target_resolve": "识别资料来源",
    "router": "准备采集", "collect": "采集数据", "video_enrich": "提取视频资料",
    "clean": "整理数据", "analyze": "分析资料", "checker": "核对分析结果",
    "output": "生成报告", "schedule": "整理自动任务要求",
}


def source_links(items, status: str) -> list[dict]:
    """来源仅作用户点击引用；不公开凭据、本机地址或伪造全文读取状态。"""
    links = []
    seen = set()
    for item in items:
        raw = str(item.get("url") or "").strip()
        if any(ord(char) < 32 for char in raw):
            continue
        try:
            url = urlsplit(raw)
            host = url.hostname or ""
            if url.scheme not in {"http", "https"} or not host or url.username or url.password:
                continue
            if "." not in host or host.endswith((".localhost", ".local", ".internal")):
                continue
            try:
                if not ip_address(host).is_global:
                    continue
            except ValueError:
                pass
            # 某些采集链接携带临时鉴权参数；只展示可公开的来源地址，不外发凭据。
            query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
                     if not any(word in key.lower() for word in ("token", "key", "signature", "auth", "password", "secret", "cookie", "session", "credential"))]
            target = urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), ""))
        except ValueError:
            continue
        if target in seen:
            continue
        seen.add(target)
        links.append({"url": target, "title": str(item.get("title") or host)[:200], "status": status})
    return links


def emit_progress(node: str, status: str, summary: str, *, sources=None):
    try:
        writer = get_stream_writer()
    except RuntimeError:
        # 单独调用节点时没有流式宿主，不影响原执行行为。
        return
    writer({"node": node, "status": status, "label": LABELS.get(node, "处理任务"), "summary": summary,
            **({"sources": sources} if sources else {})})


def completed_summary(name: str, result: dict) -> str:
    if result.get("error"):
        return "本阶段未完成，请查看任务提示。"
    if result.get("needs_clarification"):
        return "需要补充信息，请查看智能体的问题。"
    if name == "collect":
        return f"已取得 {len(result.get('raw_dataset') or [])} 条资料。"
    if name == "clean":
        return f"整理后保留 {len(result.get('cleaned_dataset') or [])} 条资料用于分析。"
    if name == "checker" and result.get("checker_feedback"):
        return "发现需要修正的内容，正在安排重新分析。"
    return f"{LABELS.get(name, '本阶段')}已完成。"
