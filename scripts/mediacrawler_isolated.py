"""在独立浏览器上下文运行采集器；凭证只从父进程管道读取。"""
from pathlib import Path
import json
import runpy
import sys


def main():
    adapter = sys.argv.pop(1)
    if adapter not in {"default", "xhs", "identity"}:
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
    config.ENABLE_CDP_MODE = False
    if adapter == "identity":
        if not cookie:
            raise ValueError("身份验证不能使用扫码或旧会话替代空凭证")
        from mediacrawler_identity import install_probe
        platform = sys.argv[sys.argv.index("--platform") + 1]
        output = Path(sys.argv[sys.argv.index("--save_data_path") + 1]) / "identity.json"
        if not install_probe(platform, output):
            return
    entry = (Path(__file__).with_name("mediacrawler_xhs.py")
             if adapter == "xhs" else Path.cwd() / "main.py")
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
