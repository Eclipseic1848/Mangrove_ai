"""视频账号的身份核验不能改用直播域；不启动浏览器或请求平台。"""
import asyncio
import sys
from types import SimpleNamespace

import pytest

from scripts import mediacrawler_identity


def test_identity_preserves_the_video_collector_origin(monkeypatch, tmp_path):
    class BrowserBoundary:
        async def __aenter__(self):
            raise RuntimeError("合成启动边界")

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(
        ENABLE_IP_PROXY=False, SAVE_LOGIN_STATE=True, ENABLE_CDP_MODE=True,
        COOKIES="session=synthetic", HEADLESS=True,
    ))
    monkeypatch.setitem(sys.modules, "playwright.async_api", SimpleNamespace(
        async_playwright=BrowserBoundary, TimeoutError=TimeoutError,
    ))
    crawler = SimpleNamespace(index_url="https://www.kuaishou.com",
                              cookie_urls=["https://www.kuaishou.com"], user_agent="synthetic")
    with pytest.raises(RuntimeError, match="合成启动边界"):
        asyncio.run(mediacrawler_identity._start_identity(
            crawler, "ks", SimpleNamespace(), tmp_path / "identity.json",
        ))
    assert crawler.index_url == "https://www.kuaishou.com"
    assert crawler.cookie_urls == ["https://www.kuaishou.com"]
