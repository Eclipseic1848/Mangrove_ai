"""采集器共用辅助：目标 URL 构造、HTML 转文本。"""
from __future__ import annotations

from html.parser import HTMLParser
import shutil
import sys
from typing import List

from src.conductor.task_spec import TaskSpec


_CAPTCHA_SIGNALS = (
    "验证码", "安全验证", "人机验证", "访问异常", "访问受限", "请开启JavaScript",
    "请启用JavaScript", "系统繁忙", "拒绝访问", "Access Denied", "403 Forbidden",
    "404 Not Found", "Just a moment", "Checking your browser", "点击继续访问",
    "异常流量", "滑动验证",
)
_CAPTCHA_MAX_LEN = 400


def is_challenge_page(text: str) -> bool:
    """短验证页不算正文；保留长文提及验证码的既有判定边界。"""
    return len(text) <= _CAPTCHA_MAX_LEN and any(signal in text for signal in _CAPTCHA_SIGNALS)


def crawler_browser_config():
    """无桌面的 Linux 复用已安装的 Chrome，避免缺少下载内核导致启动失败。"""
    from crawl4ai import BrowserConfig

    if sys.platform.startswith("linux") and shutil.which("google-chrome"):
        return BrowserConfig(chrome_channel="chrome")
    return BrowserConfig()


def build_target_urls(spec: TaskSpec) -> List[str]:
    """
    根据 TaskSpec 推导出要采集的 URL 列表（仅处理用户直接给出的 URL）。

    关键词型任务（无 URL）由 SearchDiscoveryCollector（tier 25）统一负责：
    先用真实搜索发现链接、再抓取解析，故这里不再退化为搜索引擎结果页。
    crawl4ai/scrapling/simple_http 因此只服务"显式 URL"任务。
    """
    return list(spec.urls) if spec.urls else []


class _TextExtractor(HTMLParser):
    """极简 HTML→文本：丢弃 script/style，保留可见文字与 <title>。"""

    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._in_title = False
        self.title = ""
        self._chunks: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        self._chunks.append(text)

    def get_text(self) -> str:
        # 折叠多余空行
        lines = [c for c in (s.strip() for s in self._chunks) if c]
        return "\n".join(lines)


def html_to_text(html: str) -> tuple[str, str]:
    """返回 (title, text)。解析失败时退化为原文。"""
    try:
        parser = _TextExtractor()
        parser.feed(html)
        return parser.title, parser.get_text()
    except Exception:
        return "", html
