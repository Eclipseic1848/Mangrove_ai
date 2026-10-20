"""搜索结果正文链接不能混入页面导航或静态资源。"""
from unittest.mock import AsyncMock

import httpx
import pytest

from src.collectors import search_collector as module
from src.collectors.search_collector import SearchDiscoveryCollector


@pytest.mark.asyncio
async def test_baidu_discovers_result_links_without_navigation_or_government_exclusion(monkeypatch):
    direct = "https://notices.example/notice/1.html"
    government = "https://www.example.gov.cn/notice/2.html"
    html = f"""
    <html><head><link rel="stylesheet" href="https://assets.example/site.css"></head>
    <body>
      <nav><a href="https://www.hao123.com/">导航</a></nav>
      <a href="https://assets.example/logo.png">站点图片</a>
      <div id="content_left">
        <div class="result c-container" data-mu="{direct}">
          <h3><a href="https://www.baidu.com/link?url=synthetic">公告一</a></h3>
          <a href="https://notices.example/">来源首页</a>
        </div>
        <div class="result c-container">
          <h3><a href="{government}">公告二</a></h3>
        </div>
      </div>
      <footer><a href="https://footer.example/privacy">隐私政策</a></footer>
    </body></html>
    """
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))))

    links = await SearchDiscoveryCollector()._discover_baidu_html("合成公告", "", limit=5)

    assert links == [direct, government], "只发现结果正文；政府域名不能因后缀被排除"
