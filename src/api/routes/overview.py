"""概览仪表盘路由：聚合采集器/模型/调度器/模板/会话等状态供首页展示。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from src.config.settings import settings
from src.llm import available_providers, list_models
from src.memory import load_templates

from ..auth import get_current_user, get_store
from ..services import get_schedule_store

router = APIRouter(prefix="/api/overview", tags=["overview"])


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

    # 模板（全局共享）按状态统计
    templates = load_templates()
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
