"""
MediaCrawler 社媒采集器（Tier 0，社媒专用，优先级最高）。

MediaCrawler 支持 抖音/小红书/微博/B站/快手 等的帖子与评论采集，解决签名风控，
社媒场景明显优于通用引擎（对应"小米 SU7 槽点"这类需求）。

⚠️ 许可证为非商业学习用途，商业化前需替换或采购授权（MediaCrawlerPro / 商业数据源）。

工作方式：以子进程调用 MediaCrawler 的 main.py 做关键词搜索，结果存为 JSON 后读回。
仅当配置了 MEDIACRAWLER_PATH 且该目录存在时可用，否则路由自动跳过。
MediaCrawler 多数平台需先按其文档完成登录/cookie 配置，本采集器只负责调用与读取。

安装见 external/MediaCrawler/README（克隆 https://github.com/NanmiCoder/MediaCrawler）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from src.config.settings import settings
from src.conductor.task_spec import AnalysisType, DataType, TaskSpec

from src.conductor.targets import content_id_from_url
from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register

logger = logging.getLogger(__name__)

# 平台名（中文/英文别名）-> MediaCrawler 平台代码
_PLATFORM_MAP = {
    "抖音": "dy", "douyin": "dy", "dy": "dy",
    "小红书": "xhs", "红书": "xhs", "xiaohongshu": "xhs", "xhs": "xhs",
    "微博": "wb", "weibo": "wb", "wb": "wb",
    "b站": "bili", "B站": "bili", "哔哩哔哩": "bili", "bilibili": "bili", "bili": "bili",
    "快手": "ks", "kuaishou": "ks", "ks": "ks",
    "知乎": "zhihu", "zhihu": "zhihu",
    "贴吧": "tieba", "tieba": "tieba",
}


# MediaCrawler 平台代码 -> settings 中对应的 Cookie 字段名
_COOKIE_ATTR = {
    "dy": "mc_cookie_dy",
    "xhs": "mc_cookie_xhs",
    "wb": "mc_cookie_wb",
    "bili": "mc_cookie_bili",
    "zhihu": "mc_cookie_zhihu",
    "ks": "mc_cookie_ks",
    "tieba": "mc_cookie_tieba",
}


def _platform_cookie(platform: str) -> str:
    """取该平台 Cookie：当前任务用户的自配 Cookie 优先 → 全局配置/.env（无则空串，调用方回退扫码）。"""
    attr = _COOKIE_ATTR.get(platform)
    if not attr:
        return ""
    from src.config.user_ctx import effective
    return (effective(attr) or "").strip()


# MediaCrawler 平台代码 -> 中文展示名（用于把失败原因翻译成人话）
_PLATFORM_CN = {"dy": "抖音", "xhs": "小红书", "wb": "微博", "bili": "B站",
                "ks": "快手", "zhihu": "知乎", "tieba": "贴吧"}


def _diagnose_mc_failure(platform: str, output: str) -> str:
    """把 MediaCrawler 子进程原始报错翻译成用户可操作的一句话，避免把整段 traceback 抛给用户。"""
    cn = _PLATFORM_CN.get(platform, platform)
    env_name = (_COOKIE_ATTR.get(platform) or "").upper()  # 如 mc_cookie_xhs -> MC_COOKIE_XHS
    text = output or ""
    if "登录已过期" in text or "Login state result: False" in text or "登录失败" in text:
        tip = f"{cn}登录已过期"
        if env_name:
            tip += f"，请更新 .env 的 {env_name}（重新登录{cn}后导出最新 Cookie）"
        return tip
    if "CAPTCHA" in text or "Verifytype" in text or "验证码" in text:
        return f"{cn}触发验证码风控，建议开启代理IP池换 IP 或稍后重试"
    if "IPBlock" in text or "IP_ERROR" in text or "访问频次" in text:
        return f"{cn}访问频次过高被限制，建议开启代理IP池或降低采集频率"
    # 兜底：只取最后一行非空信息，不吐整段 traceback
    last = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
    return f"{cn}采集失败：{last[:120]}" if last else f"{cn}采集失败"


def _proxy_env() -> dict:
    """生成注入 MediaCrawler 子进程的代理IP池环境变量。

    未启用代理时返回空 dict（保持直连）。启用时：
    - 4 个开关经 MC_* 注入，由 MediaCrawler 的 base_config 读取（见 config/base_config.py）；
    - provider 凭证按 MediaCrawler 约定的环境变量名注入（KDL_*/WANDOU_APP_KEY），仅在配置了才注入。
    """
    if not settings.mc_enable_ip_proxy:
        return {}
    env = {
        "MC_ENABLE_IP_PROXY": "true",
        "MC_IP_PROXY_PROVIDER": settings.mc_ip_proxy_provider,
        "MC_IP_PROXY_POOL_COUNT": str(settings.mc_ip_proxy_pool_count),
    }
    if settings.mc_static_proxy_url:
        env["MC_STATIC_PROXY_URL"] = settings.mc_static_proxy_url
    # 快代理凭证（注意 MediaCrawler 侧环境变量名为 KDL_SECERT_ID，sic 原拼写）
    if settings.mc_kdl_secret_id:
        env["KDL_SECERT_ID"] = settings.mc_kdl_secret_id
        env["KDL_SIGNATURE"] = settings.mc_kdl_signature
        env["KDL_USER_NAME"] = settings.mc_kdl_user_name
        env["KDL_USER_PWD"] = settings.mc_kdl_user_pwd
    # 豌豆HTTP 凭证
    if settings.mc_wandou_app_key:
        env["WANDOU_APP_KEY"] = settings.mc_wandou_app_key
    return env


def _cdp_env() -> dict:
    """CDP 模式：让 MediaCrawler 连接本机真实安装的 Chrome/Edge 采集，而非 Playwright 自带的
    裸 Chromium。真实浏览器指纹能显著降低抖音/B站这类设备指纹风控较重的平台误判登录态失效的概率。
    """
    if not settings.mc_enable_cdp_mode:
        return {}
    return {"MC_ENABLE_CDP_MODE": "true"}


def _build_cmd(python_exe: str, platform: str, keywords: str, want_comments: bool, cookie: str,
               max_notes: int) -> list:
    """拼 MediaCrawler 命令：有 cookie 则走 --lt cookie 登录，否则用 base_config 的扫码+会话。"""
    cmd = [
        python_exe, "main.py",
        "--platform", platform,
        "--type", "search",
        "--keywords", keywords,
        "--save_data_option", "json",
    ]
    # 必须显式传帖子数：不传则沿用 base_config 写死的 CRAWLER_MAX_NOTES_COUNT=10，
    # 导致用户要更多也只爬 10 条（数据偏少的主因），故按 max_items 显式下发。
    cmd += ["--crawler_max_notes_count", str(max_notes)]
    # 必须显式传 yes/no：不传则沿用 MediaCrawler 默认 ENABLE_GET_COMMENTS=True，
    # 会对每条笔记逐条爬评论（叠加随机间隔后极易超时），与“只取帖子”的诉求不符。
    cmd += ["--get_comment", "yes" if want_comments else "no"]
    if cookie:
        cmd += ["--lt", "cookie", "--cookies", cookie]
    return cmd

def _build_detail_cmd(python_exe: str, platform: str, target_urls: list[str], want_comments: bool,
                      cookie: str, max_notes: int) -> list:
    """显式链接必须使用 MediaCrawler 的详情采集模式。"""
    cmd = [python_exe, "main.py", "--platform", platform, "--type", "detail",
           "--specified_id", ",".join(target_urls), "--save_data_option", "json",
           "--crawler_max_notes_count", str(max_notes),
           "--get_comment", "yes" if want_comments else "no"]
    if cookie:
        cmd += ["--lt", "cookie", "--cookies", cookie]
    return cmd


def _resolve_platform(spec: TaskSpec) -> str:
    """从 TaskSpec 的平台名解析出 MediaCrawler 平台代码，无法识别返回空串。

    先用归一（别名/大小写/包含匹配）得到规范名，再映射到 MediaCrawler 代码，
    避免「小红书App」「Douyin」等变体漏匹配专用采集器。
    """
    from .platforms import normalize_platform
    for p in spec.platforms:
        canon = normalize_platform(p)
        code = (
            _PLATFORM_MAP.get(p.strip())
            or _PLATFORM_MAP.get(p.strip().lower())
            or _PLATFORM_MAP.get(canon)
        )
        if code:
            return code
    return ""


def _flatten_records(obj, out: list) -> None:
    """递归地从 MediaCrawler 的 JSON 结构里收集 dict 记录。"""
    if isinstance(obj, list):
        for x in obj:
            _flatten_records(x, out)
    elif isinstance(obj, dict):
        # 结果项通常含正文/标题/内容字段
        if any(k in obj for k in ("content", "desc", "title", "note_id", "aweme_id", "comment_id")):
            out.append(obj)
        else:
            for v in obj.values():
                _flatten_records(v, out)


def _collect_douyin_with_ytdlp(urls: list[str], cookie: str) -> list[CollectedItem]:
    '''MediaCrawler 详情接口受限时，用同一登录态解析指定抖音视频。'''
    if not cookie:
        return []
    try:
        import yt_dlp
    except ImportError:
        return []

    items: list[CollectedItem] = []
    options = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'proxy': '',
        'http_headers': {'Cookie': cookie},
    }
    for requested_url in urls:
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(requested_url, download=False)
        except Exception:
            logger.warning('yt-dlp 抖音详情兜底失败：%s', requested_url, exc_info=True)
            continue
        content_id = str(info.get('id') or '')
        canonical_url = str(info.get('webpage_url') or '')
        media_url = str(info.get('url') or '')
        if not content_id or not canonical_url or not media_url:
            continue
        items.append(CollectedItem(
            url=canonical_url,
            title=str(info.get('title') or ''),
            content=str(info.get('description') or info.get('title') or ''),
            metadata={
                'engine': 'mediacrawler+ytdlp',
                'platform': 'dy',
                'kind': 'post',
                'collection_mode': 'direct',
                'requested_url': requested_url,
                'canonical_url': canonical_url,
                'content_id': content_id,
                'identity_verified': True,
                'media_url': media_url,
                'raw_record': {'aweme_id': content_id},
            },
        ))
    return items


class SocialMediaCollector(BaseCollector):
    name = "mediacrawler"
    tier = 0  # 社媒专用，命中即最优先

    def is_available(self) -> bool:
        path = settings.mediacrawler_path
        return bool(path) and Path(path).expanduser().is_dir()

    def matches(self, spec: TaskSpec) -> bool:
        return bool(_resolve_platform(spec))

    async def collect(self, spec: TaskSpec) -> CollectResult:
        if not self.is_available():
            return CollectResult(False, self.name, message="MediaCrawler 未配置（MEDIACRAWLER_PATH）")
        platform = _resolve_platform(spec)
        direct_urls = list(spec.urls or [])
        if not platform:
            return CollectResult(False, self.name, message="未识别到 MediaCrawler 支持的平台")
        if not direct_urls and not spec.keywords:
            return CollectResult(False, self.name, message="社媒采集需要关键词")

        mc_dir = Path(settings.mediacrawler_path).expanduser()
        python_exe = settings.mediacrawler_python or sys.executable
        keywords = ",".join(spec.keywords)
        # VOC/评论类任务：槽点在评论里，自动请求抓取评论
        want_comments = (
            spec.analysis_type == AnalysisType.VOC or spec.data_type == DataType.COMMENT
        )

        # 以子进程运行 MediaCrawler 搜索，结果存 JSON
        cookie = _platform_cookie(platform)
        # 帖子数：尊重 max_items，但封顶到 collector_max_items，防止设过大拖垮浏览器采集
        max_notes = max(1, min(spec.max_items, settings.collector_max_items))

        async def direct_fallback() -> CollectResult | None:
            if platform != 'dy' or not direct_urls:
                return None
            fallback_items = await asyncio.to_thread(
                _collect_douyin_with_ytdlp, direct_urls[:max_notes], cookie
            )
            if not fallback_items:
                return None
            return CollectResult(
                True,
                self.name,
                items=fallback_items,
                message=f'MediaCrawler 详情受限，已用 yt-dlp 登录态兜底采集 {len(fallback_items)} 条',
            )

        cmd = (_build_detail_cmd(python_exe, platform, direct_urls, want_comments, cookie, max_notes) if direct_urls else _build_cmd(python_exe, platform, keywords, want_comments, cookie, max_notes))
        # 日志不打印 cookie 明文，避免泄露登录态
        safe_cmd = [("***" if i and cmd[i - 1] == "--cookies" else a) for i, a in enumerate(cmd)]
        logger.info("调用 MediaCrawler: %s (cwd=%s, 登录=%s)",
                    " ".join(safe_cmd), mc_dir, "cookie" if cookie else "扫码/会话")
        run_start = time.time()
        # 把“动态采集间隔区间”以环境变量注入子进程，供 MediaCrawler 的 config 读取，
        # 实现每次 sleep 在 [min, max] 间随机、模拟真人节奏规避频次风控。
        env = os.environ.copy()
        # Windows 默认代码页可能是 cp1252，execjs 向 Node.js 写入含中文的签名脚本时会崩溃。
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["MC_CRAWL_MIN_SLEEP_SEC"] = str(settings.mc_crawl_min_sleep_sec)
        env["MC_CRAWL_MAX_SLEEP_SEC"] = str(settings.mc_crawl_max_sleep_sec)
        # 代理IP池（启用时）：反国内社媒风控，优于服务端挂境外 VPN
        proxy_env = _proxy_env()
        env.update(proxy_env)
        if proxy_env:
            logger.info("MediaCrawler 启用代理IP池：provider=%s", settings.mc_ip_proxy_provider)
        # CDP 模式（反检测）：连接本机真实浏览器，降低抖音/B站等平台的风控识别率
        cdp_env = _cdp_env()
        env.update(cdp_env)
        if cdp_env:
            logger.info("MediaCrawler 启用 CDP 模式（连接本机真实浏览器）")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(mc_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            # 超时读 settings（与 collect 节点外层一致），避免内层写死后改 .env 不生效
            mc_timeout = settings.collect_timeout_mediacrawler_seconds
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=mc_timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return CollectResult(False, self.name, message=f"MediaCrawler 运行超时（{mc_timeout:.0f}s）")
        except Exception as e:
            # 部分异常（如某些 OSError）str() 为空，只报错误类型看不出原因；
            # 完整堆栈记日志备查，用户看到的消息至少带上异常类型名。
            logger.exception("MediaCrawler 子进程启动失败")
            return CollectResult(False, self.name, message=f"MediaCrawler 启动失败: {type(e).__name__}: {e}")

        if proc.returncode != 0:
            text = (stdout or b"").decode("utf-8", "ignore")
            # 翻译成用户可操作的一句话（登录过期/风控/频次…），并记录原始尾部到日志备查
            logger.warning("MediaCrawler 退出码 %s，原始尾部：%s", proc.returncode, text[-500:])
            fallback = await direct_fallback()
            if fallback:
                return fallback
            return CollectResult(False, self.name, message=_diagnose_mc_failure(platform, text))

        # 只读本次运行后新生成的 JSON（按 mtime 过滤，避免读到历史旧数据）
        data_dir = mc_dir / "data"
        all_json = list(data_dir.rglob("*.json")) if data_dir.exists() else []
        fresh = [p for p in all_json if p.stat().st_mtime >= run_start - 5]
        fresh.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        records: list = []
        for jf in fresh[:20]:
            try:
                _flatten_records(json.loads(jf.read_text(encoding="utf-8")), records)
            except Exception:
                continue
        expected_ids = {content_id_from_url(u) for u in direct_urls if content_id_from_url(u)}
        direct_limit = min(len(direct_urls), spec.max_items) if direct_urls else spec.max_items

        if not records:
            fallback = await direct_fallback()
            if fallback:
                return fallback
            return CollectResult(False, self.name, message="MediaCrawler 未产出可解析结果")

        # 区分评论与帖子；VOC 优先用评论（无评论则回退帖子）
        comments = [r for r in records if r.get("comment_id")]
        contents = [r for r in records if not r.get("comment_id")]
        use_comments = want_comments and bool(comments)
        chosen = comments if use_comments else (contents or records)

        items: list[CollectedItem] = []
        for rec in chosen[: direct_limit]:
            canonical_url = rec.get("note_url") or rec.get("aweme_url") or rec.get("video_url") or rec.get("url") or ""
            content_id = str(rec.get("aweme_id") or rec.get("note_id") or rec.get("video_id") or "")
            if direct_urls and (not canonical_url or not content_id or (expected_ids and content_id not in expected_ids)):
                continue
            requested_url = direct_urls[0] if direct_urls else ""
            media_url = rec.get("video_download_url") or rec.get("video_url") or ""
            content = rec.get("content") or rec.get("desc") or rec.get("title") or ""
            items.append(
                CollectedItem(
                    url=canonical_url,
                    title=rec.get("title") or rec.get("nickname") or "",
                    content=str(content),
                    metadata={
                        "engine": self.name,
                        "platform": platform,
                        "kind": "comment" if rec.get("comment_id") else "post",
                        "collection_mode": "direct" if direct_urls else "discovery",
                        "requested_url": requested_url,
                        "canonical_url": canonical_url,
                        "content_id": content_id,
                        "identity_verified": bool(direct_urls and canonical_url and content_id) or not direct_urls,
                        "media_url": media_url,
                        "raw_record": {
                            k: rec.get(k)
                            for k in ("aweme_id", "note_id", "video_id", "video_download_url", "video_url")
                            if rec.get(k)
                        },
                    },
                )
            )
        if direct_urls and not items:
            fallback = await direct_fallback()
            if fallback:
                return fallback
            return CollectResult(False, self.name, message="MediaCrawler 未返回与目标链接一致的内容")
        kind = "评论" if use_comments else "帖子"
        return CollectResult(True, self.name, items=items, message=f"采集 {len(items)} 条社媒{kind}数据")


register(SocialMediaCollector())
