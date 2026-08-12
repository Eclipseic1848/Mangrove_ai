"""
任务处理工具函数

提供任务关键词提取、匹配等功能，用于任务分析和页面状态验证。
"""
import re
from typing import List, Tuple


def extract_task_keywords(user_task: str) -> List[str]:
    """
    从任务描述中提取关键词，用于页面元素匹配
    
    功能：
    - 提取引号中的内容
    - 提取点击操作后的对象
    - 提取常见的操作对象模式（搜索、输入、填写等）
    
    Args:
        user_task: 用户任务描述
    
    Returns:
        提取的关键词列表（已去重）
    """
    keywords = []

    # 提取引号中的内容
    quoted_matches = re.findall(r'[""''"]([^""''"]+)[""''"]', user_task)
    keywords.extend(quoted_matches)

    # 提取点击操作后的对象
    click_matches = re.findall(r'点击[（(]?([^）)]+)[）)]?', user_task)
    keywords.extend(click_matches)

    # 提取常见的操作对象模式
    # 改进：更健壮的模式匹配，处理各种情况
    # 1. 搜索/输入/填写 + 可选空格 + 可选引号 + 关键词（直到遇到标点符号、连接词）
    # 2. 支持中英文引号
    # 3. 处理"搜索"后面有空格的情况
    common_patterns = [
        # 模式1：搜索 + 可选空格 + 可选引号 + 关键词（直到遇到标点符号、连接词）
        r'搜索\s*["""'']?([^，。、"""''并和及]+)',
        r'输入\s*["""'']?([^，。、"""''并和及]+)',
        r'填写\s*["""'']?([^，。、"""''并和及]+)',
    ]

    for pattern in common_patterns:
        matches = re.findall(pattern, user_task)
        keywords.extend(matches)

    # 去重并过滤空字符串
    keywords = list(set(filter(None, keywords)))

    return keywords


def match_keywords_in_snapshot(
    keywords: List[str], 
    snapshot_text: str
) -> Tuple[List[str], List[str]]:
    """
    在快照文本中匹配关键词
    
    功能：
    - 在页面快照文本中查找关键词
    - 区分找到和未找到的关键词
    
    Args:
        keywords: 关键词列表
        snapshot_text: 快照文本
        
    Returns:
        (找到的关键词列表, 未找到的关键词列表) 的元组
    """
    found_keywords = []
    missing_keywords = []
    snapshot_lower = snapshot_text.lower()
    
    for keyword in keywords:
        if not keyword or not keyword.strip():
            continue
        
        keyword_lower = keyword.lower()
        if keyword_lower in snapshot_lower or keyword in snapshot_text:
            found_keywords.append(keyword)
        else:
            missing_keywords.append(keyword)
    
    return found_keywords, missing_keywords
