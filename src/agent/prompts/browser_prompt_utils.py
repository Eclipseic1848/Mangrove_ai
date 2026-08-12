"""
浏览器自动化 Agent 提示词工具函数

提供提示词格式化和工具描述的辅助函数。
通过 Pydantic Schema 生成输出格式与工具描述，经占位符注入提示词。
"""
from typing import Optional, List
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.tools import BaseTool

from src.agent.prompts.browser_prompts import (
    BROWSER_TASK_COMPLETION_JUDGE_PROMPT_TEMPLATE,
    BROWSER_USE_SYSTEM_PROMPT_TEMPLATE,
)
from src.agent.schema.browser_use_models import (
    BrowserUseAgentOutput,
    get_browser_tools_format,
    get_task_complete_format,
)


def format_tools_description(tools: Optional[List[BaseTool]]) -> str:
    """
    将工具列表格式化为文本描述
    
    Args:
        tools: 工具列表
        
    Returns:
        格式化的工具描述文本
    """
    if not tools:
        return ""
    
    tool_lines = []
    for tool in tools:
        tool_name = tool.name if hasattr(tool, 'name') else str(tool)
        tool_desc = ""
        
        if hasattr(tool, 'description'):
            tool_desc = tool.description
        elif hasattr(tool, 'func') and hasattr(tool.func, '__doc__'):
            tool_desc = tool.func.__doc__ or ""
        
        # 提取第一行作为简短描述
        if tool_desc:
            first_line = tool_desc.strip().split('\n')[0]
            if first_line:
                tool_desc = first_line
            else:
                tool_desc = ""
        
        # 格式化工具描述
        if tool_desc:
            tool_lines.append(f"- {tool_name}: {tool_desc}")
        else:
            tool_lines.append(f"- {tool_name}")
    
    return "\n".join(tool_lines)


def get_task_completion_judge_prompt_with_format(
    output_parser: Optional[PydanticOutputParser] = None
) -> str:
    """
    获取任务完成判断提示词，包含格式要求
    
    Args:
        output_parser: Pydantic输出解析器，如果提供则添加格式要求
        
    Returns:
        格式化后的提示词
    """
    format_requirement = """输出格式：
---
【任务完成判断】

任务状态：[已完成/未完成/失败]

判断依据：
1. [原始任务要求]
2. [已执行的操作]
3. [缺失的操作]
4. [页面状态]

最终决定：[明确说明完成/未完成/失败的原因]

**最终结果：[如果完成给出结果；否则说明原因和缺失操作]**
---"""
    
    if output_parser:
        format_instructions = output_parser.get_format_instructions()
        format_requirement = f"输出格式要求（必须严格遵守）：\n{format_instructions}"
    
    return BROWSER_TASK_COMPLETION_JUDGE_PROMPT_TEMPLATE.format(
        format_requirement=format_requirement
    )


def get_execute_output_format() -> str:
    """
    从 Pydantic 模型（BrowserUseAgentOutput）生成 Execute 节点输出格式说明。
    用于提示词占位符 {execute_output_format}。
    
    Returns:
        格式说明文本，包含 JSON 结构与工具参数约束
    """
    parser = PydanticOutputParser(pydantic_object=BrowserUseAgentOutput)
    format_instructions = parser.get_format_instructions()
    tool_params = (
        "- **工具参数不可变**：browser_navigate/browser_new_page 用 {{\"url\": \"...\"}}；"
        "browser_click 用 {{\"uid\": \"...\", \"dbl_click\": false}}；"
        "browser_fill 用 {{\"uid\": \"...\", \"value\": \"...\"}}；"
        "browser_select_page 用 {{\"pageId\": 数字}}；"
        "browser_press_key 用 {{\"key\": \"Enter\"}}；"
        "**任务完成时**在根级别填 task_complete: {{\"text\": \"...\", \"success\": true/false}}，并置 action 为 []。"
    )
    return (
        f"{format_instructions}\n"
        "- 任务完成时填 task_complete 且 action 为 []；未完成时 action 至少一项。\n"
        f"- {tool_params}"
    )


_EXTRACT_URL_HINT = (
    "\n  **extract 工具 url 必须为真实 URL**（禁止占位符）："
    "(1) 若当前已是帖子详情页，用 browser_state 的「当前URL」；"
    "(2) 若在搜索/列表页：先从快照中找到可点击的「详情页链接」，优先 browser_click 进入详情页后再 extract；"
    "或直接使用快照中出现的真实详情页 URL 作为 extract 的 url（前提：该 URL 确实是详情页）。"
    "**禁止**使用示例或占位符（如 /ugc/article/xxx、懂车帝帖子URL、汽车之家帖子URL 等），必须使用实际页面或快照中的真实 URL。"
)

_DOWNLOAD_OUTPUT_DEFAULTS_HINT = (
    "\n  **下载/输出路径（固定，不可在工具参数中自定义）**："
    "汽车之家帖子/车家号 JSON → downloads/汽车之家/；"
    "懂车帝帖子/视频页 JSON → downloads/懂车帝/；"
    "抖音视频 → downloads/抖音/；"
    "VOC 分析结果 → analysis/（子目录与 downloads 对齐）；"
    "browser_analyze_voc 的 input_file 须为 extract 产出的原始 JSON，引用结果以工具返回的 output_file 为准。"
)


def get_tools_format_from_tools(tools: Optional[List[BaseTool]]) -> str:
    """
    从实际工具列表生成可用工具描述（用于提示词）。
    优先使用此函数，可展示 chrome_devtools 中的全部工具。
    
    Args:
        tools: 工具列表（来自 create_browser_tools 等）
        
    Returns:
        格式化的工具列表文本，含「**可用工具**」标题
    """
    if not tools:
        return get_browser_tools_format()
    desc = format_tools_description(tools)
    if not desc:
        return get_browser_tools_format()
    result = "**可用工具**（参数不可变，详见各工具描述）：\n" + desc
    # 保留 extract 相关提示与下载/输出路径默认值说明
    result += _EXTRACT_URL_HINT
    result += _DOWNLOAD_OUTPUT_DEFAULTS_HINT
    return result


def get_browser_use_system_prompt(tools: Optional[List[BaseTool]] = None) -> str:
    """
    获取完整的 browser-use 系统提示词。
    通过占位符注入 Pydantic Schema 生成的 {execute_output_format} 与 {tools_format}。
    
    Args:
        tools: 可选。传入工具列表时，将展示所有工具（含 chrome_devtools 全部）；否则使用 schema 硬编码的子集。
    
    Returns:
        格式化后的系统提示词
    """
    return BROWSER_USE_SYSTEM_PROMPT_TEMPLATE.format(
        execute_output_format=get_execute_output_format(),
        tools_format=get_tools_format_from_tools(tools),
        task_complete_format=get_task_complete_format(),
    )
