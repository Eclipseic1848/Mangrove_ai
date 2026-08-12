"""
VOC 用户声音处理服务

处理懂车帝和汽车之家的主贴 JSON 数据：
1. 内容过滤（filter）：LLM 判断是否值得分析
2. 内容分析（analysis）：按 RAG 映射规则进行 VOC 分析

集成方式：
- 作为 Agent 工具：browser_filter_voc、browser_analyze_voc（在 create_browser_tools 中）
- 作为 MCP 服务器：python -m src.services.voc_processor.main_mcp
- 命令行：python -m src.services.voc_processor.main <input.json>
"""

from .processor import process_voc_file, filter_voc_file, analyze_voc_file

__all__ = ["process_voc_file", "filter_voc_file", "analyze_voc_file"]
