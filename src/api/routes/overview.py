"""概览仪表盘路由：聚合采集器/模型/调度器/模板/会话等状态供首页展示。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query

from src.config.settings import settings
from src.llm import available_providers, list_models
from src.memory import load_templates
from src.timezone import now as beijing_now, timestamp, BEIJING

from ..auth import get_current_user, get_store
from ..services import get_schedule_store

router = APIRouter(prefix="/api/overview", tags=["overview"])


def _timestamp(value: str) -> str:
    return timestamp(value)


@router.get("/activity")
def activity(
    user=Depends(get_current_user),
    filter: Literal["all", "active", "attention", "completed"] = "all",
    offset: int = Query(0, ge=0),
    limit: int = Query(6, ge=1, le=100),
):
    store = get_store()
    now = beijing_now()
    # 所有角色均只聚合当前 Owner，不将管理员权限当作业务内容读取权限。
    items = [
        {"id": row["task_id"], "kind": "task", "title": row["title"],
         "status": row["status"], "updated_at": _timestamp(row["updated_at"]),
         "completed_at": _timestamp(row["completed_at"]) if row.get("completed_at") else None}
        for row in store.list_workspace_activity(user["user_id"])
    ] + [
        {"id": row["conv_id"], "kind": "conversation", "title": row["title"],
         "status": row["status"], "updated_at": _timestamp(row["updated_at"]),
         "completed_at": _timestamp(row["completed_at"]) if row.get("completed_at") else None}
        for row in store.list_chat_history(user["user_id"], include_activity_time=True)
    ]
    groups = {
        "active": {"queued", "running", "cancelling", "pausing"},
        "attention": {"needs_input", "candidate_ready", "paused", "failed"},
        "completed": {"completed"},
    }
    def matches(row, group):
        if group == "all":
            return True
        if row["status"] not in groups[group]:
            return False
        # 完成时间取终态事件/末条回复，不把重命名时间冒充完成时间。
        completed = datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None
        # 历史无时区数据保留展示，但不能冒充精确的近七天统计。
        return group != "completed" or bool(completed and completed.tzinfo and now - timedelta(days=7) <= completed <= now)

    stats = {key: sum(matches(row, key) for row in items) for key in groups}
    selected = [row for row in items if matches(row, filter)]
    selected.sort(key=lambda row: (row["updated_at"], row["kind"], row["id"]), reverse=True)
    return {"stats": stats, "items": selected[offset:offset + limit], "total": len(selected),
            "updated_at": beijing_now().isoformat()}


@router.get("/services")
def service_summary(user=Depends(get_current_user)):
    """首页只读配置和已有检查证据；不加载采集器，也不触发外部探测。"""
    health = get_store().cookie_health_all()
    provider = (settings.search_provider or "auto").lower()
    # 与搜索采集器的纯配置选择一致：其余模式保留无需 Key 的 DDG。
    search_configured = bool(settings.tavily_api_key) if provider == "tavily" else bool(settings.searxng_base_url) if provider == "searxng" else True
    platforms = {
        "xiaohongshu": "mc_cookie_xhs", "weibo": "mc_cookie_wb", "douyin": "mc_cookie_dy",
        "bilibili": "mc_cookie_bili", "zhihu": "mc_cookie_zhihu", "kuaishou": "mc_cookie_ks",
        "tieba": "mc_cookie_tieba", "jd": "jd_cookie", "taobao": "tb_cookie", "pdd": "pdd_cookie",
    }
    return {
        "cookies": [{"platform": platform, "status": health.get(key, {}).get("status", "unknown"),
                     "checked_at": _timestamp(health[key]["checked_at"]) if key in health else None}
                    for platform, key in platforms.items()],
        "services": [
            {"key": "search", "label": "搜索采集", "configured": search_configured, "enabled": True},
            {"key": "email", "label": "邮件", "configured": bool(settings.smtp_host and settings.smtp_user and settings.smtp_password), "enabled": bool(settings.smtp_enabled)},
            {"key": "slack", "label": "Slack", "configured": bool(settings.slack_webhook_url or (settings.slack_bot_token and settings.slack_channel_id)), "enabled": bool(settings.slack_enabled)},
            {"key": "embedding", "label": "知识检索", "configured": bool(settings.embedding_base_url or settings.qwen_api_key), "enabled": bool(settings.embedding_enabled)},
        ],
        "scheduler_enabled": bool(settings.scheduler_enabled),
        "updated_at": beijing_now().isoformat(),
    }


@router.get("/schedules")
def schedule_summary(user=Depends(get_current_user)):
    fields = ("task_id", "name", "status", "trigger_type", "cron_expr", "interval_seconds", "run_count", "last_success", "time_zone")
    result = []
    for plan in get_schedule_store().list_active(owner_user_id=user["user_id"]):
        item = {key: plan.get(key) for key in fields}
        item["name"] = plan.get("name") or "未命名计划"
        for key in ("run_at", "next_run_at", "last_run_at"):
            item[key] = _timestamp(plan[key]) if plan.get(key) else None
            if item[key] and key != 'last_run_at' and plan.get('time_zone') == 'Asia/Shanghai':
                parsed = datetime.fromisoformat(item[key])
                if parsed.tzinfo is None:
                    item[key] = parsed.replace(tzinfo=BEIJING).isoformat()
        result.append(item)
    return result


@router.get("")
def overview(user=Depends(get_current_user)):
    # 采集器健康（导入即注册）
    import src.collectors  # noqa: F401  确保采集器完成注册
    from src.collectors.registry import get_registry

    collectors = [
        {"name": c.name, "tier": c.tier, "available": bool(c.is_available())}
        for c in sorted(get_registry().values(), key=lambda x: x.tier)
    ]

    # 搜索后端健康（关键词任务的核心依赖）
    # backends: 可用后端名列表（前端直接渲染为字符串，保持向后兼容）
    # backends_detail: 结构化详情（含优先级/类型/中文标签），供后续前端升级使用
    from src.collectors.search_collector import SearchDiscoveryCollector
    _search = SearchDiscoveryCollector()
    _disc = _search._discovery_backends()
    has_anysearch = _search._use_anysearch()
    has_tavily = _search._use_tavily()
    has_searxng = "searxng" in _disc
    backends_available = (["anysearch"] if has_anysearch else []) + _disc + (["tavily"] if has_tavily else [])
    search_health = {
        "backends": backends_available,
        "has_html_fallback": True,  # Baidu/Bing/Google HTML 终极兜底
        "status": "good" if backends_available else "degraded",
        # 结构化详情（供未来前端升级，当前前端使用 backends 字符串列表）
        "backends_detail": [
            {"name": "anysearch", "label": "AnySearch（API·16领域·内联正文）", "available": has_anysearch, "type": "inline"},
            {"name": "tavily", "label": "Tavily（API·内联正文）", "available": has_tavily, "type": "inline"},
            {"name": "searxng", "label": "SearXNG（自托管·聚合百度/Bing）", "available": has_searxng, "type": "discovery"},
            {"name": "ddg", "label": "DuckDuckGo（免费·免Key）", "available": True, "type": "discovery"},
            {"name": "baidu_html", "label": "百度 HTML 兜底", "available": True, "type": "fallback"},
            {"name": "bing_html", "label": "必应 HTML 兜底", "available": True, "type": "fallback"},
            {"name": "google_html", "label": "谷歌 HTML 兜底（需VPN）", "available": True, "type": "fallback"},
        ],
    }

    # 采集器指标（当前进程累计）
    from src.collectors._metrics import snapshot as metrics_snapshot
    collector_metrics = metrics_snapshot()

    # Cookie/登录态状态：与 MediaCrawler/电商采集器实际支持的平台集合保持一致
    # （快手/贴吧此前 MediaCrawler 已支持但缺 Cookie 配置项，2026-07-06 补齐；
    #  淘宝/拼多多此前显示但 settings 无对应字段、恒为 False，同批补齐为真实配置）。
    # 2026-07-08：由"是否配置了 Cookie"改为"最近一次验证是否通过"——配置了但从未验证/
    # 验证失败的也按暂不可信处理（灰），和配置中心 Cookie 健康状态记录读同一张表，
    # 管理员在配置中心点一次验证，这里刷新页面即可看到最新结果。
    cookie_health = get_store().cookie_health_all()
    _cookie_platform_key = {
        "xiaohongshu": "mc_cookie_xhs", "weibo": "mc_cookie_wb", "douyin": "mc_cookie_dy",
        "bilibili": "mc_cookie_bili", "zhihu": "mc_cookie_zhihu", "kuaishou": "mc_cookie_ks",
        "tieba": "mc_cookie_tieba", "jd": "jd_cookie", "taobao": "tb_cookie", "pdd": "pdd_cookie",
    }
    cookie_status = {
        platform: cookie_health.get(key, {}).get("status") == "valid"
        for platform, key in _cookie_platform_key.items()
    }

    # 统计与模板页使用相同范围，不能把其他 Owner 的个人原件计入。
    templates = load_templates(owner_id=user["user_id"])
    tpl_stats = {"total": len(templates), "active": 0, "draft": 0, "retired": 0}
    for t in templates:
        st = (t.get("status") or "active").lower()
        if st in tpl_stats:
            tpl_stats[st] += 1

    # 方案 D：记忆命中埋点统计（命中率与降级路径分布）
    try:
        memory_hit_stats = get_store().memory_hit_log_stats()
    except Exception:
        memory_hit_stats = []

    store = get_store()
    active_tasks = get_schedule_store().list_active(owner_user_id=user["user_id"])

    # 正文提取链可用性
    _traf_ok, _read_ok = False, False
    try:
        import trafilatura; _traf_ok = True  # noqa: F401
    except Exception:
        pass
    try:
        import readability; _read_ok = True  # noqa: F401
    except Exception:
        pass
    extraction_chain = [
        {"name": "trafilatura", "label": "Trafilatura（F1=0.958, Markdown）", "available": _traf_ok},
        {"name": "readability", "label": "readability-lxml（F1=0.922）", "available": _read_ok},
        {"name": "html_to_text", "label": "html_to_text（正则兜底）", "available": True},
    ]

    # 反爬加固
    _scr_ok = False
    try:
        import scrapling; _scr_ok = True  # noqa: F401
    except Exception:
        pass
    from src.collectors.platforms import ANTI_SCRAPE_DOMAINS
    network = {
        "rate_limiting": f"{settings.collector_min_interval_seconds}s~{settings.collector_max_interval_seconds}s 随机",
        "ua_rotation": settings.collector_rotate_ua,
        "smart_routing": True,  # smart_client 国内外智能分流，恒可用
        "retry": f"最多{settings.collector_max_retries}次，退避{settings.collector_retry_base_seconds}s~{settings.collector_retry_max_seconds}s",
    }
    anti_scrape = {
        "camoufox": _scr_ok and settings.scrapling_stealth_enabled,
        "shortcut_domains": len(ANTI_SCRAPE_DOMAINS),
    }

    return {
        "collectors": collectors,
        "providers": {"available": available_providers(), "catalog": list_models()},
        "scheduler": {"enabled": settings.scheduler_enabled, "active_count": len(active_tasks)},
        "templates": tpl_stats,
        "conversations": len(store.list_conversations(user["user_id"])),
        "connectors": {
            "email": bool(settings.smtp_host and settings.smtp_user),
            "slack": bool(settings.slack_webhook_url),
            "embedding": bool(settings.embedding_enabled),
            "checkpoint": bool(settings.checkpoint_enabled),
        },
        # 上面 connectors 表示"是否已配置凭证"；这里额外给出"管理员是否开启该服务"的独立开关状态
        # （关闭后即使凭证还在也不会真的发送/生效），供设置页"连接器/增强"卡片渲染开关。
        "connectors_enabled": {
            "email": bool(settings.smtp_enabled),
            "slack": bool(settings.slack_enabled),
            "embedding": bool(settings.embedding_enabled),
            "checkpoint": bool(settings.checkpoint_enabled),
        },
        "search_health": search_health,
        "collector_metrics": collector_metrics,
        "cookie_status": cookie_status,
        "extraction_chain": extraction_chain,
        "network": network,
        "anti_scrape": anti_scrape,
        "memory_hit_stats": memory_hit_stats,
    }
