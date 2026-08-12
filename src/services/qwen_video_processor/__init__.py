"""
Qwen 视频文字提取服务

调用 src/services/qwen_video 能力，从视频画面中提取文字（字幕、标牌、界面文字等），
通过 MCP 协议使用 Qwen3.6 视频模型。

集成方式：
- 作为 Agent 工具：browser_analyze_video（在 create_browser_tools 中）
"""

from .processor import analyze_video_file

__all__ = ["analyze_video_file"]
