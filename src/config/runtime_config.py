"""
运行时配置中心 —— 凭证/端点类配置的前端可改、热生效、.env 兜底。

三层取值链（高→低）：用户级覆盖（仅白名单中 user=True 的键，作用于该用户自己的任务）
→ 全局覆盖（管理员在前端改，落 webui.db 的 runtime_config 表并即时 setattr 到 settings）
→ .env / 代码默认值（进程启动时的基线，重置即回到这里）。

设计要点：
- REGISTRY 白名单：只有登记过的 settings 字段可被前端改，防任意字段注入；
- 全局覆盖改的是 settings 单例属性，绝大多数模块按调用时读取、天然热生效；
  LLM 供应商 profile 在进程内有缓存，改 llm 组键后调 provider.reload_provider() 失效重建；
- 基线快照 _BASELINE 在首次 apply 前采集（即 .env 加载后的原始值），重置=恢复基线；
- 类型转换按 settings 字段注解（bool/int/float/str），转失败即拒绝，不写坏配置。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.config.settings import settings

logger = logging.getLogger(__name__)

# 分组展示顺序（前端按此渲染）
GROUPS: List[Dict[str, str]] = [
    {"key": "llm_default", "label": "模型 · 默认选择"},
    {"key": "document_extraction", "label": "模型 · 文档抽取"},
    {"key": "llm_deepseek", "label": "模型 · DeepSeek"},
    {"key": "llm_qwen", "label": "模型 · 阿里百炼 Qwen"},
    {"key": "llm_local", "label": "模型 · 本地端点"},
    {"key": "search", "label": "搜索与采集服务"},
    {"key": "cookies", "label": "平台 Cookie"},
    {"key": "cookie_health", "label": "Cookie 健康巡检"},
    {"key": "email", "label": "邮件 SMTP"},
    {"key": "slack", "label": "Slack"},
    {"key": "semantic", "label": "语义召回（embedding/rerank）"},
    {"key": "proxy", "label": "代理池"},
    {"key": "mc_cdp", "label": "MediaCrawler 反检测（CDP 模式）"},
    {"key": "mysql", "label": "MySQL 入库"},
    {"key": "checkpoint", "label": "断点续跑"},
    {"key": "library_dedup", "label": "知识库巡检"},
    {"key": "data_prep", "label": "数据准备模式"},
]

# 白名单：key -> {label, group, secret(掩码显示), user(普通用户可自助按用户覆盖)}
REGISTRY: Dict[str, Dict[str, Any]] = {
    # 默认模型选择
    "llm_default_provider": {
        "label": "默认供应商", "group": "llm_default", "secret": False, "user": False,
        "type": "select", "choices": ["deepseek", "qwen", "local"],
    },
    "document_extraction_model": {
        "label": "文档抽取默认模型",
        "group": "document_extraction",
        "secret": False,
        "user": True,
        "type": "select",
        "choices_from": "document",
    },
    # 模型供应商
    "deepseek_api_key": {"label": "DeepSeek API Key", "group": "llm_deepseek", "secret": True, "user": True},
    "deepseek_base_url": {"label": "DeepSeek 地址", "group": "llm_deepseek", "secret": False, "user": False},
    "deepseek_model": {
        "label": "DeepSeek 默认模型", "group": "llm_deepseek", "secret": False, "user": False,
        "type": "select", "choices_from": "deepseek",
    },
    "qwen_api_key": {"label": "百炼 API Key", "group": "llm_qwen", "secret": True, "user": True},
    "qwen_base_url": {"label": "百炼地址", "group": "llm_qwen", "secret": False, "user": False},
    "qwen_model": {
        "label": "百炼默认模型", "group": "llm_qwen", "secret": False, "user": False,
        "type": "select", "choices_from": "qwen",
    },
    "llm_base_url": {"label": "本地模型地址", "group": "llm_local", "secret": False, "user": False},
    "llm_model_name": {
        "label": "本地默认模型名", "group": "llm_local", "secret": False, "user": False,
        "type": "select", "choices_from": "local",
    },
    "local_extra_models": {"label": "额外本地模型（名称@URL,逗号分隔）", "group": "llm_local", "secret": False, "user": False},
    "local_enable_thinking": {
        # choices 用 "True"/"False"（首字母大写）以匹配 Python str(bool) 的展示格式，
        # 否则编辑弹窗回填当前值时会因大小写不匹配而选不中；cast_value 保存时不区分大小写。
        "label": "本地模型思考模式（Qwen3.6 等思考模型）", "group": "llm_local", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    # 搜索与采集服务
    "tavily_api_key": {"label": "Tavily API Key", "group": "search", "secret": True, "user": False},
    "searxng_base_url": {"label": "SearXNG 地址", "group": "search", "secret": False, "user": False},
    "anysearch_api_key": {"label": "AnySearch API Key", "group": "search", "secret": True, "user": False},
    "firecrawl_base_url": {"label": "Firecrawl 地址", "group": "search", "secret": False, "user": False},
    "firecrawl_api_key": {"label": "Firecrawl API Key", "group": "search", "secret": True, "user": False},
    "rsshub_base_url": {"label": "RSSHub 地址", "group": "search", "secret": False, "user": False},
    # 平台 Cookie（登录态凭证，普通用户可用自己账号的）
    "mc_cookie_xhs": {"label": "小红书 Cookie", "group": "cookies", "secret": True, "user": True},
    "mc_cookie_dy": {"label": "抖音 Cookie", "group": "cookies", "secret": True, "user": True},
    "mc_cookie_wb": {"label": "微博 Cookie", "group": "cookies", "secret": True, "user": True},
    "mc_cookie_bili": {"label": "B站 Cookie", "group": "cookies", "secret": True, "user": True},
    "mc_cookie_zhihu": {"label": "知乎 Cookie", "group": "cookies", "secret": True, "user": True},
    "mc_cookie_ks": {"label": "快手 Cookie", "group": "cookies", "secret": True, "user": True},
    "mc_cookie_tieba": {"label": "贴吧 Cookie", "group": "cookies", "secret": True, "user": True},
    "jd_cookie": {"label": "京东 Cookie", "group": "cookies", "secret": True, "user": True},
    "tb_cookie": {"label": "淘宝/天猫 Cookie", "group": "cookies", "secret": True, "user": True},
    "pdd_cookie": {"label": "拼多多 Cookie", "group": "cookies", "secret": True, "user": True},
    # 邮件
    "smtp_enabled": {
        "label": "启用邮件发送", "group": "email", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    "smtp_host": {"label": "SMTP 服务器", "group": "email", "secret": False, "user": False},
    "smtp_port": {"label": "SMTP 端口", "group": "email", "secret": False, "user": False},
    "smtp_user": {"label": "SMTP 账号", "group": "email", "secret": False, "user": False},
    "smtp_password": {"label": "SMTP 密码/授权码", "group": "email", "secret": True, "user": False},
    "smtp_from": {"label": "发件人（空=账号）", "group": "email", "secret": False, "user": False},
    "smtp_use_ssl": {
        # choices 用 "True"/"False"（首字母大写），原因同 local_enable_thinking：匹配 Python str(bool) 展示格式
        "label": "SSL（465端口=true）", "group": "email", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    # Slack
    "slack_enabled": {
        "label": "启用 Slack 通知", "group": "slack", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    "slack_webhook_url": {"label": "Slack Webhook URL", "group": "slack", "secret": True, "user": False},
    # 语义召回
    "embedding_enabled": {
        "label": "启用语义召回", "group": "semantic", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    "embedding_base_url": {"label": "embedding 端点", "group": "semantic", "secret": False, "user": False},
    "embedding_api_key": {"label": "embedding API Key", "group": "semantic", "secret": True, "user": False},
    "embedding_model": {"label": "embedding 模型名", "group": "semantic", "secret": False, "user": False},
    "rerank_base_url": {"label": "rerank 端点", "group": "semantic", "secret": False, "user": False},
    "rerank_api_key": {"label": "rerank API Key", "group": "semantic", "secret": True, "user": False},
    "rerank_model": {"label": "rerank 模型名", "group": "semantic", "secret": False, "user": False},
    # 代理池
    "mc_enable_ip_proxy": {
        "label": "启用代理池", "group": "proxy", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    "mc_ip_proxy_provider": {
        "label": "代理商（kuaidaili/wandouhttp/static）", "group": "proxy", "secret": False, "user": False,
        "type": "select", "choices": ["kuaidaili", "wandouhttp", "static"],
    },
    "mc_static_proxy_url": {"label": "静态代理 URL", "group": "proxy", "secret": True, "user": False},
    "mc_kdl_secret_id": {"label": "快代理 secret_id", "group": "proxy", "secret": True, "user": False},
    "mc_kdl_signature": {"label": "快代理 signature", "group": "proxy", "secret": True, "user": False},
    "mc_kdl_user_name": {"label": "快代理用户名", "group": "proxy", "secret": False, "user": False},
    "mc_kdl_user_pwd": {"label": "快代理密码", "group": "proxy", "secret": True, "user": False},
    "mc_wandou_app_key": {"label": "豌豆HTTP app_key", "group": "proxy", "secret": True, "user": False},
    # MediaCrawler 反检测（CDP 模式）
    "mc_enable_cdp_mode": {
        "label": "启用 CDP 模式（连接本机真实浏览器）", "group": "mc_cdp", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    # MySQL
    "db_backend": {
        "label": "入库后端（sqlite/mysql）", "group": "mysql", "secret": False, "user": False,
        "type": "select", "choices": ["sqlite", "mysql"],
    },
    "mysql_host": {"label": "MySQL 主机", "group": "mysql", "secret": False, "user": False},
    "mysql_port": {"label": "MySQL 端口", "group": "mysql", "secret": False, "user": False},
    "mysql_user": {"label": "MySQL 用户", "group": "mysql", "secret": False, "user": False},
    "mysql_password": {"label": "MySQL 密码", "group": "mysql", "secret": True, "user": False},
    "mysql_database": {"label": "MySQL 库名", "group": "mysql", "secret": False, "user": False},
    # 断点续跑
    "checkpoint_enabled": {
        "label": "启用断点续跑", "group": "checkpoint", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    # 模板库/教训库定时巡检
    "library_dedup_scan_enabled": {
        "label": "启用知识库巡检", "group": "library_dedup", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    "library_dedup_scan_interval_hours": {
        "label": "巡检间隔（小时）", "group": "library_dedup", "secret": False, "user": False,
    },
    "library_stale_draft_days": {
        "label": "停滞草稿清理阈值（天）", "group": "library_dedup", "secret": False, "user": False,
    },
    "library_dedup_scan_max_merges_per_run": {
        "label": "每轮最大合并对数", "group": "library_dedup", "secret": False, "user": False,
    },
    # Cookie 健康定时巡检
    "cookie_health_scan_enabled": {
        "label": "启用定时巡检", "group": "cookie_health", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    "cookie_health_scan_interval_hours": {
        "label": "巡检间隔（小时）", "group": "cookie_health", "secret": False, "user": False,
    },
    # 数据准备模式（v2）
    "data_prep_mode_enabled": {
        "label": "启用数据准备模式（新任务默认）", "group": "data_prep", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    "data_prep_raw_retention_days": {
        "label": "原始制品保留天数", "group": "data_prep", "secret": False, "user": False,
    },
}

USER_KEYS = {k for k, m in REGISTRY.items() if m["user"]}  # 普通用户可按用户覆盖的键

# .env/默认值基线（首次 apply 前采集；重置回落到这里）
_BASELINE: Dict[str, Any] = {}


def _snapshot_baseline() -> None:
    if not _BASELINE:
        for k in REGISTRY:
            _BASELINE[k] = getattr(settings, k, None)


def cast_value(key: str, raw: str) -> Any:
    """按 settings 字段注解把字符串转成目标类型；转不了抛 ValueError。"""
    base = _BASELINE.get(key, getattr(settings, key, ""))
    if isinstance(base, bool):
        low = (raw or "").strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"{key} 须为 true/false")
    if isinstance(base, int) and not isinstance(base, bool):
        return int(raw)
    if isinstance(base, float):
        return float(raw)
    return raw


def _after_set(key: str) -> None:
    """键生效后的联动：LLM 供应商组需重建 profile 缓存。"""
    if key.startswith(("deepseek_", "qwen_", "llm_", "local_")):
        try:
            from src.llm.provider import reload_provider
            reload_provider()
        except Exception as e:  # noqa: BLE001 联动失败不阻断配置保存
            logger.warning(
                "LLM 供应商缓存重建失败: %s",
                redact_sensitive_text(str(e)),
            )


def apply_global_overrides(store) -> int:
    """启动时把全局覆盖套到 settings 上（.env 已先加载为基线）。返回生效条数。"""
    _snapshot_baseline()
    n = 0
    for key, raw in (store.config_all("global") or {}).items():
        if key not in REGISTRY:
            continue
        try:
            setattr(settings, key, cast_value(key, raw))
            n += 1
        except Exception as e:  # noqa: BLE001 单条坏值跳过，不影响其余配置与启动
            logger.warning("运行时配置 %s 值非法，已忽略（回落 .env）：%s", key, e)
    if n:
        _after_set("llm_")  # 任一覆盖生效后统一重建一次 LLM 缓存
        logger.info("已应用 %d 条全局运行时配置覆盖", n)
    return n


def set_global(store, key: str, raw: str, updated_by: str = "") -> None:
    """管理员设置全局覆盖：校验→落库→热生效。"""
    _snapshot_baseline()
    if key not in REGISTRY:
        raise KeyError(f"不支持配置该项: {key}")
    value = cast_value(key, raw)  # 先校验类型，坏值不落库
    store.config_set("global", key, raw, updated_by)
    setattr(settings, key, value)
    _after_set(key)


def reset_global(store, key: str) -> None:
    """删除全局覆盖，恢复 .env/默认基线。"""
    _snapshot_baseline()
    if key not in REGISTRY:
        raise KeyError(f"不支持配置该项: {key}")
    store.config_delete("global", key)
    if key in _BASELINE:
        setattr(settings, key, _BASELINE[key])
    _after_set(key)


def mask_value(key: str, raw: Optional[str]) -> str:
    """密钥类只回尾 4 位，其余原样；空值回空串。"""
    if raw is None or raw == "":
        return ""
    if REGISTRY.get(key, {}).get("secret"):
        s = str(raw)
        return ("····" + s[-4:]) if len(s) > 4 else "····"
    return str(raw)


def redact_sensitive_text(text: str) -> str:
    """从异常、诊断与日志文本移除当前进程可见的运行时 Secret。"""
    values: set[str] = set()
    for key, metadata in REGISTRY.items():
        if not metadata.get("secret"):
            continue
        configured = str(getattr(settings, key, "") or "")
        if configured:
            values.add(configured)
        try:
            from src.config.user_ctx import get_user_override

            personal = str(get_user_override(key) or "")
        except Exception:  # noqa: BLE001 脱敏不能因上下文不可用而扩大异常面
            personal = ""
        if personal:
            values.add(personal)
    redacted = str(text)
    # 先替换长值，避免某个短 Secret 是长 Secret 的子串而留下尾部。
    for value in sorted(values, key=len, reverse=True):
        redacted = redacted.replace(value, "[SECRET]")
    return redacted


def describe(store) -> List[Dict[str, Any]]:
    """给管理员前端的全量描述：分组 → 各项 {key,label,secret,user,source,value,type,choices,choicesFrom,health}。"""
    _snapshot_baseline()
    overrides = store.config_all("global") or {}
    cookie_health = store.cookie_health_all()
    groups = []
    for g in GROUPS:
        items = []
        for key, meta in REGISTRY.items():
            if meta["group"] != g["key"]:
                continue
            overridden = key in overrides
            cur = overrides.get(key) if overridden else getattr(settings, key, "")
            entry = {
                "key": key, "label": meta["label"], "secret": meta["secret"],
                "user_editable": meta["user"],
                "source": "override" if overridden else "env",
                "value": mask_value(key, "" if cur is None else str(cur)),
            }
            # 下拉选择型配置：透传 type/choices/choicesFrom 给前端渲染下拉
            if meta.get("type") == "select":
                entry["type"] = "select"
                if "choices" in meta:
                    entry["choices"] = meta["choices"]
                if "choices_from" in meta:
                    entry["choicesFrom"] = meta["choices_from"]
            # Cookie 分组额外带上"最后一次验证状态"；没验证过的 key 为 None（前端展示"从未验证"）
            if g["key"] == "cookies":
                entry["health"] = cookie_health.get(key)
            items.append(entry)
        groups.append({**g, "items": items})
    return groups
