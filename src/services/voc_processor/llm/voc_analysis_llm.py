#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VOC分析LLM模块

功能：
- 定义分析结果的数据模型
- 加载RAG映射规则
- 构建分析链
- 验证分析结果的层级关系
"""

import json
import os
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from .settings import VLLM_CFG


# ============================================================================
# 数据模型定义
# ============================================================================

class AnalysisResult(BaseModel):
    """单个分析结果的Pydantic模型"""
    vehicle_type: str = Field(description="车辆类型，如获取不到可参照club_bbs_name字段")
    information_source: str = Field(description="平台/媒体/账号名称，source字段")
    information_time: str = Field(description="获取自publish_time字段，格式为YYYY年MM月DD日")
    problem_type: str = Field(description="问题类型，从预定义列表中选择")
    problem_direction: str = Field(description="问题指向，必须是问题类型的子集")
    object_entity: str = Field(description="对象实体，必须是问题指向的子集")
    attribute_dimension: str = Field(description="属性维度，必须是对象实体的子集")
    emotion_type: str = Field(description="情感类型：正向|中性|负向")
    viewpoint_content: str = Field(description="用户主张的观点内容，格式由对象属性和观点组成")
    viewpoint_analysis: str = Field(description="基于观点内容的技术性描述")
    confidence: float = Field(description="置信度，0-1之间的数值，保留两位小数", ge=0.0, le=1.0)


class AnalysisResults(BaseModel):
    """多个分析结果的Pydantic模型"""
    results: List[AnalysisResult] = Field(description="文本中分析出的所有结果")


# ============================================================================
# RAG映射相关函数
# ============================================================================

def load_rag_map(rag_map_path: str) -> Optional[Dict[str, Any]]:
    """
    从rag_map.json提取所有层级信息并构建层级映射关系
    
    Args:
        rag_map_path: RAG映射文件路径
        
    Returns:
        dict: 包含问题类型列表、问题指向列表、对象列表、属性列表和层级映射的字典
        None: 如果文件不存在或读取失败
    """
    print(f"📖 正在读取RAG映射文件: {rag_map_path}")
    
    if not os.path.exists(rag_map_path):
        print(f"❌ 错误: RAG映射文件不存在: {rag_map_path}")
        return None
    
    try:
        with open(rag_map_path, 'r', encoding='utf-8') as f:
            rag_map = json.load(f)
    except Exception as e:
        print(f"❌ 错误: 读取RAG映射文件失败: {e}")
        return None
    
    # 初始化数据结构
    问题类型列表 = []
    问题指向列表 = []
    对象列表 = []
    属性列表 = []
    层级映射 = {}
    
    # 遍历rag_map的层级结构
    # 第一层：问题类型（如"智能座舱"）
    for 问题类型, 问题指向字典 in rag_map.items():
        问题类型列表.append(问题类型)
        
        if 问题类型 not in 层级映射:
            层级映射[问题类型] = {}
        
        # 第二层：问题指向（如"硬件设施"）
        for 问题指向, 对象字典 in 问题指向字典.items():
            if 问题指向 not in 问题指向列表:
                问题指向列表.append(问题指向)
            
            if 问题指向 not in 层级映射[问题类型]:
                层级映射[问题类型][问题指向] = {}
            
            # 第三层：对象实体（如"中控屏幕"）
            for 对象实体, 属性数组 in 对象字典.items():
                if 对象实体 not in 对象列表:
                    对象列表.append(对象实体)
                
                if 对象实体 not in 层级映射[问题类型][问题指向]:
                    层级映射[问题类型][问题指向][对象实体] = []
                
                # 第四层：属性维度（数组）
                for 属性 in 属性数组:
                    if 属性 not in 属性列表:
                        属性列表.append(属性)
                    层级映射[问题类型][问题指向][对象实体].append(属性)
    
    # 排序
    问题类型列表 = sorted(问题类型列表)
    问题指向列表 = sorted(问题指向列表)
    对象列表 = sorted(对象列表)
    属性列表 = sorted(属性列表)
    
    print(f"✓ 问题类型数量: {len(问题类型列表)}")
    print(f"✓ 问题指向数量: {len(问题指向列表)}")
    print(f"✓ 对象数量: {len(对象列表)}")
    print(f"✓ 属性数量: {len(属性列表)}")
    
    return {
        '问题类型列表': 问题类型列表,
        '问题指向列表': 问题指向列表,
        '对象列表': 对象列表,
        '属性列表': 属性列表,
        '层级映射': 层级映射
    }


def build_chain(labels_info: Dict[str, Any]):
    """
    构建包含标签分类信息的langchain chain
    
    Args:
        labels_info: 从load_rag_map返回的标签信息字典
        
    Returns:
        langchain chain对象
    """
    问题类型列表 = labels_info['问题类型列表']
    问题指向列表 = labels_info['问题指向列表']
    对象列表 = labels_info['对象列表']
    属性列表 = labels_info['属性列表']

    # 由于属性列表过长，只列出部分常见属性作为示例
    属性示例 = 属性列表[:50] if len(属性列表) > 50 else 属性列表

    # 创建PydanticOutputParser
    parser = PydanticOutputParser(pydantic_object=AnalysisResults)

    # 构建系统提示词
    system_prompt = f"""你是一名汽车舆情与故障检测专家。

任务：对输入的用户/媒体/车主论坛文本进行深度分析，按固定格式输出结构化结果。

要求：
1. 严格按照提供的可选值进行匹配，若无匹配则使用"其他"
2. 根据本地RAG映射关系，遵循层级约束：
   - 问题指向必须是问题类型的子集
   - 对象实体必须是问题指向的子集
   - 属性维度必须是对象实体的子集
3. 若文本涉及多个不同方面，必须生成多个独立的分析结果
4. 所有内容必须基于汽车行业专业术语，分析要具体、合理、有深度
5. 置信度根据分析质量给出0-1之间的数值

可选值：
- 问题类型：{"、".join(问题类型列表)}、其他
- 问题指向：{"、".join(问题指向列表)}、其他
- 对象实体：{"、".join(对象列表)}、其他
- 属性维度：{"、".join(属性示例)}（还有更多属性，请根据文本内容匹配最合适的，若无匹配则写"其他"）"""

    # 创建ChatPromptTemplate
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "请分析以下汽车论坛帖子：\n\n{topic_content}\n\n信息来源：{source}\n发布时间：{publish_time}\n论坛名称：{club_bbs_name}\n\n{format_instructions}")
    ]).partial(format_instructions=parser.get_format_instructions())

    # 创建ChatOpenAI模型
    model = ChatOpenAI(
        model=VLLM_CFG.MODEL_NAME,
        temperature=0.2,
        base_url=VLLM_CFG.URL,
        api_key=VLLM_CFG.API_KEY
    )

    # 构建chain
    chain = prompt | model | parser

    return chain


def validate_hierarchy(result: Dict[str, Any], 层级映射: Dict[str, Any]) -> Dict[str, Any]:
    """
    验证解析结果的层级关系，修正不符合层级约束的字段
    
    Args:
        result: 分析结果字典
        层级映射: RAG层级映射字典
        
    Returns:
        dict: 验证并修正后的结果字典
    """
    problem_type = result.get("problem_type", "其他")
    problem_direction = result.get("problem_direction", "其他")
    object_entity = result.get("object_entity", "其他")
    attribute_dimension = result.get("attribute_dimension", "其他")
    
    # 如果都是"其他"，不需要验证
    if (problem_type == "其他" and problem_direction == "其他" and 
        object_entity == "其他" and attribute_dimension == "其他"):
        return result
    
    # 验证问题指向是否属于问题类型的子节点
    if problem_type != "其他" and problem_direction != "其他":
        if problem_type in 层级映射:
            if problem_direction not in 层级映射[problem_type]:
                print(f"  ⚠️  警告: 问题指向 '{problem_direction}' 不属于问题类型 '{problem_type}' 的子节点，已设置为'其他'")
                result["problem_direction"] = "其他"
                problem_direction = "其他"
    
    # 验证对象实体是否属于问题指向的子节点
    if problem_type != "其他" and problem_direction != "其他" and object_entity != "其他":
        if (problem_type in 层级映射 and 
            problem_direction in 层级映射[problem_type]):
            if object_entity not in 层级映射[problem_type][problem_direction]:
                print(f"  ⚠️  警告: 对象实体 '{object_entity}' 不属于问题指向 '{problem_direction}' 的子节点，已设置为'其他'")
                result["object_entity"] = "其他"
                object_entity = "其他"
    
    # 验证属性维度是否属于对象实体的子节点
    if (problem_type != "其他" and problem_direction != "其他" and 
        object_entity != "其他" and attribute_dimension != "其他"):
        if (problem_type in 层级映射 and 
            problem_direction in 层级映射[problem_type] and 
            object_entity in 层级映射[problem_type][problem_direction]):
            if attribute_dimension not in 层级映射[problem_type][problem_direction][object_entity]:
                print(f"  ⚠️  警告: 属性维度 '{attribute_dimension}' 不属于对象实体 '{object_entity}' 的子节点，已设置为'其他'")
                result["attribute_dimension"] = "其他"
    
    return result
