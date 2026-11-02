"""从选定凭据启动空上下文；不使用真实账号或来源。"""
import asyncio
import sys
from types import SimpleNamespace

import pytest

from scripts import mediacrawler_xhs


class Context:
    def __init__(self):
        self.rows = []
        self.closed = False

    async def add_cookies(self, rows):
        self.rows.extend(rows)

    async def close(self):
        self.closed = True


def startup(monkeypatch, tmp_path, raw, *, mode="cookie", origin="https://www.xiaohongshu.com"):
    context = Context()

    class Crawler:
        index_url = origin

        async def launch_browser(self):
            return context

    class Login:
        async def begin(self):
            raise AssertionError("已初始化的 Cookie 不得再次注入或扫码")

    config = SimpleNamespace(LOGIN_TYPE=mode, COOKIES=raw)
    monkeypatch.setitem(sys.modules, "config", config)
    core = SimpleNamespace(XiaoHongShuCrawler=Crawler, XiaoHongShuClient=type("Client", (), {}),
                           XiaoHongShuLogin=Login)
    mediacrawler_xhs._install_runtime_diagnostics(core, tmp_path)
    return core, context


@pytest.mark.parametrize("session", ["synthetic-personal", "synthetic-platform"])
def test_selected_cookie_fields_exist_before_first_navigation(monkeypatch, tmp_path, session):
    core, context = startup(monkeypatch, tmp_path, f"web_session={session}; a1=synthetic-device; webId=synthetic-web; token=value==")
    result = asyncio.run(core.XiaoHongShuCrawler().launch_browser())
    assert result is context
    assert {row["name"]: row["value"] for row in context.rows} == {
        "web_session": session, "a1": "synthetic-device", "webId": "synthetic-web", "token": "value=="}
    assert all(row["url"] == "https://www.xiaohongshu.com" for row in context.rows)


@pytest.mark.parametrize("raw", ["", "a1=synthetic", "web_session=", "web_session=synthetic; broken",
                                "web_session=synthetic; bad name=value", "web_session=synthetic\r\nHeader:value"])
def test_bad_cookie_never_reaches_navigation_or_exposes_value(monkeypatch, tmp_path, raw):
    core, context = startup(monkeypatch, tmp_path, raw)
    with pytest.raises(ValueError) as error:
        asyncio.run(core.XiaoHongShuCrawler().launch_browser())
    assert not context.rows and context.closed
    assert "synthetic" not in str(error.value)


def test_unknown_login_never_reinjects_the_original_cookie(monkeypatch, tmp_path):
    core, context = startup(monkeypatch, tmp_path, "web_session=synthetic")
    with pytest.raises(RuntimeError, match="登录尚未确认"):
        asyncio.run(core.XiaoHongShuLogin().begin())
    assert not context.rows


def test_explicit_browser_mode_keeps_its_own_context(monkeypatch, tmp_path):
    core, context = startup(monkeypatch, tmp_path, "unused", mode="qrcode")
    assert asyncio.run(core.XiaoHongShuCrawler().launch_browser()) is context
    assert not context.rows


def test_cookie_origin_is_fixed(monkeypatch, tmp_path):
    core, context = startup(monkeypatch, tmp_path, "web_session=synthetic", origin="https://unapproved.invalid")
    with pytest.raises(ValueError):
        asyncio.run(core.XiaoHongShuCrawler().launch_browser())
    assert not context.rows and context.closed


def test_initialization_cancel_survives_close_failure(monkeypatch, tmp_path):
    core, context = startup(monkeypatch, tmp_path, "web_session=synthetic")
    async def cancelled(rows):
        raise asyncio.CancelledError()
    async def failed_close():
        raise RuntimeError("合成关闭失败")
    context.add_cookies, context.close = cancelled, failed_close
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(core.XiaoHongShuCrawler().launch_browser())
