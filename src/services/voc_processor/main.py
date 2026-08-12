#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VOC用户声音处理主程序

工作流程：
1. 先调用 filter_json 进行内容过滤
2. 如果 LLM 结果是"是"，则继续进行 analysis_json 分析
3. 如果结果是"否"或其他，则直接结束

使用方法：
    python main.py <input_json_file> [output_json_file]

示例：
    python main.py input.json
    python main.py input.json output.json
"""

import os
import sys
from typing import Any, Dict, Optional, Tuple

from .filter_json import process_single_json as filter_json
from .llm.voc_analysis_llm import load_rag_map, build_chain
from .analysis_json import process_single_json_file as analysis_json


def parse_arguments():
    """解析命令行参数"""
    if len(sys.argv) < 2:
        print("使用方法: python main.py <input_json_file> [output_json_file]")
        print("示例: python main.py input.json output.json")
        sys.exit(1)
    
    input_file = os.path.abspath(sys.argv[1])
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    # 如果没有指定输出文件，使用输入文件名加_analyzed后缀
    if not output_file:
        base_name = os.path.splitext(input_file)[0]
        ext = os.path.splitext(input_file)[1]
        output_file = f"{base_name}_analyzed{ext}"
    
    output_file = os.path.abspath(output_file)
    return input_file, output_file


# 全局变量：缓存chain和labels_info（避免重复构建）
_cached_chain = None
_cached_labels_info: Optional[Dict[str, Any]] = None


def get_analysis_chain() -> Tuple[Any, Dict[str, Any]]:
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
        
        # 判断过滤结果是否为"是"
        filter_result_value = getattr(filter_result, "result", None)
        return filter_result_value == "是"
        
    except Exception as e:
        raise RuntimeError(f"内容过滤失败: {e}") from e


def run_analysis_step(input_file: str, output_file: str) -> bool:
    """
    执行内容分析步骤
    
    Args:
        input_file: 输入JSON文件路径
        output_file: 输出JSON文件路径
        
    Returns:
        bool: 是否成功
    """
    print("\n" + "=" * 60)
    print("【内容分析】")
    print("=" * 60)

    # 构建/获取缓存的分析链
    print("🔧 正在构建分析链...")
    chain, labels_info = get_analysis_chain()

    # 处理单个JSON文件
    return analysis_json(input_file, output_file, chain, labels_info)


def main():
    """主函数"""
    # 解析命令行参数
    input_file, output_file = parse_arguments()
    
    # 打印文件信息
    print("=" * 60)
    print("VOC用户声音处理系统")
    print("=" * 60)
    print(f"📄 输入文件: {input_file}")
    print(f"📄 输出文件: {output_file}")
    
    # 判断是否需要进行分析
    try:
        if not should_analyze(input_file):
            print("\n⚠️  内容过滤未通过，跳过分析步骤")
            sys.exit(0)
    except Exception as e:
        print(f"❌ 内容过滤失败: {e}")
        sys.exit(1)
    
    # 执行内容分析
    try:
        success = run_analysis_step(input_file, output_file)
        
        if success:
            print("\n" + "=" * 60)
            print("✅ 处理完成！")
            print("=" * 60)
        else:
            print("\n" + "=" * 60)
            print("❌ 处理失败！")
            print("=" * 60)
            sys.exit(1)
            
    except Exception as e:
        print(f"\n❌ 分析步骤失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
