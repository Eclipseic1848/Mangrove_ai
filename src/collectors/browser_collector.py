"""
浏览器 Agent 兜底采集器（Tier 末位，交互兜底）。

复用现有 src/agent/browser_agent.py（LangGraph + chrome-devtools-mcp）。
用于登录态 / 复杂 JS 交互 / 前述引擎都失败的页面：让浏览器 Agent 打开 URL 并提取正文。

仅当 chrome-devtools-mcp 构建产物存在时可用（见 settings.CHROME_DEVTOOLS_MCP_INDEX_JS），
否则路由自动跳过。仅处理用户直接给出的 URL（兜底场景），不做关键词全网发现。
"""
from __future__ import annotations

import logging
import shutil

from src.conductor.task_spec import TaskSpec

from .base import BaseCollector, CollectedItem, CollectResult
from ._net import get_rate_limiter
from .registry import register

logger = logging.getLogger(__name__)

try:
    from src.config.settings import CHROME_DEVTOOLS_MCP_INDEX_JS
    from src.agent.browser_agent import BrowserAgent  # 重型依赖，导入失败则不可用

    _BROWSER_OK = True
except Exception:
    BrowserAgent = None  # type: ignore
    CHROME_DEVTOOLS_MCP_INDEX_JS = None  # type: ignore
    _BROWSER_OK = False


def _extract_answer(result) -> str:
    """从浏览器 Agent 返回的结果字典里尽力取出文本答案。"""
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result or "")
    for key in ("final_result", "output", "result", "final_answer", "answer", "content"):
        v = result.get(key)
        if isinstance(v, str) and v.strip():
            return v
    # 退化为最后一条消息内容
    msgs = result.get("messages")
    if isinstance(msgs, list) and msgs:
        last = msgs[-1]
        content = getattr(last, "content", None) or (last.get("content") if isinstance(last, dict) else None)
        if isinstance(content, str):
            return content
    return ""


_TASK_TMPL = (
    "打开网址 {url}，提取该页面的主要正文内容（标题与正文文字），"
    "去除导航、广告、页脚等无关内容，直接以纯文本返回提取到的正文。"
)


class BrowserCollector(BaseCollector):
    name = "browser"
    tier = 99  # 交互兜底，最后才用

    def is_available(self) -> bool:
        if not _BROWSER_OK:
            return False
        # 需 Node.js 运行 chrome-devtools-mcp；缺失则提前判不可用，避免被选中后才失败
        if shutil.which("node") is None:
            return False
        try:
            return CHROME_DEVTOOLS_MCP_INDEX_JS is not None and CHROME_DEVTOOLS_MCP_INDEX_JS.exists()
        except Exception:
            return False

    def matches(self, spec: TaskSpec) -> bool:
        # 兜底只处理具体 URL（登录态/复杂交互页），不做关键词发现
        return bool(spec.urls)

    async def collect(self, spec: TaskSpec) -> CollectResult:
        if not self.is_available():
            return CollectResult(False, self.name, message="浏览器 Agent 不可用（chrome-devtools-mcp 未构建）")

        urls = list(spec.urls)[: max(1, min(spec.max_items, 3))]  # 浏览器较慢，限量
        items: list[CollectedItem] = []
        agent = None
        try:
            agent = BrowserAgent(verbose=False)
            for url in urls:
                # 与其它采集器一致：同域名最小间隔控速，逐页之间留出真人式停顿
                await get_rate_limiter().acquire(url)
                try:
                    result = await agent.run_async(_TASK_TMPL.format(url=url))
                except Exception as e:
                    logger.warning("browser 采集失败 %s: %s", url, e)
                    continue
                text = _extract_answer(result).strip()
                if text:
                    items.append(
                        CollectedItem(url=url, title="", content=text, metadata={"engine": self.name})
                    )
        except Exception as e:
            return CollectResult(False, self.name, message=f"浏览器 Agent 运行异常: {e}")
        finally:
            if agent is not None:
                try:
                    agent.stop()
                except Exception:
                    pass

        if not items:
            return CollectResult(False, self.name, message="浏览器 Agent 未取得内容")
        return CollectResult(True, self.name, items=items, message=f"采集 {len(items)} 个页面")


register(BrowserCollector())
