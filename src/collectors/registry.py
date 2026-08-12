"""
采集器注册表 + 能力升级式路由。

select_collectors(spec) 返回一个按优先级排序、且当前可用、且适用于该任务的
采集器列表。采集节点依次尝试，命中数据即停止（实现"被封/空 → 自动升级"）。

默认按 tier 升序（快→强）；对已知强反爬站的 URL 任务做短路——让 scrapling
（Camoufox 隐身）提前，跳过在这类站注定失败的 firecrawl/crawl4ai（2026-07-03）。
"""
from __future__ import annotations

import logging
from typing import Dict, List

from src.conductor.task_spec import TaskSpec

from .base import BaseCollector
from ._domain_health import is_learned_anti_scrape
from ._metrics import is_unhealthy

logger = logging.getLogger(__name__)

# 名称 -> 采集器实例
_REGISTRY: Dict[str, BaseCollector] = {}

# 近期成功率过低（见 _metrics.is_unhealthy）时的降权幅度：仍会被尝试，只是排到健康
# 采集器之后——只降权不排除，避免某任务类型仅此一个候选时被完全排除导致无引擎可用。
_UNHEALTHY_TIER_PENALTY = 1000

# 专属数据源采集器：没有真正的替代品，不参与健康度降权。
# 例：mediacrawler 一旦"不健康"被降权，可能被 search(25) 反超——但 search.matches()
# 不检查平台，只要"有关键词无URL"就命中，返回的是泛化网页搜索结果，不是真实的社媒
# 平台数据，跟"某个 URL 引擎失败、换另一个引擎抓同一页面"完全不是一回事，比原来直接
# 报错（如"Cookie 过期"）更容易误导人；ecommerce 同理（专属电商 API，非泛化网页抓取）。
_UNIQUE_SOURCE_COLLECTORS = {"mediacrawler", "ecommerce"}


def register(collector: BaseCollector) -> None:
    """注册一个采集器实例（重复名称覆盖）。"""
    _REGISTRY[collector.name] = collector
    logger.debug("已注册采集器: %s (tier=%s)", collector.name, collector.tier)


def get_registry() -> Dict[str, BaseCollector]:
    return dict(_REGISTRY)


# 强反爬站短路时，scrapling(Camoufox 隐身)的“有效 tier”——提前到 article(15) 之后、
# site_crawler(18)/firecrawl(20)/crawl4ai(30) 之前，跳过在强反爬站注定失败的中间引擎。
_SCRAPLING_ANTISCRAPE_TIER = 16


def _is_anti_scrape_task(spec: TaskSpec) -> bool:
    """URL 任务是否指向强反爬站——静态清单（platforms.ANTI_SCRAPE_DOMAINS）或运行时
    学到的"近期非隐身引擎持续失败"域名（_domain_health），任一命中即短路让 Camoufox 提前。
    """
    from .platforms import url_is_anti_scrape
    if not spec.urls:
        return False
    return any(url_is_anti_scrape(u) or is_learned_anti_scrape(u) for u in spec.urls)


def _effective_tier(c: BaseCollector, anti_scrape: bool) -> int:
    """采集器的有效排序键。强反爬 URL 任务时把 scrapling 的隐身能力提前（其余不变）；
    近期成功率过低（is_unhealthy）时降权排后，让健康的候选优先，但仍保留被尝试的机会
    （专属数据源采集器除外，见 _UNIQUE_SOURCE_COLLECTORS）。
    """
    if anti_scrape and c.name == "scrapling":
        return _SCRAPLING_ANTISCRAPE_TIER
    if c.name not in _UNIQUE_SOURCE_COLLECTORS and is_unhealthy(c.name):
        return c.tier + _UNHEALTHY_TIER_PENALTY
    return c.tier


def select_collectors(spec: TaskSpec) -> List[BaseCollector]:
    """挑选适用且可用的采集器，按 tier 升序（优先级高在前）。

    能力升级式路由默认“快→强”（多数站友好，先试轻快引擎秒回）；但对已知强反爬站，
    firecrawl/crawl4ai 注定失败，故短路让 scrapling(Camoufox 隐身)提前，跳过空跑。
    """
    candidates = [
        c
        for c in _REGISTRY.values()
        if c.is_available() and c.matches(spec)
    ]
    anti_scrape = _is_anti_scrape_task(spec)
    candidates.sort(key=lambda c: _effective_tier(c, anti_scrape))
    if anti_scrape:
        logger.info("强反爬站短路：scrapling(Camoufox) 提前至通用引擎之前")
    return candidates
