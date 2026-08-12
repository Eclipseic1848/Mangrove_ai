#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VOC用户声音分析模块 - 文件处理

功能：
- 读取和解析JSON文件
- 提取文本内容和元数据
- 调用LLM进行分析
- 保存分析结果

使用方法：
    python analysis_json.py <input_json_file> [output_json_file]

示例：
    python analysis_json.py input.json
    python analysis_json.py input.json output.json
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, Any

from .llm.voc_analysis_llm import (
    load_rag_map,
    build_chain,
    validate_hierarchy
)


# ============================================================================
# 工具函数
# ============================================================================

def parse_datetime(dt_str: str) -> str:
    """
    解析日期时间字符串，转换为YYYY年MM月DD日格式
    
    Args:
        dt_str: 日期时间字符串
        
    Returns:
        str: 格式化后的日期字符串，失败返回"无"
    """
    if not dt_str:
        return "无"
    
    try:
        # 尝试多种日期格式
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d"
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(str(dt_str), fmt)
                return dt.strftime("%Y年%m月%d日")
            except ValueError:
                continue
        
        # 如果都失败，尝试直接提取日期部分
        dt_str_clean = str(dt_str).strip()
        if len(dt_str_clean) >= 10:
            year = dt_str_clean[:4]
            month = dt_str_clean[5:7] if len(dt_str_clean) > 7 else "01"
            day = dt_str_clean[8:10] if len(dt_str_clean) > 10 else "01"
            return f"{year}年{month}月{day}日"
        
        return "无"
    except Exception as e:
        print(f"⚠️  日期解析错误: {e}, 原始值: {dt_str}")
        return "无"


def extract_vehicle_type(club_bbs_name: str) -> str:
    """
    从club_bbs_name提取车辆类型
    
    Args:
        club_bbs_name: 论坛名称
        
    Returns:
        str: 车辆类型，失败返回"无"
    """
    if not club_bbs_name:
        return "无"
    
    # 移除"论坛"等后缀
    vehicle_type = club_bbs_name.replace("论坛", "").strip()
    return vehicle_type if vehicle_type else "无"


# ============================================================================
# 主要处理函数
# ============================================================================

def process_single_json_file(
    input_file_path: str, 
    output_file_path: str, 
    chain, 
    labels_info: Dict[str, Any]
) -> bool:
    """
    处理单个JSON文件，进行用户声音分析
    
    Args:
        input_file_path: 输入JSON文件路径
        output_file_path: 输出JSON文件路径
        chain: langchain chain对象
        labels_info: RAG标签信息字典
        
    Returns:
        bool: 是否处理成功
    """
    # 1. 读取输入文件
    if not os.path.exists(input_file_path):
        print(f"❌ 错误: 输入文件不存在: {input_file_path}")
        return False
    
    try:
        with open(input_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ 错误: 读取文件失败 {input_file_path}: {e}")
        return False
    
    # 2. 提取topic/post数据（兼容三种格式）
    topic_data = None
    if 'topic' in data and isinstance(data['topic'], dict):
        topic_data = data['topic']
    elif 'post' in data and isinstance(data['post'], dict):
        topic_data = data['post']
    elif 'content' in data or 'summary' in data:
        # 顶层格式：数据直接在顶层
        topic_data = data
    else:
        print(f"❌ 错误: 文件缺少topic、post或顶层内容字段")
        return False
    
    # 3. 提取文本内容
    review = ""
    title = ""
    
    # 提取标题（优先从topic_data，如果没有则从顶层data）
    if 'title' in topic_data and topic_data['title']:
        title = topic_data['title']
    elif 'title' in data and data['title']:
        title = data['title']
    elif ('articleInfo' in topic_data and 
          isinstance(topic_data['articleInfo'], dict) and 
          'title' in topic_data['articleInfo']):
        title = topic_data['articleInfo']['title']
    
    # 提取内容（优先从topic_data，如果没有则从顶层data）
    if 'summary' in topic_data and topic_data['summary']:
        review = topic_data['summary']
    elif 'content' in topic_data and topic_data['content']:
        review = topic_data['content']
    elif 'summary' in data and data['summary']:
        review = data['summary']
    elif 'content' in data and data['content']:
        review = data['content']
    
    # 组合标题和内容
    if title and review:
        review = title + "\n" + review
    elif title and not review:
        review = title
    
    if not review:
        print(f"❌ 错误: post/topic无内容")
        return False
    
    # 4. 提取元数据（兼容不同格式）
    # source可能在顶层data中，也可能在topic_data中
    source = data.get('source', topic_data.get('source', '无'))
    
    # publish_time可能有多种字段名
    publish_time = (topic_data.get('publishTime', '') or 
                   topic_data.get('publish_time', '') or
                   topic_data.get('date', '') or
                   topic_data.get('timeRaw', ''))
    info_time = parse_datetime(publish_time)
    
    # club_bbs_name可能有多种字段名
    club_bbs_name = (topic_data.get('club_bbs_name', '') or
                    topic_data.get('publishedTo', '') or
                    topic_data.get('published_to', ''))
    vehicle_type = extract_vehicle_type(club_bbs_name)
    
    # 5. 调用模型进行分析
    print("🤖 正在调用模型进行分析...")
    try:
        analysis_results = chain.invoke({
            "topic_content": review,
            "source": source,
            "publish_time": info_time,
            "club_bbs_name": club_bbs_name
        })
        print(f"✓ 模型解析成功，获得 {len(analysis_results.results)} 个结果")
    except Exception as e:
        print(f"❌ 错误: 模型调用失败: {e}")
        return False
    
    # 6. 验证并修正每条结果的层级关系
    validated_results = []
    for result in analysis_results.results:
        parsed_data = result.model_dump()
        
        # 如果模型没有提取到相关信息，使用原始数据
        if parsed_data.get("information_source") == "无" and source != "无":
            parsed_data["information_source"] = source
        if parsed_data.get("information_time") == "无" and info_time != "无":
            parsed_data["information_time"] = info_time
        if vehicle_type != "无":
            parsed_data["vehicle_type"] = vehicle_type
        
        # 验证层级关系（调用llm模块的函数）
        validated_data = validate_hierarchy(parsed_data, labels_info['层级映射'])
        validated_results.append(validated_data)
    
    # 7. 将分析结果添加到原始json中
    data['analysis_results'] = validated_results
    data['analysis_timestamp'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    
    # 8. 保存到输出文件
    try:
        output_dir = os.path.dirname(output_file_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        
        print(f"✓ 分析结果已保存到: {output_file_path}")
        print(f"✓ 总解析结果数: {len(validated_results)}")
        return True
    except Exception as e:
        print(f"❌ 错误: 保存文件失败: {e}")
        return False


# ============================================================================
# 主函数（用于独立运行）
# ============================================================================

def main():
    """主函数（用于独立运行此脚本）"""
    # 检查命令行参数
    if len(sys.argv) < 2:
        print("使用方法: python analysis_json.py <input_json_file> [output_json_file]")
        print("示例: python analysis_json.py input.json output.json")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    # 如果没有指定输出文件，使用输入文件名加_analyzed后缀
    if not output_file:
        base_name = os.path.splitext(input_file)[0]
        ext = os.path.splitext(input_file)[1]
        output_file = f"{base_name}_analyzed{ext}"
    
    # 转换为绝对路径
    input_file = os.path.abspath(input_file)
    output_file = os.path.abspath(output_file)
    
    print("=" * 60)
    print("VOC用户声音分析系统")
    print("=" * 60)
    print(f"📄 输入文件: {input_file}")
    print(f"📄 输出文件: {output_file}")
    print("=" * 60)
    
    # 1. 读取RAG映射规则
    script_dir = os.path.dirname(os.path.abspath(__file__))
    rag_map_path = os.path.join(script_dir, 'llm', 'rag_map.json')
    
    labels_info = load_rag_map(rag_map_path)
    if not labels_info:
        print("❌ 错误: 无法加载RAG映射规则")
        sys.exit(1)
    
    # 2. 构建langchain chain
    print("🔧 正在构建分析链...")
    chain = build_chain(labels_info)
    
    # 3. 处理单个JSON文件
    print("=" * 60)
    success = process_single_json_file(input_file, output_file, chain, labels_info)
    
    if success:
        print("\n" + "=" * 60)
        print("✅ 处理完成！")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("❌ 处理失败！")
        print("=" * 60)
        sys.exit(1)


if __name__ == '__main__':
    main()
