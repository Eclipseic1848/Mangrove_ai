"""
浏览器 Agent LLM 输出 Schema 定义

定义用于规范 LLM 输出的 Pydantic Schema（数据结构规范）。
这些 Schema 用于解析和验证 LLM 返回的结构化 JSON 输出。

说明：
- 与 types/ 文件夹的区别：
  - types/ - 运行时状态类型（TypedDict, Enum, dataclass）
  - schema/ - LLM 输出数据结构规范（Pydantic BaseModel）
"""
from .browser_use_models import (
    BrowserUseAgentOutput,
    TaskCompletionJudgmentOutput,
)

__all__ = [
    "BrowserUseAgentOutput",
    "TaskCompletionJudgmentOutput",
]

