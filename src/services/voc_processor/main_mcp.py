#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VOC用户声音处理MCP服务器

将VOC处理功能暴露为MCP工具（filter_voc、analyze_voc），可通过MCP协议调用。

使用方法：
    python -m src.services.voc_processor.main_mcp
    python -m src.services.voc_processor.main_mcp <input.json> [output.json]  # 命令行模式
"""

import os
import sys
import asyncio
import json
from typing import Any, Optional

# 支持直接运行脚本（非包模式）：将 voc_processor 目录加入 path
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    # 如果MCP库未安装，使用标准输入输出方式
    print("警告: MCP库未安装，将使用标准输入输出模式")
    Server = None
    stdio_server = None
    Tool = None
    TextContent = None

try:
    from .filter_json import process_single_json as filter_json
    from .llm.voc_analysis_llm import load_rag_map, build_chain
    from .analysis_json import process_single_json_file as analysis_json
except ImportError:
    from filter_json import process_single_json as filter_json
    from llm.voc_analysis_llm import load_rag_map, build_chain
    from analysis_json import process_single_json_file as analysis_json


# 全局变量：缓存chain和labels_info
_cached_chain = None
_cached_labels_info = None


def get_analysis_chain():
    """获取或构建分析链（带缓存）"""
    global _cached_chain, _cached_labels_info
    
    if _cached_chain is None or _cached_labels_info is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        rag_map_path = os.path.join(script_dir, 'llm', 'rag_map.json')
        
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
        raise RuntimeError(f"内容过滤失败: {e}")


def process_voc_file(input_file: str, output_file: Optional[str] = None) -> dict:
    """
    处理VOC文件的主函数
    
    Args:
        input_file: 输入JSON文件路径
        output_file: 输出JSON文件路径（可选，默认使用输入文件名加_analyzed后缀）
        
    Returns:
        dict: 处理结果
    """
    input_file = os.path.abspath(input_file)
    
    # 如果没有指定输出文件，使用输入文件名加_analyzed后缀
    if not output_file:
        base_name = os.path.splitext(input_file)[0]
        ext = os.path.splitext(input_file)[1]
        output_file = f"{base_name}_analyzed{ext}"
    
    output_file = os.path.abspath(output_file)
    
    # 判断是否需要进行分析
    if not should_analyze(input_file):
        return {
            "success": False,
            "message": "内容过滤未通过，跳过分析步骤",
            "input_file": input_file,
            "output_file": None
        }
    
    # 执行内容分析
    try:
        chain, labels_info = get_analysis_chain()
        success = analysis_json(input_file, output_file, chain, labels_info)
        
        if success:
            return {
                "success": True,
                "message": "处理完成",
                "input_file": input_file,
                "output_file": output_file
            }
        else:
            return {
                "success": False,
                "message": "分析处理失败",
                "input_file": input_file,
                "output_file": None
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"分析步骤失败: {e}",
            "input_file": input_file,
            "output_file": None
        }


# 创建MCP服务器
if Server is not None:
    app = Server("voc-processor")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        """列出可用的工具（仅保留筛选与解析两个工具）"""
        return [
            Tool(
                name="filter_voc",
                description="仅对VOC用户声音JSON文件进行内容筛选，返回是否需要进一步分析。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "input_file": {
                            "type": "string",
                            "description": "输入JSON文件路径"
                        }
                    },
                    "required": ["input_file"]
                }
            ),
            Tool(
                name="analyze_voc",
                description="对已通过筛选的VOC用户声音JSON文件进行内容解析与标签提取。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "input_file": {
                            "type": "string",
                            "description": "输入JSON文件路径"
                        },
                        "output_file": {
                            "type": "string",
                            "description": "输出JSON文件路径（可选，默认使用输入文件名加_analyzed后缀）",
                            "default": None
                        }
                    },
                    "required": ["input_file"]
                }
            )
        ]


    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """处理工具调用（仅支持 filter_voc 与 analyze_voc）"""
        input_file = arguments.get("input_file")
        output_file = arguments.get("output_file")

        if name in {"filter_voc", "analyze_voc"} and not input_file:
            return [TextContent(
                type="text",
                text='{"success": false, "message": "缺少必需参数: input_file"}'
            )]

        if name == "filter_voc":
            try:
                filter_result = filter_json(input_file)

                if filter_result is None:
                    result_payload = {
                        "success": True,
                        "message": "清洗后的文本为空或无有效内容，建议不进行后续分析",
                        "input_file": os.path.abspath(input_file),
                        "need_analysis": False,
                        "filter_result": None
                    }
                else:
                    result_value = getattr(filter_result, "result", None)
                    analysis_text = getattr(filter_result, "analysis", None)
                    result_payload = {
                        "success": True,
                        "message": "筛选完成",
                        "input_file": os.path.abspath(input_file),
                        "need_analysis": result_value == "是",
                        "filter_result": {
                            "result": result_value,
                            "analysis": analysis_text
                        }
                    }

                return [TextContent(
                    type="text",
                    text=json.dumps(result_payload, ensure_ascii=False, indent=2)
                )]
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "success": False,
                        "message": f"筛选失败: {str(e)}",
                        "input_file": os.path.abspath(input_file) if input_file else None
                    }, ensure_ascii=False, indent=2)
                )]

        if name == "analyze_voc":
            try:
                # 直接进行解析，不在此处做筛选，筛选由调用方自行决定是否执行
                input_path = os.path.abspath(input_file)

                if not output_file:
                    base_name = os.path.splitext(input_path)[0]
                    ext = os.path.splitext(input_path)[1]
                    output_file = f"{base_name}_analyzed{ext}"

                output_path = os.path.abspath(output_file)

                chain, labels_info = get_analysis_chain()
                success = analysis_json(input_path, output_path, chain, labels_info)

                if success:
                    result_payload = {
                        "success": True,
                        "message": "解析完成",
                        "input_file": input_path,
                        "output_file": output_path
                    }
                else:
                    result_payload = {
                        "success": False,
                        "message": "解析处理失败",
                        "input_file": input_path,
                        "output_file": None
                    }

                return [TextContent(
                    type="text",
                    text=json.dumps(result_payload, ensure_ascii=False, indent=2)
                )]
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "success": False,
                        "message": f"解析步骤失败: {str(e)}",
                        "input_file": os.path.abspath(input_file) if input_file else None,
                        "output_file": None
                    }, ensure_ascii=False, indent=2)
                )]

        return [TextContent(
            type="text",
            text=f'{{"success": false, "message": "未知工具: {name}"}}'
        )]


    async def main():
        """主函数：启动MCP服务器"""
        import sys
        # 检查stdin是否可用（MCP服务器需要通过stdio通信）
        if sys.stdin.isatty():
            print("警告: MCP服务器需要通过MCP客户端调用，不能直接运行")
            print("请使用MCP客户端连接此服务器，或使用命令行模式:")
            print("  python -m src.services.voc_processor.main_mcp <input_json_file> [output_json_file]")
            sys.exit(1)
        
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options()
            )
else:
    # 如果没有MCP库，提供命令行接口
    def main_sync():
        """命令行模式（同步）"""
        if len(sys.argv) < 2:
            print("使用方法: python -m src.services.voc_processor.main_mcp <input_json_file> [output_json_file]")
            print("示例: python -m src.services.voc_processor.main_mcp input.json output.json")
            sys.exit(1)
        
        input_file = sys.argv[1]
        output_file = sys.argv[2] if len(sys.argv) > 2 else None
        
        result = process_voc_file(input_file, output_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    async def main():
        """命令行模式（异步包装）"""
        main_sync()


if __name__ == '__main__':
    # 如果提供了命令行参数，使用命令行模式
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
        output_file = sys.argv[2] if len(sys.argv) > 2 else None
        result = process_voc_file(input_file, output_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # 没有命令行参数，启动MCP服务器
        if Server is None:
            print("错误: MCP库未安装，且未提供命令行参数")
            print("请安装MCP库: pip install mcp")
            print("或使用命令行模式: python -m src.services.voc_processor.main_mcp <input_json_file> [output_json_file]")
            sys.exit(1)
        else:
            # 启动MCP服务器
            asyncio.run(main())
