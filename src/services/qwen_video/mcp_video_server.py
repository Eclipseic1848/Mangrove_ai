#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen3.6 视频文字提取 MCP 服务器

功能：
- 接收客户端传来的 base64 编码视频
- 转发给本地 Qwen3.6-35B-A3B 模型，从视频画面中提取文字（如字幕、标牌、界面文字等）
- 返回提取到的文字结果

使用方法（MCP）：
作为 MCP 服务器运行，通过 MCP 客户端调用 analyze_video 工具

配置：
- 模型服务地址：http://192.168.1.20:6012/v1
- 模型名称：Qwen3.6-35B-A3B
"""

import json
import os
import sys
import httpx
from openai import OpenAI

# MCP相关导入
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("错误: 需要安装 mcp 库", file=sys.stderr)
    print("请运行: pip install mcp", file=sys.stderr)
    sys.exit(1)

# ===== 配置 =====
BASE_URL = os.getenv("QWEN_VL_BASE_URL", os.getenv("LLM_BASE_URL", "http://192.168.1.20:6012/v1"))
MODEL = os.getenv("QWEN_VL_MODEL", os.getenv("LLM_MODEL_NAME", "Qwen3.6-35B-A3B"))
API_KEY = os.getenv("QWEN_VL_API_KEY", "not-needed")

# 创建 OpenAI 客户端
client = OpenAI(
    base_url=BASE_URL,
    api_key=API_KEY,
    http_client=httpx.Client(trust_env=False, timeout=600),
)

# 创建 MCP 服务器
server = Server("qwen-video-analysis")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """列出可用的工具"""
    return [
        Tool(
            name="analyze_video",
            description="从视频画面中提取文字，使用 Qwen3.6-35B-A3B 模型识别字幕/标牌/界面文字等内容",
            inputSchema={
                "type": "object",
                "properties": {
                    "base64_video": {
                        "type": "string",
                        "description": "视频文件的 base64 编码字符串（不包含 data:video/mp4;base64, 前缀）"
                    },
                    "question": {
                        "type": "string",
                        "description": "对视频文字提取和整合方式的额外说明，默认输出一整篇连贯文章",
                        "default": (
                            "请先在内部识别并理解整个视频画面中出现的所有文字内容（包括字幕、标牌、界面文字、弹幕等），"
                            "然后基于这些文字内容，用自然、连贯的中文写出一篇完整的讲解稿/文章，"
                            "大致复现原视频的讲述逻辑和信息点。\n"
                            "要求：\n"
                            "1. 输出为一整篇连续的中文文章，不要带时间戳、不要用项目符号列表；\n"
                            "2. 内容可以适当合并相近句子，但不要凭空杜撰与画面文字无关的信息；\n"
                            "3. 如果有品牌名、车型名、数字等关键信息，请尽量原样保留；\n"
                            "4. 语气可以口语化，类似主持人口播文案。"
                        )
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "最大生成 token 数",
                        "default": 2048
                    },
                    "temperature": {
                        "type": "number",
                        "description": "温度参数，控制输出的随机性",
                        "default": 0.7
                    }
                },
                "required": ["base64_video"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """处理工具调用"""
    if name == "analyze_video":
        try:
            # 获取参数
            base64_video = arguments.get("base64_video")
            if not base64_video:
                return [TextContent(
                    type="text",
                    text=json.dumps({"error": "缺少必需参数: base64_video"}, ensure_ascii=False)
                )]
            
            # 移除可能的前缀
            if base64_video.startswith("data:video"):
                # 提取 base64 部分
                base64_video = base64_video.split(",")[-1]
            
            question = arguments.get(
                "question",
                (
                    "请先在内部识别并理解整个视频画面中出现的所有文字内容（包括字幕、标牌、界面文字、弹幕等），"
                    "然后基于这些文字内容，用自然、连贯的中文写出一篇完整的讲解稿/文章，"
                    "大致复现原视频的讲述逻辑和信息点。\n"
                    "要求：\n"
                    "1. 输出为一整篇连续的中文文章，不要带时间戳、不要用项目符号列表；\n"
                    "2. 内容可以适当合并相近句子，但不要凭空杜撰与画面文字无关的信息；\n"
                    "3. 如果有品牌名、车型名、数字等关键信息，请尽量原样保留；\n"
                    "4. 语气可以口语化，类似主持人口播文案。"
                )
            )
            max_tokens = arguments.get("max_tokens", 2048)
            temperature = arguments.get("temperature", 0.7)
            
            # 构建消息
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": f"data:video/mp4;base64,{base64_video}"
                            }
                        },
                        {
                            "type": "text",
                            "text": question
                        }
                    ]
                }
            ]
            
            # 调用模型
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=0.9,
                presence_penalty=1.0,
            )
            
            # 获取结果：兼容 content 为 None 或列表（部分视频/多模态 API 返回 list[{"type":"text","text":"..."}]）
            raw_content = response.choices[0].message.content
            result_text = None
            if raw_content is not None:
                if isinstance(raw_content, str):
                    result_text = raw_content.strip() or None
                elif isinstance(raw_content, list):
                    for part in raw_content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            t = part.get("text")
                            if t and isinstance(t, str) and t.strip():
                                result_text = t.strip()
                                break
            # 确保 summary 有内容时不为 None，便于 analysis JSON 文件可读
            if result_text is None:
                result_text = ""

            # 返回结果（JSON 格式）
            result = {
                "success": True,
                "summary": result_text if result_text else None,
                "model": MODEL,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
                    "completion_tokens": response.usage.completion_tokens if response.usage else None,
                    "total_tokens": response.usage.total_tokens if response.usage else None,
                }
            }
            
            return [TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2)
            )]
            
        except Exception as e:
            error_result = {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
            return [TextContent(
                type="text",
                text=json.dumps(error_result, ensure_ascii=False, indent=2)
            )]
    
    else:
        return [TextContent(
            type="text",
            text=json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        )]


async def main_mcp():
    """MCP 服务器主函数"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio
    
    # 检查是否是 MCP 模式（通过 stdio）
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        # MCP 模式
        asyncio.run(main_mcp())
    else:
        # 直接运行模式（用于测试）
        print("MCP 服务器模式")
        print("使用方法：")
        print("  1. 作为 MCP 服务器运行：python mcp_video_server.py --mcp")
        print("  2. 通过 MCP 客户端调用 analyze_video 工具")
        print("\n配置：")
        print(f"  模型服务: {BASE_URL}")
        print(f"  模型名称: {MODEL}")
        print("\n环境变量：")
        print("  QWEN_VL_BASE_URL - 模型服务地址（默认: http://192.168.1.20:6012/v1）")
        print("  QWEN_VL_MODEL - 模型名称（默认: Qwen3.6-35B-A3B）")
        print("  QWEN_VL_API_KEY - API 密钥（默认: not-needed）")
