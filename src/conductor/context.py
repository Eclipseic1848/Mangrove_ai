"""
上下文工程（Phase 3 C）：对话历史压缩。

多轮对话里 history 会持续增长、推高 token。当超过阈值时，把较早的消息用 LLM 摘要成一条，
仅保留最近若干轮，从而把上下文规模稳定在预算内。纯函数式：输入 messages、输出压缩后的 messages。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from src.config.settings import settings
from src.llm import achat

from .prompts import HISTORY_COMPRESS_SYSTEM

logger = logging.getLogger(__name__)

Message = Dict[str, str]


async def compress_history(
    messages: List[Message],
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> List[Message]:
    """对话历史压缩：超过 context_max_messages 时，把较早消息摘要成一条，保留最近 context_keep_recent 条。

    未超阈值则原样返回。摘要失败则退化为"硬截断最近若干条"，不阻断主流程。
    """
    max_n = settings.context_max_messages
    keep = settings.context_keep_recent
    if max_n <= 0 or len(messages) <= max_n:
        return messages

    head, recent = messages[:-keep], messages[-keep:]
    convo = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in head)
    try:
        summary = await achat(
            [
                {"role": "system", "content": HISTORY_COMPRESS_SYSTEM},
                {"role": "user", "content": convo[:8000]},
            ],
            provider=provider,
            model=model,
        )
        summary = (summary or "").strip()
    except Exception:
        logger.warning("对话历史压缩失败，退化为只保留最近消息", exc_info=True)
        return recent

    if not summary:
        return recent
    logger.info("对话历史已压缩：%d 条 → 摘要 + 最近 %d 条", len(messages), keep)
    return [{"role": "system", "content": f"【早前对话摘要】\n{summary}"}] + recent
