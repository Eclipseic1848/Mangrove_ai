"""
正文提取级联工具：多级回退，从 HTML 中提取干净正文。

级联策略：Trafilatura (F1=0.958, Markdown输出) → readability-lxml (F1=0.922)
→ 现有 html_to_text（正则兜底）。安装任一增强库即可自动提升提取质量，
均未安装时退回到零依赖的 html_to_text，不影响运行。

安装：pip install trafilatura readability-lxml
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from src.config.settings import settings

from ._common import html_to_text
from ._recency import extract_date_from_html

logger = logging.getLogger(__name__)

_Result = Tuple[str, str, Dict[str, Any]]


def _longer(a: Optional[_Result], b: _Result) -> _Result:
    """两个提取结果取正文更长的一个（用于三级都未达阈值时的兜底）。"""
    if a is None or len(b[1]) > len(a[1]):
        return b
    return a

# ── 可选依赖探测 ──
try:
    import trafilatura  # type: ignore

    _TRAFILATURA_OK = True
except Exception:
    trafilatura = None  # type: ignore
    _TRAFILATURA_OK = False

try:
    from readability import Document  # type: ignore

    _READABILITY_OK = True
except Exception:
    Document = None  # type: ignore
    _READABILITY_OK = False


def is_trafilatura_available() -> bool:
    return _TRAFILATURA_OK


def is_readability_available() -> bool:
    return _READABILITY_OK


def extract_content(html: str, url: str = "") -> Tuple[str, str, Dict[str, Any]]:
    """从 HTML 中提取 (title, text, metadata)。

    级联：Trafilatura → readability-lxml → html_to_text，命中"足够长"（>= extract_min_chars）
    即停；三级都不够长（如 JS 空壳页只抓到导航栏文字）则不盲目认输，取三级中最长的结果兜底，
    避免"提取到几十字垃圾就停"把后续分析喂成残渣。
    metadata["via"] 标注实际使用的提取器，metadata 含 date/author 等结构化字段。
    """
    if not html or not html.strip():
        return "", "", {"via": "empty"}

    min_chars = settings.extract_min_chars
    best: Optional[_Result] = None

    # ── 1) Trafilatura（基准冠军 F1=0.958，输出 Markdown）──
    if _TRAFILATURA_OK:
        try:
            text = trafilatura.extract(
                html,
                url=url or None,
                output_format="markdown",
                with_metadata=True,
            )
            if text and text.strip():
                meta = _trafilatura_metadata(html, url)
                title = meta.pop("title", "")
                meta["via"] = "trafilatura"
                result = (title, text.strip(), _with_date(meta, html))
                if len(result[1]) >= min_chars:
                    return result
                best = _longer(best, result)
        except Exception:
            logger.debug("Trafilatura 提取异常，回退", exc_info=True)

    # ── 2) readability-lxml（F1=0.922，零配置）──
    if _READABILITY_OK:
        try:
            doc = Document(html)
            summary_html = doc.summary()
            title = doc.title() or ""
            if summary_html and summary_html.strip():
                _, text = html_to_text(summary_html)
                if text and text.strip():
                    result = (title, text.strip(), _with_date({"via": "readability", "title": title}, html))
                    if len(result[1]) >= min_chars:
                        return result
                    best = _longer(best, result)
        except Exception:
            logger.debug("readability-lxml 提取异常，回退", exc_info=True)

    # ── 3) 现有正则兜底（零依赖，恒可用）── 末级恒返回；若前面级数更长，取更长的那个
    title, text = html_to_text(html)
    result = (title, text, _with_date({"via": "html_to_text"}, html))
    return _longer(best, result)


def _with_date(meta: Dict[str, Any], html: str) -> Dict[str, Any]:
    """提取器没给出日期时，从原始 HTML 的 meta/JSON-LD/<time> 补提发布日期（P1-3）。

    正文提取会剥掉这些标签，必须趁 HTML 还在手里时捕获；下游时效过滤
    （extract_publish_date）按 metadata["date"] 键直接命中。
    """
    if not meta.get("date"):
        d = extract_date_from_html(html)
        if d:
            meta["date"] = d.isoformat()
    return meta


def _trafilatura_metadata(html: str, url: str = "") -> Dict[str, Any]:
    """用 Trafilatura 提取结构化元数据（date/author/language 等）。"""
    try:
        md = trafilatura.extract_metadata(html, url=url or None) or {}
    except Exception:
        return {}
    out: Dict[str, Any] = {}
    for src_key, dst_key in [
        ("title", "title"),
        ("date", "date"),
        ("author", "author"),
        ("language", "language"),
        ("source", "source"),
        ("categories", "categories"),
        ("tags", "tags"),
        ("license", "license"),
    ]:
        val = md.get(src_key)
        if val:
            out[dst_key] = val
    return out
