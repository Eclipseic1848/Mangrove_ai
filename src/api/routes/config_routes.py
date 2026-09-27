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
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.config import runtime_config as rc
from src.config.settings import settings
from src.config.user_ctx import set_user_overrides, user_overrides_context

from ..auth import get_current_user, get_store, is_admin_role, require_admin

router = APIRouter(prefix="/api/config", tags=["config"])

logger = logging.getLogger(__name__)


class ConfigValueIn(BaseModel):
    value: str


class ConfigBatchIn(BaseModel):
    values: dict[str, str]


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
@contextmanager
def _audit_config_changes(request, keys):
    # 与既有运行态写锁共用边界，避免并发保存导致前后值错配。
    with rc._CONFIG_LOCK:
        before = {}
        for key in keys:
            meta = rc.REGISTRY.get(key)
            if not meta:
                continue
            value = None if meta["secret"] else getattr(settings, key, None)
            before[key] = value if isinstance(value, (bool, int, float)) else None
        yield
        if request is not None:
            request.state.operations_changes = [
                {"field": rc.REGISTRY[key]["label"], "before": value if value is not None else "不记录",
                 "after": getattr(settings, key) if value is not None else "已修改"}
                for key, value in before.items()
            ]


@router.get("")
def list_config(admin=Depends(require_admin)):
    return {"groups": rc.describe(get_store())}


@router.put("/batch")
def set_config_batch(body: ConfigBatchIn, admin=Depends(require_admin), request: Request = None):
    try:
        with _audit_config_changes(request, body.values):
            rc.set_global_many(get_store(), body.values, updated_by=admin["user_id"])
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.put("/{key}")
def set_config(key: str, body: ConfigValueIn, admin=Depends(require_admin), request: Request = None):
    try:
        with _audit_config_changes(request, [key]):
            rc.set_global(get_store(), key, body.value, updated_by=admin["user_id"])
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"值格式非法：{e}")
    return {"ok": True, "key": key, "source": "override"}


@router.delete("/{key}")
def reset_config(key: str, admin=Depends(require_admin), request: Request = None):
    try:
        with _audit_config_changes(request, [key]):
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

# 电商登录页探测：明确跳回登录页可判定未认证，HTTP 200 只证明页面可达。
# 尚未接通稳定身份接口的平台保持 unknown，不靠页面壳或搜索结果证明账号身份。
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


class CookieProbeError(RuntimeError):
    """明确的认证证据才标失效，其他失败保留为未知。"""

    def __init__(self, message: str, *, reason: str, status: str = "unknown"):
        super().__init__(message)
        self.reason = reason
        self.status = status


def _cookie_failure_status(error: Exception) -> str:
    return error.status if isinstance(error, CookieProbeError) else "unknown"


def _cookie_verification_evidence(error: Exception | None = None) -> dict:
    # 原因来自有限代码表，不能把第三方返回的任意字符串当成公开分类透传。
    reasons = {"login_required", "rate_limited", "challenge_required", "access_denied", "network_error",
               "http_error", "identity_unverified", "identity_probe_unsupported", "identity_probe_failed",
               "empty_result", "collection_failed", "collector_unavailable", "credential_missing", "credential_changed"}
    reason = error.reason if isinstance(error, CookieProbeError) and error.reason in reasons else "probe_failed"
    status = ("invalid" if _cookie_failure_status(error) == "invalid" else "unknown") if error is not None else "valid"
    return {"scope": "identity", "status": status,
            "reason": reason if error is not None else "authenticated", "operations": "not_checked"}


def _classify_ecommerce_probe(
    landed_url: str, status_code: int, cn: str, login_markers: tuple, best_effort: bool,
) -> str:
    """跳回登录页可证明当前探测未认证；页面成功加载不证明身份。"""
    url = urlsplit(landed_url)
    if any((url.path == marker if marker.startswith("/") else url.hostname == marker) for marker in login_markers):
        raise CookieProbeError(f"{cn} 登录状态已失效，请重新导出 Cookie", reason="login_required", status="invalid")
    if status_code != 200:
        hint = "，可能是反爬拦截而非 Cookie 失效" if best_effort else "，请稍后重试确认"
        reason = "rate_limited" if status_code == 429 else "access_denied" if status_code == 403 else "http_error"
        raise CookieProbeError(f"{cn} 无法判断 Cookie 状态（HTTP {status_code}）{hint}", reason=reason)
    raise CookieProbeError(f"{cn} 无法判断 Cookie 状态：页面可达，但未取得已登录身份凭据", reason="identity_unverified")


async def _verify_ecommerce_cookie(cookie_key: str) -> str:
    """真实探测：带上 Cookie 访问一个必须登录才能访问的页面，交给 _classify_ecommerce_probe 判定。"""
    from src.config.user_ctx import effective

    probe = _ECOMMERCE_PROBES[cookie_key]
    cookie = (effective(cookie_key) or "").strip()
    if not cookie:
        raise CookieProbeError(f"{probe['cn']} 未配置 Cookie", reason="credential_missing")
    headers = {"User-Agent": _ECOMMERCE_UA, "Cookie": cookie}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(probe["url"], headers=headers)
    except httpx.RequestError:
        # 不透传带请求地址或凭证的第三方异常。
        raise CookieProbeError(f"{probe['cn']} 无法判断 Cookie 状态：网络探测失败，请稍后重试", reason="network_error") from None
    return _classify_ecommerce_probe(
        str(r.url), r.status_code, probe["cn"], probe["login_markers"], probe["best_effort"],
    )


async def _verify_mc_cookie(cookie_key: str) -> str:
    """运行独立身份探针；缺少探针时不回落到搜索。

    这是真实浏览器自动化（登录+身份接口），不是轻量探活，耗时可能从十几秒到几分钟不等——
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
        raise CookieProbeError(f"{platform_cn} 未配置 Cookie，无法验证账号身份", reason="credential_missing")
    collector = get_registry().get("mediacrawler")
    if collector is None or not collector.is_available():
        raise CookieProbeError("MediaCrawler 未配置（MEDIACRAWLER_PATH），无法探测", reason="collector_unavailable")
    # 小红书与专属采集使用同一独立浏览器入口，不能借 CDP/持久会话证明所填 Cookie 有效。
    spec = TaskSpec(intent="Cookie 连通验证", platforms=[platform_cn], keywords=["你好"], max_items=1,
                    xhs_sort="general" if platform_code == "xhs" else None)
    probe = getattr(collector, "verify_cookie", None)
    if not callable(probe):
        raise CookieProbeError(f"{platform_cn} 暂不支持独立身份验证", reason="identity_probe_unsupported")
    result = await probe(spec)
    authentication = getattr(result, "authentication", {})
    if callable(probe) and authentication:
        if result.success and authentication.get("status") == "valid":
            return f"{platform_cn} 本次登录身份验证通过；尚未验证搜索、详情或评论权限"
        if authentication.get("status") == "invalid":
            raise CookieProbeError(f"{platform_cn} 身份接口明确返回未登录，请重新认证", reason="login_required", status="invalid")
        raise CookieProbeError(f"{platform_cn} 尚未取得可确认的登录身份，不能判断 Cookie 是否有效",
                               reason=authentication.get("reason") or "identity_unverified")
    if result.has_data:
        # 公开搜索可匿名成功，采集器尚未提供独立的账号身份证据，不能据此标记有效。
        raise CookieProbeError(
            f"{platform_cn} 搜索可用（采到 {len(result.items)} 条数据），账号身份未验证，无法判断 Cookie 是否有效",
            reason="identity_unverified",
        )
    msg = result.message or ""
    # 空结果既不能证明身份有效，也不能证明凭证过期；只有明确登录证据才判失效。
    if msg == "MediaCrawler 未产出可解析结果":
        raise CookieProbeError(f"{platform_cn} 无法判断 Cookie 状态：探测未产出数据", reason="empty_result")
    if "登录已过期" in msg:
        raise CookieProbeError(msg, reason="login_required", status="invalid")
    raise CookieProbeError(msg or "MediaCrawler 采集失败，原因未知", reason="collection_failed")


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


def _record_cookie_health(key: str, status: str, message: str, checked_by: str, *, expected_version: str | None = None, expected_binding: str | None = None) -> bool | None:
    """验证结果顺带落库，供配置中心展示"最后一次验证状态"；旁路副作用，落库失败只记日志。"""
    safe_message = rc.redact_sensitive_text(str(message))[:500]
    try:
        if expected_binding is not None:
            from src.config.cookie_probe_binding import cookie_probe_binding
            version, value = rc.global_secret_snapshot(get_store(), key)
            if cookie_probe_binding(key, version, value) != expected_binding:
                return False
        return get_store().cookie_health_set(key, status, safe_message, checked_by, expected_version=expected_version, expected_binding=expected_binding)
    except Exception:  # noqa: BLE001 落库失败不该影响验证结果本身返回给前端
        # 持久化异常可能携带连接参数或 Cookie；旁路日志只保留目标标识。
        logger.warning("cookie_health 写入失败 key=%s", key)


async def _verify_global_cookie(key: str, checked_by: str) -> str:
    version, value = rc.global_secret_snapshot(get_store(), key)
    from src.config.cookie_probe_binding import cookie_probe_binding, capture_probe_environment, probe_environment_context
    environment = capture_probe_environment()
    binding = cookie_probe_binding(key, version, value, environment=environment)
    # 明确冻结所选平台凭证，不继承调用协程中的个人覆盖，也不在探测中途换账号。
    with user_overrides_context({key: value}), probe_environment_context(environment):
        try:
            # 空覆盖会回落进程设置；验证必须停在冻结凭证，不能借旧值或扫码换账号。
            if not value:
                raise CookieProbeError("未配置 Cookie，无法验证账号身份", reason="credential_missing")
            detail = await _verify_target(key)
        except Exception as error:
            # 必须在冻结旧凭证仍可见时脱敏，退出上下文后全局值可能已经轮换。
            safe_detail = rc.redact_sensitive_text(str(error.detail if isinstance(error, HTTPException) else error))[:500]
            if _record_cookie_health(key, _cookie_failure_status(error), safe_detail, checked_by, expected_version=version, expected_binding=binding) is False:
                raise CookieProbeError("凭证已更新或验证环境已变化，本次旧探测结果已丢弃，请重新验证", reason="credential_changed") from None
            if isinstance(error, HTTPException):
                raise HTTPException(error.status_code, safe_detail, headers=error.headers) from None
            raise CookieProbeError(safe_detail, reason=error.reason if isinstance(error, CookieProbeError) else "probe_failed", status=_cookie_failure_status(error)) from None
        if _record_cookie_health(key, "valid", detail, checked_by, expected_version=version, expected_binding=binding) is False:
            raise CookieProbeError("凭证已更新或验证环境已变化，本次旧探测结果已丢弃，请重新验证", reason="credential_changed")
        return detail


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
        from src.conductor.slack_sender import verify_connection
        await verify_connection()
        return "Slack 身份验证成功（未发送消息；未验证频道与文件权限）"
    if target == "semantic":
        from src.memory.embeddings import embed_texts_with_model, is_rerank_configured, rerank_scores
        got = await asyncio.to_thread(embed_texts_with_model, ["连通验证"])
        if not (got and got[1]):
            raise RuntimeError("embedding 端点不可用")
        msg = f"embedding 可用（{got[0]}）"
        if is_rerank_configured():
            scores = await asyncio.to_thread(rerank_scores, "连通验证", ["连通验证"])
            if not scores:
                raise RuntimeError("embedding 可用；已配置的 rerank 不可用，组合检查未通过")
            msg += "；rerank 可用"
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
            return f"检测到本机浏览器：{found}；当前平台采集强制使用独立会话，不连接共享 CDP"
        return "未检测到本机浏览器；当前平台采集使用独立 Chromium，不连接共享 CDP"
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
        detail = (await _verify_global_cookie(body.target, "manual")
                  if is_admin and body.target in _COOKIE_HEALTH_KEYS else await _verify_target(body.target))
        return {"ok": True, "detail": detail,
                **({"verification": _cookie_verification_evidence()} if body.target in _COOKIE_HEALTH_KEYS else {})}
    except HTTPException as exc:
        detail = rc.redact_sensitive_text(str(exc.detail))[:300]
        raise HTTPException(
            status_code=exc.status_code,
            detail=detail,
            headers=exc.headers,
        ) from exc
    except Exception as e:  # noqa: BLE001 验证失败把原因回前端
        detail = rc.redact_sensitive_text(str(e))[:300]
        return {"ok": False, "detail": detail,
                **({"verification": _cookie_verification_evidence(e)} if body.target in _COOKIE_HEALTH_KEYS else {})}


class CookieReauthenticationIn(BaseModel):
    key: str
    value: str
    scope: Literal["personal", "platform"] = "personal"


@router.post("/reauthenticate")
async def reauthenticate_cookie(body: CookieReauthenticationIn, user=Depends(get_current_user), request: Request = None):
    """用户正常登录后验证新凭证；仅成功时采用，不自动恢复或重放任务。"""
    if body.scope == "platform" and not is_admin_role(user.get("role")):
        raise HTTPException(403, "无权更新平台凭证")
    if body.key not in _COOKIE_HEALTH_KEYS or body.key not in rc.USER_KEYS:
        raise HTTPException(400, "不支持的采集账号")
    value = body.value.strip()
    if not value:
        raise HTTPException(400, "新凭证不能为空")
    from src.config.cookie_probe_binding import capture_probe_environment, probe_environment_context
    from src.config.user_ctx import frozen_effective_values

    store = get_store()
    scope = "global" if body.scope == "platform" else user["user_id"]
    snapshot = store.config_secret_snapshot(scope, body.key)
    version = snapshot[0] if snapshot is not None else None
    environment = capture_probe_environment()
    with user_overrides_context({body.key: value}), frozen_effective_values([body.key]), probe_environment_context(environment):
        try:
            await _verify_target(body.key)
        except Exception as error:
            # 新值尚未进入Vault；固定提示避免第三方异常携带凭证正文。
            return {"ok": False, "saved": False, "detail": "新凭证未通过身份验证，原配置未修改",
                    "verification": _cookie_verification_evidence(error)}
    if capture_probe_environment() != environment:
        raise HTTPException(409, "验证环境已变化，原配置未修改，请重新验证")
    if body.scope == "platform":
        with _audit_config_changes(request, [body.key]):
            saved = rc.replace_global_secret(store, body.key, value, expected_version=version, updated_by=user["user_id"])
    else:
        saved = store.config_replace_secret(scope, body.key, value, expected_version=version, updated_by=user["user_id"])
    if not saved:
        raise HTTPException(409, "验证期间凭证已被更新，未覆盖新配置，请重新加载后核对")
    # 身份有效不证明新旧凭证为同一账号；保留旧任务绑定和未知调用不重放边界。
    return {"ok": True, "saved": True, "detail": "新凭证已验证并保存；原任务未自动恢复",
            "verification": _cookie_verification_evidence(), "task_resume": "requires_explicit_recovery"}
