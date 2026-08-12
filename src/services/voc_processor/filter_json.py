#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
JSON内容过滤模块

功能：
- 读取JSON文件并提取内容
- 清洗文本内容（移除图片标签等）
- 调用LLM进行内容有效性判断

使用方法：
    python filter_json.py <json_file_path>
"""

import json
import re
import sys
from pathlib import Path

from .llm.voc_filter_llm import VocFilterLLM


# ============================================================================
# 文本清洗工具函数
# ============================================================================

def remove_image_url(text: str) -> str:
    """
    移除文本中的图片标签
    
    Args:
        text: 原始文本
        
    Returns:
        str: 移除图片标签后的文本
    """
    return re.sub(r'<img\b[^>]*>', '', text, flags=re.I)


def load_json_data(json_path: str) -> dict:
    """
    加载JSON文件数据
    
    Args:
        json_path: JSON文件路径
        
    Returns:
        dict: JSON数据
        
    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON格式错误
    """
    json_file = Path(json_path)
    if not json_file.exists():
        raise FileNotFoundError(f"JSON 文件不存在: {json_file}")
    
    with open(json_file, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_post_data(data: dict, filename: str) -> dict:
    """
    从JSON数据中提取post/topic数据
    
    Args:
        data: JSON数据字典
        filename: 文件名（用于错误提示）
        
    Returns:
        dict: post/topic数据（可能是嵌套的或顶层数据）
        
    Raises:
        KeyError: 缺少必要字段
    """
    # 兼容三种结构：
    # 1. data["post"] - 懂车帝格式
    # 2. data["topic"] - 汽车之家嵌套格式
    # 3. 顶层数据 - 汽车之家扁平格式（直接在顶层有content/summary等字段）
    post = None
    if "post" in data:
        post = data["post"]
    elif "topic" in data:
        post = data["topic"]
    elif "content" in data or "summary" in data:
        # 顶层格式：数据直接在顶层
        post = data
    else:
        raise KeyError(f"{filename} 中没有找到 'post'、'topic' 或顶层内容字段")
    
    return post


def extract_summary_text(post: dict, filename: str) -> str:
    """
    从post/topic数据中提取summary或content文本
    
    Args:
        post: post/topic数据字典
        filename: 文件名（用于错误提示）
        
    Returns:
        str: summary或content文本
        
    Raises:
        KeyError: 缺少必要字段
    """
    # 优先使用summary，如果没有则使用content
    if "summary" in post:
        return post["summary"]
    elif "content" in post:
        return post["content"]
    else:
        raise KeyError(f"{filename} 的 post/topic 中没有找到 'summary' 或 'content' 字段")


def clean_summary(summary: str, club_bbs_name: str = "") -> str:
    """
    清洗summary文本
    
    Args:
        summary: 原始summary文本
        club_bbs_name: 论坛名称（可选）
        
    Returns:
        str: 清洗后的文本
    """
    # 移除图片标签
    cleaned = remove_image_url(summary)
    
    # 如果有论坛名称，在前面添加
    if club_bbs_name:
        cleaned = f"{club_bbs_name}:{cleaned}"
    
    return cleaned


def process_single_json(json_path: str):
    """
    处理单个JSON文件，进行内容过滤
    
    流程：
    1. 读取JSON文件
    2. 提取post/topic数据
    3. 清洗summary文本
    4. 调用LLM进行过滤判断
    
    Args:
        json_path: JSON文件路径
        
    Returns:
        VocFilterInfo: LLM过滤结果对象，包含analysis和result字段
        None: 如果清洗后文本为空
        
    Raises:
        FileNotFoundError: 文件不存在
        KeyError: 缺少必要字段
    """
    # 1. 加载JSON数据
    data = load_json_data(json_path)
    
    # 2. 提取post/topic数据
    json_file = Path(json_path)
    post = extract_post_data(data, json_file.name)
    
    # 3. 获取原始数据（兼容summary和content字段）
    original_summary = extract_summary_text(post, json_file.name)
    club_bbs_name = post.get("club_bbs_name", "")
    
    # 4. 清洗文本
    cleaned_summary = clean_summary(original_summary, club_bbs_name)
    
    # 5. 检查清洗后是否为空
    if not cleaned_summary.strip():
        return None
    
    # 6. 调用LLM进行过滤
    voc_filter = VocFilterLLM(model_source="vllm")
    result = voc_filter.get_response(cleaned_summary)
    
    return result


if __name__ == "__main__":
    """命令行入口"""
    if len(sys.argv) < 2:
        print("用法: python filter_json.py <json_file_path>")
        sys.exit(1)
    
    json_path = sys.argv[1]
    
    try:
        result = process_single_json(json_path)
        
        if result is None:
            print("❌ 清洗后的 summary 为空或未能得到有效结果")
        else:
            print("=" * 60)
            print("📝 LLM 分析:", getattr(result, "analysis", None))
            print("✅ LLM 结果:", getattr(result, "result", None))
            print("=" * 60)
            
    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)

