# -*- coding: utf-8 -*-
"""时效（recency）工具：把 time_range 自然语言解析为时间窗，并对采集结果按发布日期过滤+倒序。

供 search/firecrawl 等采集器共用，落实"要最新"的约束：
- parse_time_range：自然语言 → RecencyWindow（含天数窗口与搜索后端档位）；
- extract_publish_date：从条目元数据/正文/URL 中尽力解析发布日期；
- apply_recency：丢弃早于时间窗的条目（仅在能确信解析到更早日期时），并按日期倒序（新→旧）。

设计取舍：日期解析不可靠时**保留**条目（不臆断、不误杀），仅把能确信判旧的剔除；
排序则让有日期的新条目浮到最前，无日期的垫后，从而"最新"优先。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional
from urllib.parse import urlsplit

# 中文数字（仅覆盖常见小数值，足够 time_range 表达）
_CN_NUM = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _to_int(token: str) -> Optional[int]:
    token = token.strip()
    if token.isdigit():
        return int(token)
    if token in _CN_NUM:
        return _CN_NUM[token]
    # 十几 / 几十 简单处理
    if token == "十":
        return 10
    return None


@dataclass
class RecencyWindow:
    """时效窗口。days 为硬窗口天数（None=不设硬窗口，仅按时间排序偏好新）；label 供搜索后端档位。"""
    days: Optional[int]
    label: Optional[str]  # 'day' | 'week' | 'month' | 'year' | None


def _label_of(days: Optional[int]) -> Optional[str]:
    if days is None:
        return None
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"


def parse_time_range(time_range: Optional[str]) -> Optional[RecencyWindow]:
    """把 time_range 自然语言解析为 RecencyWindow；无法识别或为空返回 None（即不做时效处理）。"""
    if not time_range:
        return None
    s = str(time_range).strip().lower()
    if not s:
        return None

    # 纯"最新/最近/近期/latest"且无具体数值 → 不设硬窗口，仅按时间倒序（避免误杀真正最新但非近几天的内容）
    if re.search(r"最新|最近期|近期|latest|recent|newest", s) and not re.search(r"\d|[一二两三四五六七八九十]", s):
        return RecencyWindow(days=None, label=None)

    # "YYYY 年/-/. 至今" 或 "YYYY 年以来"
    m = re.search(r"(20\d{2})\s*[年\-./]?.*?(至今|以来|起)", s)
    if m:
        try:
            since = date(int(m.group(1)), 1, 1)
            days = max(1, (date.today() - since).days)
            return RecencyWindow(days=days, label=_label_of(days))
        except Exception:
            pass

    # "最近/近 N 天/周/月/年"、"N 天内"、"今天/本周/本月/今年" 等
    num_unit = re.search(r"(\d+|[一二两三四五六七八九十])\s*(天|日|周|星期|个?月|年)", s)
    if num_unit:
        n = _to_int(num_unit.group(1))
        unit = num_unit.group(2)
        if n:
            if "天" in unit or "日" in unit:
                days = n
            elif "周" in unit or "星期" in unit:
                days = n * 7
            elif "月" in unit:
                days = n * 30
            else:  # 年
                days = n * 365
            return RecencyWindow(days=days, label=_label_of(days))

    if re.search(r"今天|当天|24\s*小时|今日", s):
        return RecencyWindow(days=1, label="day")
    if re.search(r"本周|这周|一周内", s):
        return RecencyWindow(days=7, label="week")
    if re.search(r"本月|这个?月|一月内|30\s*天", s):
        return RecencyWindow(days=30, label="month")
    if re.search(r"今年|本年|一年内", s):
        return RecencyWindow(days=365, label="year")

    return None


# 日期解析：元数据键 + 正文/URL 常见格式
_META_DATE_KEYS = (
    "publishedTime", "published_time", "publish_time", "publishTime",
    "articlePublishedTime", "article:published_time", "date", "datePublished",
    "pubDate", "release_time", "created_at", "last_update_time",
)
_DATE_PATTERNS = [
    re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})"),
    re.compile(r"(20\d{2})[-/.年](\d{1,2})月?"),  # 年月
]


def _parse_date_str(text: str) -> Optional[date]:
    if not text:
        return None
    t = str(text)
    # ISO/时间戳优先
    iso = re.search(r"(20\d{2})-(\d{2})-(\d{2})", t)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except Exception:
            pass
    for pat in _DATE_PATTERNS:
        m = pat.search(t)
        if m:
            try:
                y = int(m.group(1))
                mo = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else 1
                d = int(m.group(3)) if m.lastindex and m.lastindex >= 3 else 1
                mo = min(max(mo, 1), 12)
                d = min(max(d, 1), 28)
                return date(y, mo, d)
            except Exception:
                continue
    return _parse_relative_date(t)


def _parse_relative_date(t: str) -> Optional[date]:
    """相对日期与无年份日期（中文站常见发布时间格式，P1-3 增强）。

    仅在绝对日期（ISO/年月日）都解析不到时才尝试；模式都要求明确后缀
    （"前"/"月...日"），避免把正文里的普通数字误认成日期。
    """
    today = date.today()
    # "刚刚 / N分钟前 / N小时前 / 今天 / 今日" → 今天
    if re.search(r"刚刚|(\d{1,3})\s*(?:分钟|小时|秒)前|今天|今日", t):
        return today
    if "昨天" in t or "昨日" in t:
        return today - timedelta(days=1)
    if "前天" in t:
        return today - timedelta(days=2)
    m = re.search(r"(\d{1,2})\s*[天日]前", t)
    if m:
        return today - timedelta(days=int(m.group(1)))
    # "MM月DD日"（无年份，如"07月05日"）：按今年算，若在未来则回退一年（跨年场景）
    m = re.search(r"(?<![\d年])(\d{1,2})\s*月\s*(\d{1,2})\s*日", t)
    if m:
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
            return d if d <= today + timedelta(days=1) else d.replace(year=today.year - 1)
        except Exception:
            pass
    # "MM-DD hh:mm"（微博等常见，如"07-05 12:30"）：必须带时间部分才认，避免误伤普通数字段
    m = re.search(r"(?<!\d)(\d{1,2})-(\d{1,2})\s+\d{1,2}:\d{2}", t)
    if m:
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
            return d if d <= today + timedelta(days=1) else d.replace(year=today.year - 1)
        except Exception:
            pass
    return None


# HTML 层日期提取（P1-3）：正文提取会剥掉 meta/JSON-LD，须在拿到原始 HTML 时先捕获。
# 常见发布时间 meta 键（property/name/itemprop 属性值，统一小写比较）
_HTML_META_DATE_KEYS = {
    "article:published_time", "og:published_time", "og:release_date",
    "datepublished", "pubdate", "publishdate", "publish_date", "publish-date",
    "published_time", "publishtime", "publish_time", "date",
    "dcterms.date", "dc.date", "dc.date.issued", "weibo:article:create_at",
    "apub:time", "sailthru.date",
}
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_META_KEY_RE = re.compile(r'(?:property|name|itemprop)\s*=\s*["\']([^"\']+)["\']', re.I)
_META_CONTENT_RE = re.compile(r'content\s*=\s*["\']([^"\']+)["\']', re.I)
_JSONLD_DATE_RE = re.compile(r'"(?:datePublished|dateCreated|uploadDate)"\s*:\s*"([^"]+)"')
_TIME_TAG_RE = re.compile(r'<time\b[^>]*datetime\s*=\s*["\']([^"\']+)["\']', re.I)


def extract_date_from_html(html: str) -> Optional[date]:
    """从原始 HTML 提取发布日期：meta 标签 → JSON-LD → <time> 标签（P1-3 多路解析）。

    供正文提取（_extract.extract_content）在剥离 HTML 前调用，把日期存进
    metadata["date"]，下游 extract_publish_date 按元数据键直接命中。
    """
    if not html:
        return None
    head = html[:300_000]  # 发布时间几乎都在头部区域，限量防超大页面拖慢
    # 1) meta 标签（兼容 property/name/itemprop 与 content 的任意属性顺序）
    for tag in _META_TAG_RE.findall(head):
        km = _META_KEY_RE.search(tag)
        if not km or km.group(1).strip().lower() not in _HTML_META_DATE_KEYS:
            continue
        cm = _META_CONTENT_RE.search(tag)
        if cm:
            d = _parse_date_str(cm.group(1))
            if d:
                return d
    # 2) JSON-LD 结构化数据（datePublished/dateCreated/uploadDate）
    m = _JSONLD_DATE_RE.search(head)
    if m:
        d = _parse_date_str(m.group(1))
        if d:
            return d
    # 3) <time datetime="..."> 标签
    m = _TIME_TAG_RE.search(head)
    if m:
        d = _parse_date_str(m.group(1))
        if d:
            return d
    return None


def extract_publish_date(item) -> Optional[date]:
    """从 CollectedItem 的元数据/正文/URL 尽力解析发布日期；解析不到返回 None。"""
    meta = getattr(item, "metadata", None) or {}
    for k in _META_DATE_KEYS:
        if k in meta and meta[k]:
            d = _parse_date_str(str(meta[k]))
            if d:
                return d
    # 正文开头（通常含发布时间）与 URL（很多站点 URL 内嵌 /2017/03/ 之类）
    head = (getattr(item, "content", "") or "")[:300]
    d = _parse_date_str(head)
    if d:
        return d
    url = getattr(item, "url", "") or ""
    m = re.search(r"/(20\d{2})[-/](\d{1,2})(?:[-/](\d{1,2}))?", urlsplit(url).path)
    if m:
        return _parse_date_str("-".join(p for p in m.groups() if p))
    return None


def apply_recency(items: List, window: Optional[RecencyWindow]) -> List:
    """按时效窗口过滤+倒序。window 为 None 时原样返回。

    - 有硬窗口(days)时：丢弃"能确信解析到、且早于 cutoff"的条目；无法解析日期的保留。
    - 一律按发布日期倒序（新→旧），无日期的垫后，确保"最新"优先。
    """
    if window is None or not items:
        return items
    cutoff: Optional[date] = None
    if window.days is not None:
        cutoff = date.today() - timedelta(days=window.days)

    kept = []
    for it in items:
        d = extract_publish_date(it)
        if cutoff is not None and d is not None and d < cutoff:
            continue  # 确信过旧 → 丢弃
        kept.append((d, it))

    # 倒序：有日期的按日期新→旧在前，无日期的（None）排最后
    kept.sort(key=lambda pair: (pair[0] is not None, pair[0] or date.min), reverse=True)
    return [it for _, it in kept]
