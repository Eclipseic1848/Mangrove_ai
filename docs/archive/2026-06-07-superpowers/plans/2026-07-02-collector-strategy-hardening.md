# 采集器策略加固 Plan

> **For agentic workers:** Execute inline task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 消除 P0 策略隐患——修复采集器无效匹配浪费调用、补齐搜索后端的可观测性

**Architecture:** 不改动架构，只做三件事：(1) 收紧 crawl4ai/scrapling 的 matches() 防止无 URL 时无效调用；(2) 收紧 article 的 matches() 排除已知 JS 重度站；(3) 确认 SearXNG Docker 就绪

**Tech Stack:** Python (httpx, trafilatura), Docker

## Global Constraints

- 不改动 tier 排序和降级链逻辑
- 测试驱动：先写测试 → 看它失败 → 最小实现 → 验证全绿
- 中文注释

---

### Task 1: crawl4ai `matches()` 加 URL 守卫

**Files:**
- Modify: `src/collectors/crawl4ai_collector.py:35`
- Test: `scripts/test_article_collector.py` (追加测试)

**Interfaces:**
- Consumes: `TaskSpec` (urls, data_type fields)
- Produces: `Crawl4AICollector.matches(spec) -> bool`

- [ ] **Step 1: 写失败测试**

```python
def test_crawl4ai_skips_keyword_only():
    """crawl4ai: no URL → skip (wasted call otherwise)."""
    from src.collectors.crawl4ai_collector import Crawl4AICollector
    spec = TaskSpec(intent="search this", keywords=["AI"], data_type=DataType.GENERIC, urls=[])
    assert not Crawl4AICollector().matches(spec), "keyword-only tasks should skip crawl4ai"

def test_crawl4ai_matches_url():
    """crawl4ai: has URL → matches."""
    from src.collectors.crawl4ai_collector import Crawl4AICollector
    spec = TaskSpec(intent="fetch", urls=["https://example.com"], data_type=DataType.GENERIC)
    assert Crawl4AICollector().matches(spec)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/test_article_collector.py`
Expected: `test_crawl4ai_skips_keyword_only` FAIL

- [ ] **Step 3: 实现——给 Crawl4AICollector 加 matches()**

```python
def matches(self, spec: TaskSpec) -> bool:
    # 无 URL 的关键词任务由 search 负责，不浪费浏览器资源做无效调用
    return bool(spec.urls)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python scripts/test_article_collector.py`
Expected: both new tests PASS, all existing tests still PASS

---

### Task 2: scrapling `matches()` 加 URL 守卫

**Files:**
- Modify: `src/collectors/scrapling_collector.py`
- Test: `scripts/test_article_collector.py` (追加测试)

- [ ] **Step 1: 写失败测试**

```python
def test_scrapling_skips_keyword_only():
    from src.collectors.scrapling_collector import ScraplingCollector
    spec = TaskSpec(intent="search", keywords=["AI"], data_type=DataType.GENERIC, urls=[])
    assert not ScraplingCollector().matches(spec)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python scripts/test_article_collector.py`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
def matches(self, spec: TaskSpec) -> bool:
    return bool(spec.urls)
```

- [ ] **Step 4: 跑测试确认通过**

---

### Task 3: article `matches()` 排除 JS 重度站

**Files:**
- Modify: `src/collectors/article_collector.py`
- Test: `scripts/test_article_collector.py` (追加测试)

- [ ] **Step 1: 写测试**

```python
def test_article_skips_js_heavy_sites():
    """JS-heavy sites should be handled by crawl4ai/firecrawl, not article (httpx can't render)."""
    spec = TaskSpec(intent="...", urls=["https://www.zhihu.com/question/123"],
                    data_type=DataType.ARTICLE, keywords=[])
    assert not ArticleCollector().matches(spec)

def test_article_still_matches_news():
    """Normal news sites should still match article."""
    spec = TaskSpec(intent="news", urls=["https://www.thepaper.cn/newsDetail_123"],
                    data_type=DataType.ARTICLE, keywords=[])
    assert ArticleCollector().matches(spec)
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

在 ArticleCollector 的 matches() 中检测 URL 是否指向已知 JS 重度站：
```python
_JS_HEAVY_DOMAINS = {"zhihu.com", "weibo.com", "xiaohongshu.com", "douyin.com",
                      "bilibili.com", "kuaishou.com", "taobao.com", "tmall.com"}

def _is_js_heavy(url: str) -> bool:
    from urllib.parse import urlsplit
    host = (urlsplit(url).netloc or "").lower().replace("www.", "")
    return any(host == d or host.endswith("." + d) for d in _JS_HEAVY_DOMAINS)

# 在 matches() 中添加：
if spec.urls and all(_is_js_heavy(u) for u in spec.urls):
    return False  # 交给 crawl4ai/firecrawl
```

- [ ] **Step 4: 跑测试确认通过**

---

### Task 4: SearXNG Docker 就绪确认 + 搜索可观测性

**Files:**
- Read: `docker/searxng/docker-compose.yml`
- Read: `docker/searxng/config/settings.yml`
- Modify: `src/collectors/search_collector.py` (加启动时自检日志)

- [ ] **Step 1: 确认 Docker Compose 配置正确**

阅读 `docker/searxng/docker-compose.yml`，确认端口和 settings 路径

- [ ] **Step 2: 在 search 采集器 is_available() 中加可观测性日志**

```python
def is_available(self) -> bool:
    backends = self._discovery_backends()
    if not backends and not self._use_tavily():
        logger.warning("search 采集器：所有搜索后端不可用！"
                       "建议 docker compose -f docker/searxng up -d 部署 SearXNG，"
                       "或在 .env 配置 TAVILY_API_KEY")
    return True
```

这个改动让 search 采集器在启动时主动告知运维缺失了什么，而不是静默失败。

---

## 验证

1. `python scripts/test_article_collector.py` —— 所有采集器匹配测试通过
2. `python scripts/test_analyze_routing.py` —— 路由测试不受影响
3. 全量回归（test_planner, test_intent, test_template_learning 等）
