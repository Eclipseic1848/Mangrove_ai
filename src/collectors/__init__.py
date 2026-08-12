"""
采集层：可插拔的 Collector 适配器 + 能力升级式路由。

导入本包即完成内置采集器的注册（通过各模块底部的 register(...)）。
"""
from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register, get_registry, select_collectors

# 导入即注册内置采集器（顺序无关，按 tier 排序使用）
# 各采集器在依赖/配置缺失时通过 is_available() 自动降级，导入本身不应失败。
from . import direct_video_collector  # noqa: F401  tier -1 直接视频文件
from . import social_media_collector  # noqa: F401  tier 0  社媒专用(MediaCrawler)
from . import rsshub_collector  # noqa: F401         tier 5  RSSHub 轻量社媒/内容关键词
from . import youtube_collector  # noqa: F401        tier 8  YouTube专用(yt-dlp,免密钥,可选依赖)
from . import ecommerce_collector  # noqa: F401      tier 10 电商专用(京东评论,免登录)
from . import rss_collector  # noqa: F401            tier 12 RSS/站点地图订阅发现+抓取
from . import v2ex_collector  # noqa: F401           tier 13 V2EX专用(公开JSON API,免密钥)
from . import article_collector  # noqa: F401        tier 15 文章/新闻正文提取(httpx+Trafilatura级联)
from . import site_crawler_collector  # noqa: F401    tier 18 BFS整站递归爬取(招投标扫标/博客遍历)
from . import firecrawl_collector  # noqa: F401      tier 20 全网发现+抓取
from . import search_collector  # noqa: F401         tier 25 关键词→搜索发现链接→抓取
from . import crawl4ai_collector  # noqa: F401       tier 30 通用默认引擎
from . import scrapling_collector  # noqa: F401      tier 50 反爬/自愈升级层
from . import simple_http_collector  # noqa: F401     tier 90 零依赖兜底
from . import browser_collector  # noqa: F401         tier 99 浏览器交互兜底

from .platforms import (  # noqa: F401  平台词表/归一/站点域名解析
    known_platforms,
    normalize_platform,
    normalize_domain,
    resolve_domains,
    url_in_domains,
)

__all__ = [
    "BaseCollector",
    "CollectedItem",
    "CollectResult",
    "register",
    "get_registry",
    "select_collectors",
    "known_platforms",
    "normalize_platform",
    "normalize_domain",
    "resolve_domains",
    "url_in_domains",
]
