"""在独立浏览器上下文运行采集器；凭证只从父进程管道读取。"""
from pathlib import Path
import json
import os
import re
import runpy
import sys


def _kuaishou_cookie_dict(cookie):
    cookies = {}
    for field in cookie.split(";"):
        if not field.strip():
            continue
        name, separator, value = field.strip().partition("=")
        name = name.strip()
        if not separator:
            continue
        if not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", name):
            raise ValueError("快手 Cookie 字段格式无效")
        cookies[name] = value.strip()
    return cookies


def _isolated_cause_type(error):
    allowed = {
        ("builtins", "RuntimeError"): "builtin_runtime_error", ("builtins", "ValueError"): "builtin_value_error",
        ("builtins", "TypeError"): "builtin_type_error", ("builtins", "KeyError"): "builtin_key_error",
        ("builtins", "OSError"): "builtin_os_error", ("builtins", "FileNotFoundError"): "builtin_file_not_found",
        ("builtins", "PermissionError"): "builtin_permission_error", ("builtins", "ModuleNotFoundError"): "builtin_module_not_found",
        ("builtins", "ImportError"): "builtin_import_error", ("builtins", "TimeoutError"): "builtin_timeout_error",
        ("builtins", "ConnectionError"): "builtin_connection_error",
        ("builtins", "AttributeError"): "builtin_attribute_error", ("builtins", "NameError"): "builtin_name_error",
        ("builtins", "IndexError"): "builtin_index_error", ("builtins", "AssertionError"): "builtin_assertion_error",
        ("json.decoder", "JSONDecodeError"): "json_decode_error",
        ("playwright._impl._errors", "Error"): "playwright_error",
        ("playwright._impl._errors", "TimeoutError"): "playwright_timeout",
        ("playwright._impl._errors", "TargetClosedError"): "playwright_target_closed",
        ("httpx", "ConnectError"): "httpx_connect_error", ("httpx", "ConnectTimeout"): "httpx_connect_timeout",
        ("httpx", "ReadTimeout"): "httpx_read_timeout", ("httpx", "HTTPStatusError"): "httpx_http_status",
        ("httpx", "RemoteProtocolError"): "httpx_remote_protocol",
        ("media_platform.xhs.exception", "DataFetchError"): "xhs_data_fetch",
        ("media_platform.xhs.exception", "IPBlockError"): "xhs_ip_block",
        ("media_platform.xhs.exception", "NoteNotFoundError"): "xhs_note_not_found",
    }
    cause, seen, types = error.__cause__, {id(error)}, set()
    # 只读已有因果链；未知、冲突、循环或超过四节点都不猜，更不能调用重试对象。
    for _ in range(4):
        if cause is None:
            break
        if id(cause) in seen:
            return None
        seen.add(id(cause))
        cause_type = type(cause)
        identity = (cause_type.__module__, cause_type.__name__)
        if identity != ("tenacity", "RetryError"):
            if identity not in allowed:
                return None
            types.add(allowed[identity])
            if len(types) > 1:
                return None
        cause = cause.__cause__
    return next(iter(types)) if cause is None and len(types) == 1 else None


def _isolated_rejection(error):
    error_type = type(error)
    module = sys.modules.get("media_platform.xhs.client")
    token = os.environ.get("MANGROVE_MC_DIAGNOSTIC_TOKEN", "")
    if (error_type.__module__ != "media_platform.xhs.client" or error_type.__name__ != "PlatformAccessError"
            or getattr(module, "PlatformAccessError", None) is not error_type
            or re.fullmatch(r"[0-9a-f]{32}", token) is None):
        return None
    attributes = vars(error)
    if "http_status" not in attributes or "platform_code" not in attributes:
        return None
    status, code = attributes["http_status"], attributes["platform_code"]
    if type(status) is not int or (code is not None and type(code) is not int):
        return None
    if (status, code) not in {(401, None), (403, None), (429, None), (461, None), (471, None), (200, 300011), (200, 300012)}:
        return None
    return {"version": 1, "token": token, "http_status": status, "platform_code": code}


def main():
    adapter = sys.argv.pop(1)
    if adapter not in {"default", "xhs", "xhs-browser", "identity"}:
        raise ValueError("不支持的采集适配入口")
    raw = sys.stdin.buffer.read(131073)
    if len(raw) > 131072:
        raise ValueError("采集凭证超过读取上限")
    cookie = json.loads(raw)
    if not isinstance(cookie, str):
        raise ValueError("采集凭证必须为文本")
    if cookie:
        sys.argv.extend(["--cookies", cookie])
    sys.path.insert(0, str(Path.cwd()))
    import config

    config.COOKIES = cookie
    config.LOGIN_TYPE = "cookie" if cookie else "qrcode"
    # 不让旧持久会话或宿主 CDP 账号替代本次选定的凭证；无凭证时也仅在当前会话登录。
    config.SAVE_LOGIN_STATE = False
    if adapter == "xhs-browser":
        profile = Path(os.environ.get("MANGROVE_XHS_BROWSER_PROFILE", ""))
        platform = sys.argv[sys.argv.index("--platform") + 1] if "--platform" in sys.argv else ""
        if cookie or platform != "xhs" or not profile.is_absolute() or not profile.is_dir() or profile.name != "xhs_user_data_dir":
            raise ValueError("平台浏览器会话配置无效")
        config.SAVE_LOGIN_STATE = True
        config.HEADLESS = False
        # 上游使用 % platform 拼接；绝对路径覆盖 cwd，百分号按字面量转义。
        config.USER_DATA_DIR = str(profile.resolve().parent).replace("%", "%%") + "/%s_user_data_dir"
    else:
        os.environ.pop("MANGROVE_XHS_BROWSER_PROFILE", None)
    config.ENABLE_CDP_MODE = False
    platform = (sys.argv[sys.argv.index("--platform") + 1]
                if "--platform" in sys.argv else getattr(config, "PLATFORM", ""))
    # Cookie 采集无需桌面；显式平台浏览器会话仍保留自己的显示与登录方式。
    if platform == "xhs" and cookie:
        config.HEADLESS = True
    if platform == "ks":
        from tools import crawler_util, utils
        # 原解析器会丢掉含等号的登录字段；在 core 导入前同时修正公共导出与原函数。
        crawler_util.convert_str_cookie_to_dict = utils.convert_str_cookie_to_dict = _kuaishou_cookie_dict
    if adapter == "identity":
        if not cookie:
            raise ValueError("身份验证不能使用扫码或旧会话替代空凭证")
        from mediacrawler_identity import install_probe
        output = Path(sys.argv[sys.argv.index("--save_data_path") + 1]) / "identity.json"
        if not install_probe(platform, output):
            return
    entry = (Path(__file__).with_name("mediacrawler_xhs.py")
             if adapter in {"xhs", "xhs-browser"} else Path.cwd() / "main.py")
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        error_type = type(error)
        isolated_type = "UnknownError"
        if error_type.__module__ == "builtins" and error_type.__name__ in {
            "RuntimeError", "ValueError", "TypeError", "KeyError", "OSError", "FileNotFoundError",
            "PermissionError", "ModuleNotFoundError", "ImportError", "TimeoutError", "ConnectionError",
        }:
            isolated_type = error_type.__name__
        elif error_type.__module__ == "playwright._impl._errors" and error_type.__name__ in {
            "Error", "TimeoutError", "TargetClosedError",
        }:
            isolated_type = "Playwright" + error_type.__name__
        try:
            print("MANGROVE_MC_EXCEPTION_TYPE:" + isolated_type, file=sys.stderr, flush=True)
            rejection = _isolated_rejection(error)
            if rejection:
                # 只输出已知拒绝的固定字段，并绑定本次父进程，不能把旧日志当新证明。
                print("MANGROVE_MC_REJECTION:" + json.dumps(rejection, sort_keys=True), file=sys.stderr, flush=True)
            cause_type = _isolated_cause_type(error)
            if cause_type:
                # 小写闭集枚举不触发旧业务分类器；没有唯一可信原因就不输出标记。
                print("MANGROVE_MC_CAUSE_TYPE:" + cause_type, file=sys.stderr, flush=True)
        except Exception:
            # 诊断写入失败不能遮蔽原异常，仍沿原退出和失败关闭行为传播。
            pass
        raise
