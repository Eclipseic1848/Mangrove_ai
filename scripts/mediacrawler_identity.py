"""只在独立凭证验证进程读取当前账号接口；不使用公开搜索证明身份。"""
import hashlib
import importlib
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit


_CRAWLERS = {
    "xhs": ("xhs", "XiaoHongShuCrawler", "xhs_client"),
    "bili": ("bilibili", "BilibiliCrawler", "bili_client"),
    "wb": ("weibo", "WeiboCrawler", "wb_client"),
    "dy": ("douyin", "DouYinCrawler", "dy_client"),
    "zhihu": ("zhihu", "ZhihuCrawler", "zhihu_client"),
    "tieba": ("tieba", "TieBaCrawler", "tieba_client"),
    "ks": ("kuaishou", "KuaishouCrawler", "ks_client"),
}

# 这几个平台原 start 有未认证导航、搜索或宽松 Cookie 判定；身份模式不调用原 start。
_IDENTITY_STARTS = {
    "wb": ("create_weibo_client", "mobile_index_url", "mobile_user_agent"),
    "dy": ("create_douyin_client", "index_url", None),
    "zhihu": ("create_zhihu_client", "index_url", "user_agent"),
    "tieba": ("create_tieba_client", "index_url", "user_agent"),
    "ks": ("create_ks_client", "index_url", "user_agent"),
}


async def _close_identity(resources, interrupted):
    failure = None
    for resource in resources:
        if resource is not None:
            try:
                await resource.close()
            except Exception as error:
                if failure is None:
                    failure = error
    # 清理失败不能把已有取消吞成普通探针失败；外层上下文与进程门继续收口。
    if failure is not None and not interrupted:
        raise failure


async def _kuaishou_current_account(page):
    origin = "https://www.kuaishou.com"
    home = origin + "/new-reco"
    state = {"documents": 0, "profiles": 0, "scope_unknown": False}
    closed = False

    def is_self(request):
        url = urlsplit(request.url)
        return (url.scheme == "https" and url.hostname == "www.kuaishou.com"
                and url.port in (None, 443) and url.username is None and url.password is None
                and url.path == "/rest/v/profile/get" and request.method == "GET"
                and request.resource_type in {"xhr", "fetch"}
                and not request.post_data
                and request.frame.page is page and request.frame.url == home
                and set(parse_qs(url.query, keep_blank_values=True)) <= {"caver", "__NS_hxfalcon"})

    async def guard(route):
        request = route.request
        url = urlsplit(request.url)
        secure = url.scheme == "https" and url.port in (None, 443) and url.username is None and url.password is None
        if request.resource_type == "document" and request.url == home and request.method == "GET":
            state["documents"] += 1
            if state["documents"] == 1:
                await route.continue_()
                return
            state["scope_unknown"] = True
        elif secure and request.resource_type in {"script", "stylesheet"} and request.method == "GET":
            host = url.hostname or ""
            if (url.path.endswith((".js", ".mjs", ".css")) and not url.path.startswith(("/rest/", "/graphql", "/pass/"))
                    and (host == "www.kuaishou.com" or host.endswith((".wskwai.com", ".wsbkwai.com", ".yximgs.com", ".kwimgs.com")))):
                await route.continue_()
                return
        elif request.resource_type in {"xhr", "fetch"} and is_self(request):
            state["profiles"] += 1
            if state["profiles"] == 1:
                await route.continue_()
                return
            state["scope_unknown"] = True
        # 身份检查只放行官网自发的当前账号请求，不能加载推荐、搜索或任意用户资料。
        await route.abort()

    try:
        await page.context.route("**/*", guard)
        async with page.expect_response(lambda response: is_self(response.request), timeout=20000) as pending:
            await page.goto(home, wait_until="domcontentloaded", timeout=30000)
        response = await pending.value
        if response.status != 200 or state["scope_unknown"] or state["profiles"] != 1:
            return {"status": "unknown", "reason": "identity_response_unconfirmed"}
        raw = await response.json()
        # 先停止页面再作最终判断，读取响应或关闭期间发生的重复请求也必须算未知。
        await _close_identity((page,), False)
        closed = True
        if state["scope_unknown"] or state["profiles"] != 1:
            return {"status": "unknown", "reason": "identity_response_unconfirmed"}
        if not isinstance(raw, dict):
            return {"status": "unknown", "reason": "identity_response_unconfirmed"}
        if any(raw.get(key) for key in ("error", "errors", "error_code")):
            return {"status": "unknown", "reason": "identity_response_unconfirmed"}
        message = " ".join(raw[key] for key in ("error_msg", "errorMsg", "message") if isinstance(raw.get(key), str))
        if (any(raw.get(key) is True for key in ("needCaptcha", "needVerify", "captcha_required", "verify_required"))
                or any(marker in message for marker in ("验证码", "安全验证", "访问频繁"))):
            return {"status": "unknown", "reason": "challenge_required"}
        if any(marker in message for marker in ("未登录", "请先登录", "登录已过期", "登录已失效")):
            if raw.get("result") != 1:
                return {"status": "invalid", "reason": "login_required"}
            return {"status": "unknown", "reason": "identity_response_unconfirmed"}
        if raw.get("error_msg") or raw.get("errorMsg"):
            return {"status": "unknown", "reason": "identity_response_unconfirmed"}
        uid = raw.get("userId")
        if (type(raw.get("result")) is int and raw["result"] == 1
                and (type(uid) is int and uid > 0 or isinstance(uid, str) and bool(uid.strip()) and uid.strip() != "0")):
            return {"status": "valid", "reason": "authenticated", "account_ref_kind": "account_id",
                    "account_ref": hashlib.sha256(f"ks\0{uid}".encode("utf-8")).hexdigest()}
        # 官网未返回账号时不猜测 109 等未公开结果码的含义，更不能宣称 Cookie 过期。
        return {"status": "unknown", "reason": "login_state_unconfirmed"}
    finally:
        # 保留请求门直到页面关闭，避免返回后后台重试趁清理窗口加载推荐数据。
        if not closed:
            await _close_identity((page,), sys.exc_info()[0] is not None)


async def probe_account(platform, client):
    unknown = {"status": "unknown", "reason": "identity_unverified"}
    account = None
    account_kind = "unavailable"
    try:
        if platform == "xhs":
            try:
                current = await client.get("/api/sns/web/v2/user/me", params={})
            except Exception:
                # 新接口不可用不抹掉旧接口的有效证据；取消不会进入此分支。
                current = None
            if isinstance(current, dict):
                if current.get("guest") is True:
                    return {"status": "invalid", "reason": "login_required"}
                uid = current.get("user_id")
                if current.get("guest") is False and isinstance(uid, str) and uid.strip():
                    account, account_kind = uid, "account_id"
            if account is None:
                response = await client.query_self()
                data = response.get("data", {}) if isinstance(response, dict) else {}
                info = data.get("basic_info", {})
                if response is None or response.get("success") is not True or data.get("result", {}).get("success") is not True or not isinstance(info.get("red_id"), str) or not info["red_id"].strip():
                    return unknown
                account, account_kind = info["red_id"], "handle"
        elif platform == "bili":
            data = await client.get("/x/web-interface/nav")
            if data.get("isLogin") is False:
                return {"status": "invalid", "reason": "login_required"}
            if data.get("isLogin") is not True:
                return unknown
            account, account_kind = data.get("mid"), "account_id"
        elif platform == "wb":
            data = await client.request(method="GET", url=f"{client._host}/api/config", headers=client.headers)
            if data.get("login") is False:
                return {"status": "invalid", "reason": "login_required"}
            if data.get("login") is not True:
                return unknown
            account, account_kind = data.get("uid"), "account_id"
        elif platform == "zhihu":
            data = await client.get_current_user_info()
            uid, name = data.get("uid"), data.get("name")
            if not isinstance(uid, (str, int)) or isinstance(uid, bool) or not str(uid).strip() or not isinstance(name, str) or not name.strip():
                return unknown
            account, account_kind = uid, "account_id"
        elif platform == "dy":
            data = await client.get("/aweme/v1/web/user/profile/self/", params={"aid": "6383"})
            if data.get("status_code") == 2483 or any(marker in str(data.get("status_msg") or "") for marker in ("用户未登录", "请先登录")):
                return {"status": "invalid", "reason": "login_required"}
            user = data.get("user")
            uid = user.get("uid") if isinstance(user, dict) else None
            if type(data.get("status_code")) is not int or data["status_code"] != 0 or not isinstance(uid, (str, int)) or isinstance(uid, bool) or not str(uid).strip() or str(uid) == "0":
                return unknown
            account, account_kind = uid, "account_id"
        elif platform == "tieba":
            # 同一独立 Cookie jar 直接请求，避免首页重渲染使页面 fetch 失效；不跟随登录重定向。
            response = await client.playwright_page.context.request.get(
                "https://tieba.baidu.com/dc/common/tbs", headers={"Referer": "https://tieba.baidu.com/"},
                timeout=15000, max_redirects=0,
            )
            if response.status != 200:
                return unknown
            data = await response.json()
            if not isinstance(data, dict) or any(type(data[key]) not in (int, str) or data[key] not in (0, "0") for key in ("error_code", "no") if key in data):
                return unknown
            login = data.get("is_login")
            if type(login) not in (int, str) or login not in (0, 1, "0", "1") or not isinstance(data.get("tbs"), str) or not data["tbs"].strip():
                return unknown
            if login in (0, "0"):
                return {"status": "invalid", "reason": "login_required"}
            # 接口证明当前登录态，但没有稳定账号 ID；不能用于同账号自动续跑。
        elif platform == "ks":
            return await _kuaishou_current_account(client.playwright_page)
        else:
            # 未接通可靠身份接口的平台不靠 Cookie 字段或公开资料伪造成功。
            return {"status": "unknown", "reason": "identity_probe_unsupported"}
    except Exception:
        # 网络、挑战及上游结构变化不等于凭证过期，禁止保存原始响应或异常正文。
        return {"status": "unknown", "reason": "identity_probe_failed"}
    result = {"status": "valid", "reason": "authenticated", "account_ref_kind": account_kind if account else "unavailable"}
    if isinstance(account, (str, int)) and not isinstance(account, bool) and str(account):
        result["account_ref"] = hashlib.sha256(f"{platform}\0{account}".encode("utf-8")).hexdigest()
    # handle可更改；此结果只证明本次认证，不能据此自动批准同账号续跑。
    return result


async def _start_identity(self, platform, core, output):
    import config
    from playwright.async_api import TimeoutError as BrowserTimeout, async_playwright

    factory, origin_attr, agent_attr = _IDENTITY_STARTS[platform]
    origin = getattr(self, origin_attr)
    user_agent = getattr(self, agent_attr) if agent_attr else None
    playwright_proxy, httpx_proxy = None, None
    if config.ENABLE_IP_PROXY:
        self.ip_proxy_pool = await core.create_ip_pool(config.IP_PROXY_POOL_COUNT, enable_validate_ip=True)
        info = await self.ip_proxy_pool.get_proxy()
        playwright_proxy, httpx_proxy = core.utils.format_proxy_info(info)
    # 只在本次验证进程关闭共享会话；普通采集的启动实现保持原样。
    config.SAVE_LOGIN_STATE = False
    config.ENABLE_CDP_MODE = False
    async with async_playwright() as playwright:
        context = browser = None
        try:
            if platform == "ks":
                browser = await playwright.chromium.launch(headless=config.HEADLESS, proxy=playwright_proxy)
                context = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=user_agent,
                                                    service_workers="block")
                socket_route = getattr(context, "route_web_socket", None)
                if not callable(socket_route):
                    output.write_text(json.dumps({"status": "unknown", "reason": "browser_version_unsupported"}), encoding="utf-8")
                    return
                async def close_socket(socket):
                    await socket.close()
                await socket_route("**/*", close_socket)
            else:
                context = await self.launch_browser(playwright.chromium, playwright_proxy, user_agent, headless=config.HEADLESS)
            self.browser_context = context
            cookies = core.utils.convert_str_cookie_to_dict(config.COOKIES)
            await context.add_cookies([{"name": name, "value": value, "url": origin} for name, value in cookies.items()])
            await context.add_init_script(path="libs/stealth.min.js")
            self.context_page = await context.new_page()
            if platform != "ks":
                await self.context_page.goto(origin, wait_until="domcontentloaded", timeout=60000)
            if platform == "dy":
                # 与现有抖音启动一致，等待首页重渲染；持续轮询造成的空闲超时不判凭证失效。
                try:
                    await self.context_page.wait_for_load_state("networkidle", timeout=8000)
                except BrowserTimeout:
                    pass
            client = await getattr(self, factory)(httpx_proxy)
            result = await probe_account(platform, client)
            output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        finally:
            await _close_identity((context, browser), sys.exc_info()[0] is not None)


def install_probe(platform: str, output: Path) -> bool:
    definition = _CRAWLERS.get(platform)
    if definition is None:
        output.write_text(json.dumps({"status": "unknown", "reason": "identity_probe_unsupported"}), encoding="utf-8")
        return False
    module, class_name, client_attribute = definition
    core = importlib.import_module(f"media_platform.{module}.core")
    crawler = getattr(core, class_name)

    async def search(self):
        result = await probe_account(platform, getattr(self, client_attribute))
        output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    crawler.search = search
    if platform in _IDENTITY_STARTS:
        async def start(self):
            await _start_identity(self, platform, core, output)
        crawler.start = start
    return True
