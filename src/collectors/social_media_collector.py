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
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from http.cookies import SimpleCookie
from filelock import FileLock, Timeout as LockTimeout

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


def _diagnose_mc_failure(platform: str, output: str) -> dict:
    """把 MediaCrawler 子进程原始报错翻译成用户可操作的一句话，避免把整段 traceback 抛给用户。"""
    cn = _PLATFORM_CN.get(platform, platform)
    env_name = (_COOKIE_ATTR.get(platform) or "").upper()  # 如 mc_cookie_xhs -> MC_COOKIE_XHS
    text = output or ""
    # 验证码和限流不等于过期，不能引导用户通过换凭证或换 IP 绕过验证。
    if "CAPTCHA" in text or "Verifytype" in text or "验证码" in text:
        return {"reason": "challenge_required", "next_action": "complete_platform_challenge", "message": f"{cn}触发验证码，请通过平台正常登录完成验证后重新验证采集凭证"}
    if "IPBlock" in text or "IP_ERROR" in text or "访问频次" in text:
        return {"reason": "rate_limited", "next_action": "wait_for_rate_limit", "message": f"{cn}访问频次过高被限制，请降低采集频率并等待限制解除"}
    # 注入前匿名会话 False、普通登录失败均不能证明所选凭证过期。
    if "登录已过期" in text:
        tip = f"{cn}登录已过期"
        if env_name:
            tip += f"，请在采集账号设置中更新正在使用的 Cookie；个人配置优先，无个人配置时使用平台配置"
        return {"reason": "login_required", "next_action": "reauthenticate_selected_account", "message": tip}
    # 第三方异常可能带完整请求或凭证，不能直接透传到会话和交付。
    return {"reason": "collection_failed", "next_action": "inspect_collection_service", "message": f"{cn}采集失败，请核对登录状态与采集服务配置"}


def _operation_evidence(spec: TaskSpec, result: CollectResult) -> dict:
    """只描述本次操作事实，不能用成功采集推断登录身份或未来权限。"""
    primary = "detail" if spec.urls else "search"
    failure = result.coverage.get("failure") or {}
    evidence = {primary: {
        "status": ("partial" if result.coverage.get("collector_timeout") else "succeeded") if result.success else "unknown",
        "reason": failure.get("reason", "operation_completed" if result.success else "operation_unconfirmed"),
        "next_action": failure.get("next_action", "none" if result.success else "inspect_collection_service"),
    }}
    if spec.include_comments or spec.analysis_type == AnalysisType.VOC or spec.data_type == DataType.COMMENT:
        coverage = [item.metadata.get("comment_coverage") or {} for item in result.items]
        complete = result.success and bool(coverage) and all(item.get("truncated") is False for item in coverage)
        partial = any(item.get("truncated") is True for item in coverage) or any(
            item.metadata.get("comments") or item.metadata.get("kind") == "comment" for item in result.items)
        evidence["comments"] = {
            "status": "succeeded" if complete else "partial" if partial else "unknown",
            "reason": "operation_completed" if complete else failure.get("reason", "comment_coverage_incomplete"),
            "next_action": "none" if complete else failure.get("next_action", "review_coverage"),
        }
    return evidence


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
        from src.config.cookie_probe_binding import current_probe_environment
        frozen = current_probe_environment()
        path = frozen["collector_path"] if frozen else settings.mediacrawler_path
        return bool(path) and Path(path).expanduser().is_dir()

    def matches(self, spec: TaskSpec) -> bool:
        return bool(_resolve_platform(spec))

    async def verify_cookie(self, spec: TaskSpec) -> CollectResult:
        from src.config.cookie_probe_binding import capture_probe_environment, current_probe_environment, probe_environment_context
        from src.config.cookie_identity import record_identity, identity_verification
        from src.config.user_ctx import frozen_effective_values
        key = _COOKIE_ATTR.get(_resolve_platform(spec))
        if not key:
            return await self.collect(spec, identity_probe=True)
        environment = current_probe_environment() or capture_probe_environment()
        with frozen_effective_values([key]) as values, probe_environment_context(environment), identity_verification(key, values[key], environment):
            result = await self.collect(spec, identity_probe=True)
            try:
                record_identity(key, values[key], environment, result.authentication if result.success else {})
            except OSError:
                # 身份缓存写失败不能改变原验证结论；后续恢复仍会因缺证据拒绝。
                logger.warning("采集账号证明未能保存")
            return result

    async def collect(self, spec: TaskSpec, *, identity_probe: bool = False) -> CollectResult:
        platform = _resolve_platform(spec)
        cookie = _platform_cookie(platform)
        parsed = SimpleCookie()
        try:
            parsed.load(cookie)
        except Exception:
            pass
        identity = parsed["web_session"].value if platform == "xhs" and "web_session" in parsed else ";".join(sorted(cookie.split(";")))
        key = hashlib.sha256((platform + "\0" + identity).encode("utf-8")).hexdigest()
        lock_dir = Path(settings.webui_db_path).resolve().parent / ".collector-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock = FileLock(lock_dir / (key + ".lock"), thread_local=False)
        deadline = asyncio.get_running_loop().time() + 10 * settings.collect_timeout_mediacrawler_seconds
        # 本实例按凭证串行；文件锁兼容多进程，不将凭证或 Owner 明文写入文件名。
        while True:
            from src.api.execution import execution_checkpoint
            execution_checkpoint()
            try:
                lock.acquire(timeout=0)
                break
            except LockTimeout:
                if asyncio.get_running_loop().time() >= deadline:
                    return CollectResult(False, self.name, message="共享采集凭证排队超时，请稍后重试")
                await asyncio.sleep(0.05)
        try:
            execution_checkpoint()
            # 验证、不同 Owner 和并发任务不能共享按日期追加的结果文件。
            with TemporaryDirectory(prefix="mangrove-mc-") as run_dir:
                if identity_probe:
                    return await self._collect(spec, Path(run_dir), cookie, identity_probe=True)
                result = await self._collect(spec, Path(run_dir), cookie)
                result.coverage["operations"] = _operation_evidence(spec, result)
                from src.config.user_ctx import get_user_override
                result.coverage["connection"] = {"platform": platform, "source":
                    "unconfigured" if not cookie else "personal" if get_user_override(_COOKIE_ATTR.get(platform, "")) is not None else "platform"}
                return result
        finally:
            lock.release()

    async def _collect(self, spec: TaskSpec, run_dir: Path, cookie: str, *, identity_probe: bool = False) -> CollectResult:
        if not self.is_available():
            return CollectResult(False, self.name, message="MediaCrawler 未配置（MEDIACRAWLER_PATH）")
        platform = _resolve_platform(spec)
        if identity_probe and not cookie:
            return CollectResult(False, self.name, message="未配置 Cookie，无法验证账号身份")
        direct_urls = list(spec.urls or [])
        if not platform:
            return CollectResult(False, self.name, message="未识别到 MediaCrawler 支持的平台")
        if spec.include_comments and platform != "xhs":
            return CollectResult(False, self.name, message="附带评论目前仅支持小红书")
        xhs_options = platform == "xhs" and (spec.include_comments or spec.xhs_sort or spec.xhs_note_type)
        if xhs_options and not cookie:
            return CollectResult(False, self.name, message="小红书专属采集需要配置个人或平台 Cookie")
        if not direct_urls and not spec.keywords:
            return CollectResult(False, self.name, message="社媒采集需要关键词")

        from src.config.cookie_probe_binding import current_probe_environment
        frozen = current_probe_environment()
        mc_dir = Path(frozen["collector_path"] if frozen else settings.mediacrawler_path).expanduser()
        python_exe = frozen["python"] if frozen else settings.mediacrawler_python or sys.executable
        keywords = ",".join(spec.keywords)
        # VOC/评论类任务：槽点在评论里，自动请求抓取评论
        want_comments = (
            spec.include_comments or spec.analysis_type == AnalysisType.VOC or spec.data_type == DataType.COMMENT
        )

        # 以子进程运行 MediaCrawler 搜索，结果存 JSON
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
        if "--cookies" in cmd:
            index = cmd.index("--cookies")
            del cmd[index:index + 2]
        credential_input = json.dumps(cookie, ensure_ascii=False).encode("utf-8")
        if len(credential_input) > 131072:
            return CollectResult(False, self.name, message="采集凭证超过读取上限")
        cmd[1:2] = [str(Path(__file__).resolve().parents[2] / "scripts" / "mediacrawler_isolated.py"),
                    "identity" if identity_probe else "xhs" if platform == "xhs" else "default"]
        cmd += ["--save_data_path", str(run_dir)]
        if platform == "xhs" and not direct_urls:
            cmd += ["--start", str(spec.xhs_search_page)]
        if spec.include_comments:
            cmd += ["--get_sub_comment", "yes", "--max_comments_count_singlenotes", str(spec.comment_limit)]
        # 日志不打印 cookie 明文，避免泄露登录态
        safe_cmd = [("***" if i and cmd[i - 1] in {"--cookies", "--specified_id"} else a) for i, a in enumerate(cmd)]
        logger.info("调用 MediaCrawler: %s (cwd=%s, 登录=%s)",
                    " ".join(safe_cmd), mc_dir, "cookie" if cookie else "扫码/会话")
        # 把“动态采集间隔区间”以环境变量注入子进程，供 MediaCrawler 的 config 读取，
        # 实现每次 sleep 在 [min, max] 间随机、模拟真人节奏规避频次风控。
        from src.config.secret_refs import RUNTIME_CONFIG_SECRET_KEYS
        cookie_env_keys = {key.upper() for key in RUNTIME_CONFIG_SECRET_KEYS if "cookie" in key} | {"COOKIES"}
        # 部署可能直接以环境变量提供凭证，不能让它们再次流入子进程或浏览器。
        env = dict(frozen["environment"]) if frozen else {key: value for key, value in os.environ.items() if key.upper() not in cookie_env_keys}
        # Windows 默认代码页可能是 cp1252，execjs 向 Node.js 写入含中文的签名脚本时会崩溃。
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # 显式清空未选的参数，不能继承宿主另一任务的检索范围。
        env["MANGROVE_XHS_TIMEOUT_SECONDS"] = str(frozen["timeout"] if frozen else settings.collect_timeout_mediacrawler_seconds)
        env["MANGROVE_XHS_SORT"] = spec.xhs_sort or ""
        env["MANGROVE_XHS_NOTE_TYPE"] = spec.xhs_note_type or ""
        env["MC_CRAWL_MIN_SLEEP_SEC"] = str(frozen["sleep_min"] if frozen else settings.mc_crawl_min_sleep_sec)
        env["MC_CRAWL_MAX_SLEEP_SEC"] = str(frozen["sleep_max"] if frozen else settings.mc_crawl_max_sleep_sec)
        # 代理IP池（启用时）：反国内社媒风控，优于服务端挂境外 VPN
        proxy_env = {} if frozen else _proxy_env()
        env.update(proxy_env)
        if proxy_env:
            logger.info("MediaCrawler 启用代理IP池：provider=%s", settings.mc_ip_proxy_provider)
        # 独立入口统一禁用共享 CDP，不把宿主浏览器会话当作当前凭证的验证结果。
        env["MC_ENABLE_CDP_MODE"] = "false"
        timed_out = False
        try:
            # 文件承接输出，避免子进程继承 PIPE 或输出缓冲满时阻塞超时回收。
            with TemporaryFile(mode="w+b") as process_output:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(mc_dir),
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=process_output,
                    stderr=asyncio.subprocess.STDOUT,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}),
                )
                mc_timeout = frozen["timeout"] if frozen else settings.collect_timeout_mediacrawler_seconds
                try:
                    await asyncio.wait_for(proc.communicate(input=credential_input), timeout=mc_timeout)
                except asyncio.TimeoutError:
                    if not (xhs_options and spec.include_comments):
                        return CollectResult(False, self.name, message=f"MediaCrawler 运行超时（{mc_timeout:.0f}s）")
                    # 先回收本次进程树，再只读取当前目录中完整且来源匹配的记录。
                    timed_out = True
                finally:
                    # 只回收本次创建的进程树，且回收有界；不触碰共享浏览器或未知进程。
                    if proc.returncode is None:
                        try:
                            if os.name == "nt":
                                await asyncio.to_thread(
                                    subprocess.run, ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
                                )
                            else:
                                os.killpg(proc.pid, signal.SIGKILL)
                        finally:
                            if proc.returncode is None:
                                proc.kill()
                            await asyncio.wait_for(proc.wait(), timeout=5)
                process_output.seek(0)
                stdout = process_output.read()
        except Exception as e:
            # 部分异常（如某些 OSError）str() 为空，只报错误类型看不出原因；
            # 完整堆栈记日志备查，用户看到的消息至少带上异常类型名。
            logger.exception("MediaCrawler 子进程启动失败")
            return CollectResult(False, self.name, message=f"MediaCrawler 启动失败: {type(e).__name__}: {e}")

        if proc.returncode != 0 and not timed_out:
            text = (stdout or b"").decode("utf-8", "ignore")
            # 翻译成用户可操作的一句话（登录过期/风控/频次…），并记录原始尾部到日志备查
            logger.warning("MediaCrawler 退出码 %s，平台：%s", proc.returncode, platform)
            fallback = await direct_fallback()
            if fallback:
                return fallback
            failure = _diagnose_mc_failure(platform, text)
            return CollectResult(False, self.name, message=failure.pop("message"), coverage={"failure": failure})

        if identity_probe:
            identity_path = run_dir / "identity.json"
            try:
                if not identity_path.is_file() or identity_path.stat().st_size > 4096:
                    raise ValueError("缺少有界身份探测结果")
                authentication = json.loads(identity_path.read_text(encoding="utf-8"))
                if not isinstance(authentication, dict) or authentication.get("status") not in {"valid", "invalid", "unknown"}:
                    raise ValueError("身份探测结果无效")
            except (ValueError, OSError):
                authentication = {"status": "unknown", "reason": "identity_probe_failed"}
            return CollectResult(authentication["status"] == "valid", self.name, authentication=authentication)

        # 只读本次调用、当前平台的产物；共享历史文件的修改时间不能证明数据归属。
        data_dir = run_dir / {"dy": "douyin", "wb": "weibo", "ks": "kuaishou"}.get(platform, platform) / "json"
        records: list = []
        for jf in sorted(data_dir.glob("*.json")):
            try:
                _flatten_records(json.loads(jf.read_text(encoding="utf-8")), records)
            except Exception:
                continue
        expected_ids = {content_id_from_url(u) for u in direct_urls if content_id_from_url(u)}
        direct_limit = min(len(direct_urls), spec.max_items) if direct_urls else spec.max_items
        search_coverage_path = run_dir / "search_coverage.json"
        search_coverage = json.loads(search_coverage_path.read_text(encoding="utf-8")) if search_coverage_path.is_file() else {}

        if not records and timed_out:
            return CollectResult(False, self.name, message=f"MediaCrawler 运行超时（{mc_timeout:.0f}s），未取得可保留数据")
        if not records:
            pages = search_coverage.get("pages") or []
            if pages and all(page.get("item_count") == 0 and page.get("has_more") is False for page in pages):
                return CollectResult(True, self.name, message="本次检索未返回更多内容", coverage=search_coverage)
            fallback = await direct_fallback()
            if fallback:
                return fallback
            return CollectResult(False, self.name, message="MediaCrawler 未产出可解析结果")

        # 区分评论与帖子；VOC 优先用评论（无评论则回退帖子）
        comments = [r for r in records if r.get("comment_id")]
        contents = [r for r in records if not r.get("comment_id")]
        if not direct_urls:
            expected_keywords = {word.strip() for word in keywords.split(",") if word.strip()}
            # 搜索来源缺失或串词时失败关闭，不能把别的任务结果交给分析器。
            if not contents or any(str(r.get("source_keyword") or "").strip() not in expected_keywords for r in contents):
                return CollectResult(False, self.name, message="采集结果的搜索词与本次任务不一致或缺失，已拒绝使用")
        # 附带评论不改变帖子的结果粒度；原有纯评论任务仍返回评论。
        use_comments = want_comments and bool(comments) and not spec.include_comments
        chosen = comments if use_comments else (contents or records)
        coverage_path = run_dir / "comment_coverage.json"
        coverage = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.is_file() else {}

        items: list[CollectedItem] = []
        for rec in chosen[: direct_limit]:
            canonical_url = rec.get("note_url") or rec.get("aweme_url") or rec.get("video_url") or rec.get("url") or ""
            content_id = str(rec.get("aweme_id") or rec.get("note_id") or rec.get("video_id") or "")
            if direct_urls and (not canonical_url or not content_id or (expected_ids and content_id not in expected_ids)):
                continue
            requested_url = direct_urls[0] if direct_urls else ""
            public_fields = {}
            if platform == "xhs":
                from ._xhs import public_note_fields, public_note_url

                canonical_url = public_note_url(str(canonical_url))
                requested_url = public_note_url(str(requested_url))
                if not rec.get("comment_id"):
                    public_fields = public_note_fields(rec, comments if spec.include_comments else [])
                    if spec.include_comments:
                        public_fields["comment_coverage"] = coverage.get(content_id, {
                            "status": "partial" if timed_out else "unknown", "truncated": True,
                            "reasons": ["collector_timeout" if timed_out else "coverage_not_reported"],
                        })
            media_url = rec.get("video_download_url") or rec.get("video_url") or ""
            if platform == "xhs":
                media_url = public_note_url(str(media_url))
            content = rec.get("content") or rec.get("desc") or rec.get("title") or ""
            access_handle = None
            if platform == "xhs" and spec._collection_task_id:
                from .source_access import seal_access, xhs_access_url
                access_url = xhs_access_url(rec)
                if access_url:
                    access_handle = seal_access(spec._collection_task_id, content_id, access_url)
            items.append(
                CollectedItem(
                    access_handle=access_handle,
                    url=canonical_url,
                    title=rec.get("title") or rec.get("nickname") or "",
                    content=str(content),
                    metadata={
                        **public_fields,
                        "engine": self.name,
                        "platform": platform,
                        "kind": "comment" if rec.get("comment_id") else "post",
                        "collection_mode": "direct" if direct_urls else "discovery",
                        "source_keyword": rec.get("source_keyword") or "",
                        "requested_url": requested_url,
                        "canonical_url": canonical_url,
                        "content_id": content_id,
                        "identity_verified": bool(direct_urls and canonical_url and content_id) or not direct_urls,
                        "media_url": media_url,
                        "raw_record": {
                            k: (public_note_url(str(rec[k])) if platform == "xhs" and k in {"video_download_url", "video_url"} else rec.get(k))
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
        partial_comments = sum(bool(item.metadata.get("comment_coverage", {}).get("truncated")) for item in items)
        message = f"采集 {len(items)} 条社媒{kind}数据"
        if timed_out or partial_comments:
            search_coverage.update(partial=True, collector_timeout=timed_out, incomplete_comment_notes=partial_comments)
            message += ("；采集超时，已保留有效数据" if timed_out else "") + f"；{partial_comments} 篇评论未采全，详见覆盖说明"
        return CollectResult(True, self.name, items=items, message=message, coverage=search_coverage)


register(SocialMediaCollector())
