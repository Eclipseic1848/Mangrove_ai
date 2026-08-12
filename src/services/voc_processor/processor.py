#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VOC 用户声音处理核心逻辑

提供 process_voc_file 函数，供 Agent 工具和 MCP 服务器调用。
"""

import os
from typing import Any, Dict, Optional

from .filter_json import process_single_json as filter_json
from .llm.voc_analysis_llm import load_rag_map, build_chain
from .analysis_json import process_single_json_file as analysis_json


# 全局变量：缓存 chain 和 labels_info
_cached_chain = None
_cached_labels_info: Optional[Dict[str, Any]] = None


def get_analysis_chain():
    """获取或构建分析链（带缓存）"""
    global _cached_chain, _cached_labels_info

    if _cached_chain is None or _cached_labels_info is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        rag_map_path = os.path.join(script_dir, "llm", "rag_map.json")

        labels_info = load_rag_map(rag_map_path)
        if not labels_info:
            raise RuntimeError("无法加载RAG映射规则")

        chain = build_chain(labels_info)
        _cached_chain = chain
        _cached_labels_info = labels_info

    return _cached_chain, _cached_labels_info


def should_analyze(input_file: str) -> bool:
    """
    判断是否需要进行内容分析

    Args:
        input_file: 输入JSON文件路径

    Returns:
        bool: True表示需要分析，False表示跳过分析
    """
    try:
        filter_result = filter_json(input_file)

        if filter_result is None:
            return False

        filter_result_value = getattr(filter_result, "result", None)
        return filter_result_value == "是"

    except Exception as e:
        raise RuntimeError(f"内容过滤失败: {e}") from e


def filter_voc_file(input_file: str) -> dict:
    """
    仅对 VOC JSON 进行内容筛选，返回是否需要进一步分析。

    Args:
        input_file: 输入JSON文件路径

    Returns:
        dict: 包含 success、message、input_file、need_analysis、filter_result
    """
    input_file = os.path.abspath(input_file)
    try:
        filter_result = filter_json(input_file)
        if filter_result is None:
            return {
                "success": True,
                "message": "内容过滤未通过，清洗后的文本为空或无有效内容，建议不进行后续分析",
                "input_file": input_file,
                "need_analysis": False,
                "filter_result": None,
            }
        result_value = getattr(filter_result, "result", None)
        analysis_text = getattr(filter_result, "analysis", None)
        need_analysis = result_value == "是"
        return {
            "success": True,
            "message": "内容过滤未通过，筛选完成" if not need_analysis else "筛选完成",
            "input_file": input_file,
            "need_analysis": need_analysis,
            "filter_result": {
                "result": result_value,
                "analysis": analysis_text,
            },
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"筛选失败: {e}",
            "input_file": input_file,
            "need_analysis": False,
            "filter_result": None,
        }


def analyze_voc_file(input_file: str, output_file: Optional[str] = None) -> dict:
    """
    对已通过筛选的 VOC JSON 进行内容解析与标签提取。

    Args:
        input_file: 输入JSON文件路径
        output_file: 输出JSON文件路径（可选，默认使用输入文件名加_analyzed后缀）

    Returns:
        dict: 包含 success、message、input_file、output_file
    """
    input_file = os.path.abspath(input_file)
    if not output_file:
        base_name = os.path.splitext(input_file)[0]
        ext = os.path.splitext(input_file)[1]
        output_file = f"{base_name}_analyzed{ext}"
    output_file = os.path.abspath(output_file)
    try:
        chain, labels_info = get_analysis_chain()
        success = analysis_json(input_file, output_file, chain, labels_info)
        if success:
            return {
                "success": True,
                "message": "解析完成",
                "input_file": input_file,
                "output_file": output_file,
            }
        return {
            "success": False,
            "message": "解析处理失败",
            "input_file": input_file,
            "output_file": None,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"解析步骤失败: {e}",
            "input_file": input_file,
            "output_file": None,
        }


def process_voc_file(input_file: str, output_file: Optional[str] = None) -> dict:
    """
    处理VOC文件的主函数

    Args:
        input_file: 输入JSON文件路径
        output_file: 输出JSON文件路径（可选，默认使用输入文件名加_analyzed后缀）

    Returns:
        dict: 处理结果，包含 success、message、input_file、output_file
    """
    input_file = os.path.abspath(input_file)

    if not output_file:
        base_name = os.path.splitext(input_file)[0]
        ext = os.path.splitext(input_file)[1]
        output_file = f"{base_name}_analyzed{ext}"

    output_file = os.path.abspath(output_file)

    if not should_analyze(input_file):
        return {
            "success": False,
            "message": "内容过滤未通过，跳过分析步骤",
            "input_file": input_file,
            "output_file": None,
        }

    try:
        chain, labels_info = get_analysis_chain()
        success = analysis_json(input_file, output_file, chain, labels_info)

        if success:
            return {
                "success": True,
                "message": "处理完成",
                "input_file": input_file,
                "output_file": output_file,
            }
        else:
            return {
                "success": False,
                "message": "分析处理失败",
                "input_file": input_file,
                "output_file": None,
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"分析步骤失败: {e}",
            "input_file": input_file,
            "output_file": None,
        }
