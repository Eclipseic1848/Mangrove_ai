"""配置中心路由：凭证/端点类配置的前端读写 + 连通性验证。

- 管理员/超管：GET /api/config 全量分组；PUT/DELETE /api/config/{key} 设置/重置全局覆盖（热生效）。
- 普通用户：GET /api/config/self 白名单内自助项（自己的 API Key/平台 Cookie，按用户隔离）；
  PUT/DELETE /api/config/self/{key}。
- POST /api/config/verify {"target": ...}：逐项/逐组连通验证；普通用户验证时套用其个人覆盖。

安全：密钥值只回掩码（尾4位），前端永远拿不到全文；键必须在 REGISTRY 白名单内。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.config import runtime_config as rc
from src.config.settings import settings
from src.config.user_ctx import set_user_overrides

from ..auth import get_current_user, get_store, is_admin_role, require_admin

router = APIRouter(prefix="/api/config", tags=["config"])

logger = logging.getLogger(__name__)


class ConfigValueIn(BaseModel):
    value: str


class VerifyIn(BaseModel):
    target: str  # 组名或键名，如 llm_deepseek / tavily_api_key / smtp / cookies:jd_cookie


# ---------- 模型列表（供前端下拉选择） ----------
@router.get("/models")
def list_models(user=Depends(get_current_user)):
    """返回各供应商可选模型：{provider: [model_id, ...]}，含默认模型标记。

    额外返回 local_urls（本地模型名→base_url 映射），供前端切换模型时联动更新地址。
    """
    from src.llm.provider import list_models as get_models
    from src.llm.provider import get_provider, LOCAL_MODELS
    models = get_models()
    prov = get_provider()
    document_models = [
        f"{provider}::{model}"
        for provider, provider_models in models.items()
        for model in provider_models
    ]
    result = {
        "models": {**models, "document": document_models},
        "default_provider": prov.default_provider,
        "available_providers": prov.available_providers(),
    }
    # LAN 地址属于平台内部拓扑。普通用户只需要友好模型目录，不应看到具体端点。
    if is_admin_role(user.get("role")):
        result["local_urls"] = LOCAL_MODELS
    return result


# ---------- 管理员：全局配置 ----------
@router.get("")
def list_config(admin=Depends(require_admin)):
    return {"groups": rc.describe(get_store())}


@router.put("/{key}")
def set_config(key: str, body: ConfigValueIn, admin=Depends(require_admin)):
    try:
        rc.set_global(get_store(), key, body.value, updated_by=admin["user_id"])
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"值格式非法：{e}")
    return {"ok": True, "key": key, "source": "override"}


@router.delete("/{key}")
def reset_config(key: str, admin=Depends(require_admin)):
    try:
        rc.reset_global(get_store(), key)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "key": key, "source": "env"}


# ---------- 反爬域名自动增补（P1-2）：查看/手动释放误判 ----------
@router.get("/domain-health")
def list_domain_health(admin=Depends(require_admin)):
    """列出当前因"非隐身引擎近期持续失败"被临时判定为疑似强反爬站的域名（见 _domain_health.py）。

    30 分钟内无新失败记录会自动过期解除；确认误判不想等的话用下面的 DELETE 立即释放。
    """
    from src.collectors._domain_health import list_flagged
    return {"flagged": list_flagged()}


@router.delete("/domain-health/{domain}")
def reset_domain_health(domain: str, admin=Depends(require_admin)):
    """手动释放某个域名的"疑似强反爬"判定——确认是误判时立即恢复正常按 tier 路由，不必等 30 分钟过期。"""
    from src.collectors._domain_health import reset
    reset(domain)
    return {"ok": True, "domain": domain}


# ---------- 普通用户：自助项（按用户隔离） ----------
@router.get("/self")
def list_self_config(user=Depends(get_current_user)):
    mine = get_store().config_all(user["user_id"]) or {}
    items = []
    for key in sorted(rc.USER_KEYS):
        meta = rc.REGISTRY[key]
        items.append({
            "key": key, "label": meta["label"], "secret": meta["secret"],
            "group": meta["group"],
            "set": key in mine,
            "value": rc.mask_value(key, mine.get(key)),
        })
    return {"items": items}


@router.put("/self/{key}")
def set_self_config(key: str, body: ConfigValueIn, user=Depends(get_current_user)):
    if key not in rc.USER_KEYS:
        raise HTTPException(status_code=403, detail="该项不支持按用户自助配置")
    if not body.value.strip():
        raise HTTPException(status_code=400, detail="值不能为空（清除请用删除）")
    get_store().config_set(user["user_id"], key, body.value.strip(), updated_by=user["user_id"])
    return {"ok": True, "key": key}


@router.delete("/self/{key}")
def reset_self_config(key: str, user=Depends(get_current_user)):
    if key not in rc.USER_KEYS:
        raise HTTPException(status_code=403, detail="该项不支持按用户自助配置")
    get_store().config_delete(user["user_id"], key)
    return {"ok": True, "key": key}


# ---------- 连通验证 ----------
async def _verify_llm(provider: str) -> str:
    from src.llm.provider import achat
    # 超时上限与 settings.llm_timeout 保持一致：本地思考模型（如 Qwen3.6）思考链长度不稳定，
    # 固定 60s 曾把"仍在思考、尚未超限"的正常情况误判为验证失败。
    out = await asyncio.wait_for(
        achat([{"role": "user", "content": "回复：OK"}], provider=provider, max_tokens=512),
        timeout=settings.llm_timeout,
    )
    return f"{provider} 模型连通（回复 {len(out or '')} 字）"


# mc_cookie_* 配置键 -> (中文平台名, MediaCrawler 平台代码)
_MC_COOKIE_PLATFORM = {
    "mc_cookie_dy": ("抖音", "dy"), "mc_cookie_xhs": ("小红书", "xhs"), "mc_cookie_wb": ("微博", "wb"),
    "mc_cookie_bili": ("B站", "bili"), "mc_cookie_zhihu": ("知乎", "zhihu"),
    "mc_cookie_ks": ("快手", "ks"), "mc_cookie_tieba": ("贴吧", "tieba"),
}

# 电商 Cookie 真实校验：访问一个必须登录才能访问的页面，看是否被重定向回登录页。
# 只看最终落地 URL 是否命中登录页特征，不解析页面正文（改版不影响判定），但登录跳转
# 策略本身变化时仍可能需要更新这里的 url/login_markers——如实标注维护成本，不假装稳定。
# 拼多多/淘宝反爬更激进，标 best_effort=True：请求被拦截时不误判为"Cookie 失效"，
# 而是回"无法判断"，避免让管理员误删一个其实还有效的 Cookie。
# 淘宝 2026-07-08 实测：探测请求（无真实浏览器 TLS/JS 指纹）会被淘宝 WAF 转发到内部边缘节点
# outmem.taobao.com 返回 500，连续 3 次复现一致——这是反爬拦截本身，和 Cookie 有效性无关，
# 原先按"没那么激进"标了 False，属于误判，改回 True。
_ECOMMERCE_PROBES = {
    "jd_cookie": {
        "cn": "京东",
        "url": "https://order.jd.com/center/list.action",
        "login_markers": ("passport.jd.com",),
        "best_effort": False,
    },
    "tb_cookie": {
        "cn": "淘宝/天猫",
        "url": "https://member1.taobao.com/member/fresh/account_setting.htm",
        "login_markers": ("login.taobao.com", "login.tmall.com"),
        "best_effort": True,
    },
    "pdd_cookie": {
        "cn": "拼多多",
        "url": "https://mobile.yangkeduo.com/user_setting.html",
        "login_markers": ("login.yangkeduo.com", "/login.html"),
        "best_effort": True,
    },
}

_ECOMMERCE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _classify_ecommerce_probe(
    landed_url: str, status_code: int, cn: str, login_markers: tuple, best_effort: bool,
) -> str:
    """纯函数：根据探测请求最终落地的 URL + 状态码判定 Cookie 状态，返回结论文案；
    判定为失效/无法判断时抛 RuntimeError（消息含"无法判断"四字表示"测不准，非确定失效"）。
    """
    if any(marker in landed_url for marker in login_markers):
        raise RuntimeError(f"{cn} 登录状态已失效，请重新导出 Cookie")
    if status_code != 200:
        hint = "，可能是反爬拦截而非 Cookie 失效" if best_effort else "，请稍后重试确认"
        raise RuntimeError(f"{cn} 无法判断 Cookie 状态（HTTP {status_code}）{hint}")
    return f"{cn} Cookie 有效（{landed_url}）"


async def _verify_ecommerce_cookie(cookie_key: str) -> str:
    """真实探测：带上 Cookie 访问一个必须登录才能访问的页面，交给 _classify_ecommerce_probe 判定。"""
    from src.config.user_ctx import effective

    probe = _ECOMMERCE_PROBES[cookie_key]
    cookie = (effective(cookie_key) or "").strip()
    if not cookie:
        raise RuntimeError(f"{probe['cn']} 未配置 Cookie")
    headers = {"User-Agent": _ECOMMERCE_UA, "Cookie": cookie}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(probe["url"], headers=headers)
    except Exception as e:  # noqa: BLE001 网络异常也是"测不了"，不该冒充"Cookie失效"
        raise RuntimeError(f"{probe['cn']} 探测请求失败：{e}")
    return _classify_ecommerce_probe(
        str(r.url), r.status_code, probe["cn"], probe["login_markers"], probe["best_effort"],
    )


async def _verify_mc_cookie(cookie_key: str) -> str:
    """真实探测：跑一次最小的 MediaCrawler 搜索，检验该平台 Cookie 是否还能登录。

    这是真实浏览器自动化（登录+搜索），不是轻量探活，耗时可能从十几秒到几分钟不等——
    前端对这类目标会先弹确认框（仿 Slack 侧效应验证），不会被误触。
    """
    from src.collectors.registry import get_registry
    from src.collectors.social_media_collector import _platform_cookie
    from src.conductor.task_spec import TaskSpec

    entry = _MC_COOKIE_PLATFORM.get(cookie_key)
    if not entry:
        raise RuntimeError("未知的平台 Cookie 项")
    platform_cn, platform_code = entry
    if not _platform_cookie(platform_code):
        raise RuntimeError(f"{platform_cn} 未配置 Cookie，无需探测（未配置时走扫码登录，不受此项影响）")
    collector = get_registry().get("mediacrawler")
    if collector is None or not collector.is_available():
        raise RuntimeError("MediaCrawler 未配置（MEDIACRAWLER_PATH），无法探测")
    spec = TaskSpec(intent="Cookie 连通验证", platforms=[platform_cn], keywords=["你好"], max_items=1)
    result = await collector.collect(spec)
    if result.success:
        return f"{platform_cn} Cookie 有效（真实登录并采到 {len(result.items)} 条数据）"
    msg = result.message or ""
    # 唯一真正"没崩溃、只是没搜到内容"的情况：进程正常退出但没解析出数据。
    # 其余（启动失败/运行超时/退出码非0的登录过期/验证码/频次/IP 等诊断）都是采集本身没跑成功，
    # 必须算验证失败——此前误把这些也归为"未产出数据"，会把真实故障显示成绿色的"未报错"。
    if msg == "MediaCrawler 未产出可解析结果":
        return f"{platform_cn} 登录未报错，但本次探测未产出数据（{msg}）"
    raise RuntimeError(msg or "MediaCrawler 采集失败，原因未知")


async def _verify_http(url: str, name: str, ok_codes=(200,)) -> str:
    lan = any(m in url for m in ("localhost", "127.", "192.168.", "10."))
    async with httpx.AsyncClient(timeout=10, trust_env=not lan) as c:
        r = await c.get(url)
        if r.status_code not in ok_codes:
            raise RuntimeError(f"{name} 返回 HTTP {r.status_code}")
    return f"{name} 可达"


# 7 个社媒 + 3 个电商，唯一权威列表：手动验证/定时巡检都只对这 10 个 key 落库到
# cookie_health；其余 verify 目标（email/slack/proxy/mysql 等）不是"某一个 Cookie"，
# 不落这张表。元组顺序即 Task 4 定时巡检的扫描顺序，cookie_health_scanner.py 直接
# import 这个元组，不重新声明一份重复列表（DRY）。
_COOKIE_HEALTH_KEYS = (
    "mc_cookie_xhs", "mc_cookie_dy", "mc_cookie_wb", "mc_cookie_bili", "mc_cookie_zhihu",
    "mc_cookie_ks", "mc_cookie_tieba", "jd_cookie", "tb_cookie", "pdd_cookie",
)


# CDP 模式常见的本机浏览器安装位置：MediaCrawler 自身会自动探测，这里只做轻量文件系统
# 检查（不起浏览器），提前告诉管理员“开了 CDP 但本机找不到浏览器”这种一开跑就会失败的情况。
_BROWSER_PATH_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


def _detect_local_browser() -> Optional[str]:
    """探测本机是否装了 Chrome/Edge：先查 PATH，再查常见安装目录，找到即返回可执行文件路径。"""
    for exe in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(exe)
        if found:
            return found
    for p in _BROWSER_PATH_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _record_cookie_health(key: str, status: str, message: str, checked_by: str) -> None:
    """验证结果顺带落库，供配置中心展示"最后一次验证状态"；旁路副作用，落库失败只记日志。"""
    try:
        get_store().cookie_health_set(key, status, message[:500], checked_by)
    except Exception as e:  # noqa: BLE001 落库失败不该影响验证结果本身返回给前端
        logger.warning("cookie_health 写入失败 key=%s: %s", key, e)


async def _verify_target(target: str) -> str:
    """按目标执行连通验证，返回人话结论；失败抛异常。"""
    if target in ("llm_deepseek", "deepseek_api_key"):
        return await _verify_llm("deepseek")
    if target in ("llm_qwen", "qwen_api_key"):
        return await _verify_llm("qwen")
    if target == "llm_local":
        return await _verify_llm("local")
    if target in ("search", "tavily_api_key"):
        if not settings.tavily_api_key:
            raise RuntimeError("Tavily API Key 未配置")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{settings.tavily_base_url}/search",
                             json={"api_key": settings.tavily_api_key, "query": "ping", "max_results": 1})
            if r.status_code != 200:
                raise RuntimeError(f"Tavily 返回 HTTP {r.status_code}: {r.text[:120]}")
        return "Tavily 检索可用"
    if target == "anysearch":
        if not settings.anysearch_api_key:
            raise RuntimeError("AnySearch API Key 未配置")
        from src.collectors._anysearch import search as anysearch_search
        items = await anysearch_search("ping test", max_results=1)
        if items:
            return f"AnySearch 连通（返回 {len(items)} 条）"
        raise RuntimeError("AnySearch 返回空结果，请检查 Key 是否有效")
    if target == "searxng_base_url":
        return await _verify_http(settings.searxng_base_url, "SearXNG")
    if target in ("firecrawl_base_url", "firecrawl_api_key"):
        return await _verify_http(settings.firecrawl_base_url, "Firecrawl")
    if target == "rsshub_base_url":
        return await _verify_http(settings.rsshub_base_url, "RSSHub")
    if target in ("email", "smtp"):
        from src.conductor.email_sender import verify_connection
        await asyncio.to_thread(verify_connection)
        return "SMTP 连接并登录成功（未发送邮件）"
    if target == "slack":
        from src.conductor.slack_sender import is_slack_configured, send_report, unavailable_reason
        if not is_slack_configured():
            raise RuntimeError(unavailable_reason())
        await send_report("Mangrove 配置验证", "配置中心连通性测试消息，可忽略。")
        return "已向 Slack 发送测试消息"
    if target == "semantic":
        from src.memory.embeddings import embed_texts_with_model, is_rerank_configured, rerank_scores
        got = await asyncio.to_thread(embed_texts_with_model, ["连通验证"])
        if not (got and got[1]):
            raise RuntimeError("embedding 端点不可用")
        msg = f"embedding 可用（{got[0]}）"
        if is_rerank_configured():
            scores = await asyncio.to_thread(rerank_scores, "连通验证", ["连通验证"])
            msg += "；rerank 可用" if scores else "；rerank 不可用"
        return msg
    if target == "mysql":
        import pymysql
        conn = await asyncio.to_thread(pymysql.connect, host=settings.mysql_host,
                                       port=settings.mysql_port, user=settings.mysql_user,
                                       password=settings.mysql_password, database=settings.mysql_database,
                                       connect_timeout=8)
        conn.close()
        return "MySQL 连接成功"
    if target.startswith("mc_cookie_"):
        return await _verify_mc_cookie(target)
    if target in ("jd_cookie", "tb_cookie", "pdd_cookie"):
        return await _verify_ecommerce_cookie(target)
    if target == "cookies":
        return "Cookie 已保存（登录态有效性以实际采集结果为准，过期时任务会提示重新导出）"
    if target == "proxy":
        return "代理凭证已保存（有效性在启用代理的采集任务中校验）"
    if target == "mc_cdp":
        if not settings.mc_enable_cdp_mode:
            return "CDP 模式未启用，采集仍使用 MediaCrawler 自带的 Chromium"
        found = await asyncio.to_thread(_detect_local_browser)
        if found:
            return f"CDP 模式已启用，检测到本机浏览器：{found}"
        raise RuntimeError("CDP 模式已启用，但本机未检测到 Chrome/Edge，采集时会启动失败，请先安装浏览器")
    if target == "checkpoint":
        if not settings.checkpoint_enabled:
            raise RuntimeError("断点续跑未启用")
        from src.conductor.graph import probe_checkpoint_storage
        db_path = await asyncio.to_thread(probe_checkpoint_storage)
        return f"本地存储目录可写（{db_path}）"
    if target == "cookie_health":
        if not settings.cookie_health_scan_enabled:
            return "定时巡检未启用"
        return f"定时巡检已启用，每 {settings.cookie_health_scan_interval_hours} 小时扫描一次"
    if target == "library_dedup":
        if not settings.library_dedup_scan_enabled:
            return "知识库巡检未启用"
        return (
            f"知识库巡检已启用，每 {settings.library_dedup_scan_interval_hours} 小时扫描一次"
            f"（停滞草稿阈值 {settings.library_stale_draft_days} 天，"
            f"每轮最多合并 {settings.library_dedup_scan_max_merges_per_run} 对）"
        )
    raise RuntimeError(f"暂不支持验证该目标: {target}")


@router.post("/verify")
async def verify_config(body: VerifyIn, user=Depends(get_current_user)):
    """连通验证。普通用户验证时套用其个人覆盖（验的是"他自己任务会用到的配置"）。"""
    is_admin = user.get("role") in ("admin", "super_admin")
    if not is_admin:
        # 普通用户只允许验证自助项相关目标
        allowed = {"llm_deepseek", "llm_qwen", "deepseek_api_key", "qwen_api_key", "cookies"}
        if body.target not in allowed and not body.target.startswith(("mc_cookie_", "jd_cookie", "tb_cookie", "pdd_cookie")):
            raise HTTPException(status_code=403, detail="无权验证该项")
        mine = get_store().config_all(user["user_id"]) or {}
        set_user_overrides({k: v for k, v in mine.items() if k in rc.USER_KEYS})
    try:
        detail = await _verify_target(body.target)
        # 只有管理员验证时测的才是全局配置中心展示的那份值；普通用户验证的是自己的
        # 个人覆盖（见上面 set_user_overrides），写进全局表会污染管理员看到的状态。
        if is_admin and body.target in _COOKIE_HEALTH_KEYS:
            _record_cookie_health(body.target, "valid", detail, checked_by="manual")
        return {"ok": True, "detail": detail}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 验证失败把原因回前端
        detail = str(e)[:300]
        if is_admin and body.target in _COOKIE_HEALTH_KEYS:
            status = "unknown" if "无法判断" in detail else "invalid"
            _record_cookie_health(body.target, status, detail, checked_by="manual")
        return {"ok": False, "detail": detail}
