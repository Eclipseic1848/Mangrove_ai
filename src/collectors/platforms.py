# -*- coding: utf-8 -*-
"""平台名词表与归一。

known_platforms()：展示给 planner 的「规范平台名」清单，让规划输出的平台名
从源头可被路由命中。
normalize_platform()：把任意别名/大小写/含噪声的平台名归一为规范名（router 容错）。
resolve_domains()/url_in_domains()：站点限定的统一解析（内置域名表优先，未命中用
planner 给出的 site_domains 兜底），供 search/firecrawl 等采集器共用，确保"指定站点"
的约束在任何采集路径下都被尊重。
"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import urlsplit

# 专用采集器支持的「规范名」（社媒由 MediaCrawler 承接，京东为电商专用）
KNOWN_PLATFORMS: List[str] = [
    "抖音", "小红书", "微博", "B站", "快手", "知乎", "贴吧", "京东", "YouTube", "V2EX",
]

# 别名（小写）-> 规范名
_ALIASES = {
    "douyin": "抖音", "dy": "抖音",
    "xiaohongshu": "小红书", "红书": "小红书", "xhs": "小红书",
    "weibo": "微博", "wb": "微博",
    "哔哩哔哩": "B站", "bilibili": "B站", "bili": "B站", "b站": "B站",
    "kuaishou": "快手", "ks": "快手",
    "zhihu": "知乎",
    "tieba": "贴吧",
    "jd": "京东", "jingdong": "京东", "京东商城": "京东",
    "youtube": "YouTube", "yt": "YouTube",
    "v2ex": "V2EX",
}


# 平台名（中文/英文别名）-> 站点主域名。命中则用 site: 限定检索到该站。
# 社媒优先由 MediaCrawler 处理，这里作为其不可用/无数据时的通用兜底，也供 firecrawl/search 共用。
_PLATFORM_DOMAINS = {
    "懂车帝": "dongchedi.com", "dongchedi": "dongchedi.com",
    "汽车之家": "autohome.com.cn", "autohome": "autohome.com.cn",
    "知乎": "zhihu.com", "zhihu": "zhihu.com",
    "小红书": "xiaohongshu.com", "xhs": "xiaohongshu.com",
    "微博": "weibo.com", "weibo": "weibo.com",
    "b站": "bilibili.com", "B站": "bilibili.com", "哔哩哔哩": "bilibili.com", "bilibili": "bilibili.com",
    "抖音": "douyin.com", "douyin": "douyin.com",
    "快手": "kuaishou.com", "kuaishou": "kuaishou.com",
    "youtube": "youtube.com", "YouTube": "youtube.com", "yt": "youtube.com",
    "v2ex": "v2ex.com", "V2EX": "v2ex.com",
}


# 已知强反爬站：通用 HTTP/渲染引擎（firecrawl/crawl4ai/httpx）抓正文常集体失败，
# 实测仅 scrapling 的 Camoufox 隐身浏览器 或 社媒专用引擎能过（见 mangrove 记忆）。
# URL 任务命中时，router 短路让 scrapling(Camoufox) 提前，跳过注定失败的中间引擎、省时间。
#
# 2026-07-07 实测复核（httpx 直连 + extract_content 抓首页）：sohu.com/163.com/ifeng.com
# 三站均正常抓到真实正文（千字级、含当天真实标题），未表现出反爬迹象，已从清单移除，
# 交给运行时动态学习（_domain_health.py）按实际失败率判断——不再无条件强制走更慢的
# Camoufox。zhihu/toutiao/weibo/xiaohongshu/xchuxing 实测确认仍是空壳/验证墙/未渲染模板，
# 予以保留；autohome.com.cn/dongchedi.com 本轮测试方法有局限（前者遇到编码探测问题，
# 后者只测了首页未测文章详情页），无法证伪，暂维持短路。
ANTI_SCRAPE_DOMAINS = {
    "zhihu.com", "autohome.com.cn", "dongchedi.com",
    "toutiao.com", "xiaohongshu.com", "weibo.com", "xchuxing.com",
}


def url_is_anti_scrape(url: str) -> bool:
    """URL 是否指向已知强反爬站（据此让 Camoufox 隐身兜底提前）。"""
    host = (urlsplit(url if "//" in url else f"//{url}").netloc or "").split("@")[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == d or host.endswith("." + d) for d in ANTI_SCRAPE_DOMAINS)


def known_platforms() -> List[str]:
    """规范平台名清单（副本）。"""
    return list(KNOWN_PLATFORMS)


def normalize_domain(value: str) -> str:
    """把 URL/带 www 的域名归一为主域名：strip 协议、路径、前导 www。无法解析返回去空格原值。"""
    if not value:
        return ""
    s = value.strip()
    if not s:
        return ""
    # urlsplit 需要协议才能解析 netloc；无协议时补一个占位
    netloc = urlsplit(s if "//" in s else f"//{s}").netloc or s
    netloc = netloc.split("@")[-1].split(":")[0]  # 去掉用户信息与端口
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.strip().lower()


def resolve_domains(platforms: Optional[List[str]], site_domains: Optional[List[str]] = None) -> List[str]:
    """解析"站点限定"目标域名清单：内置域名表命中优先，未命中再并入 planner 给出的 site_domains。

    去重且保序（内置命中在前）。返回空列表表示不限定站点（走全网）。
    """
    out: List[str] = []
    for p in platforms or []:
        key = str(p).strip()
        d = _PLATFORM_DOMAINS.get(key) or _PLATFORM_DOMAINS.get(key.lower())
        if d and d not in out:
            out.append(d)
    for sd in site_domains or []:
        d = normalize_domain(str(sd))
        if d and d not in out:
            out.append(d)
    return out


def url_in_domains(url: str, domains: List[str]) -> bool:
    """url 的域名是否归属 domains 之一（含子域名）。domains 为空视为不限定（恒 True）。"""
    if not domains:
        return True
    host = (urlsplit(url).netloc or "").split("@")[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == d or host.endswith("." + d) for d in domains)


def normalize_platform(name: str) -> str:
    """归一平台名为规范名；无法识别时返回去空格后的原值。"""
    if not name:
        return ""
    key = name.strip()
    if key in KNOWN_PLATFORMS:
        return key
    low = key.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    if key in _ALIASES:
        return _ALIASES[key]
    # 包含匹配：规范名作为子串出现（如「小红书App」）
    for canon in KNOWN_PLATFORMS:
        if canon in key:
            return canon
    return key
