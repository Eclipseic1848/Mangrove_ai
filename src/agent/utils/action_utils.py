"""
操作类型识别工具函数

提供从工具名称中提取操作类型等功能。
"""
from typing import Optional, List

# 工具名称前缀（用于自动提取操作类型）
TOOL_NAME_PREFIX = "browser_"


def extract_action_type_from_tool_name(tool_name: str) -> Optional[str]:
    """
    从工具名称中提取操作类型（通用方法）
    
    工具名称格式：browser_<action_type>
    例如：browser_navigate -> navigate
         browser_fill_form -> fill_form
    
    Args:
        tool_name: 工具名称（如 "browser_navigate"）
        
    Returns:
        操作类型（如 "navigate"），如果无法提取则返回 None
    """
    if not tool_name:
        return None
    
    # 移除前缀
    if tool_name.startswith(TOOL_NAME_PREFIX):
        action_type = tool_name[len(TOOL_NAME_PREFIX):]
        return action_type
    
    return None


def matches_action_type(tool_name_or_action: str, action_types: List[str]) -> bool:
    """
    检查工具名称或操作类型是否匹配给定的操作类型列表
    
    支持两种格式：
    1. 完整工具名称：browser_fill, browser_click 等
    2. 操作类型：fill, click 等
    
    Args:
        tool_name_or_action: 工具名称（如 "browser_fill"）或操作类型（如 "fill"）
        action_types: 操作类型列表（如 ["fill", "fill_form", "click"]）
        
    Returns:
        如果匹配则返回 True，否则返回 False
        
    示例:
        matches_action_type("browser_fill", ["fill", "click"]) -> True
        matches_action_type("fill", ["fill", "click"]) -> True
        matches_action_type("browser_snapshot", ["snapshot"]) -> True
    """
    if not tool_name_or_action:
        return False
    
    # 如果已经是操作类型格式（不包含 browser_ 前缀），直接检查
    if tool_name_or_action in action_types:
        return True
    
    # 如果是完整工具名称，提取操作类型后检查
    extracted = extract_action_type_from_tool_name(tool_name_or_action)
    if extracted and extracted in action_types:
        return True
    
    return False
