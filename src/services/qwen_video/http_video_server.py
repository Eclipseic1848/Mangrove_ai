#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen3.6 视频文字提取 HTTP 服务器

功能：
- 通过 HTTP API 接收客户端传来的 base64 编码视频
- 转发给本地 Qwen3.6-35B-A3B 模型，从视频画面中提取文字（如字幕、标牌、界面文字等）
- 返回提取到的文字结果

使用方法：
python http_video_server.py [--host 0.0.0.0] [--port 8000]

配置：
- 模型服务地址：http://192.168.1.20:6012/v1
- 模型名称：Qwen3.6-35B-A3B
"""

import json
import os
import sys
import httpx
from typing import Optional
from openai import OpenAI
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ===== 配置 =====
BASE_URL = os.getenv("QWEN_VL_BASE_URL", os.getenv("LLM_BASE_URL", "http://192.168.1.20:6012/v1"))
MODEL = os.getenv("QWEN_VL_MODEL", os.getenv("LLM_MODEL_NAME", "Qwen3.6-35B-A3B"))
API_KEY = os.getenv("QWEN_VL_API_KEY", "not-needed")
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

# 创建 OpenAI 客户端
client = OpenAI(
    base_url=BASE_URL,
    api_key=API_KEY,
    http_client=httpx.Client(trust_env=False, timeout=600),
)

# 创建 FastAPI 应用
app = FastAPI(
    title="Qwen3.6 视频文字提取服务",
    description="基于 Qwen3.6-35B-A3B 的视频文字提取 API（从视频画面中提取字幕/标牌/界面文字等）",
    version="1.0.0"
)

# 配置 CORS（允许跨域请求）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== 请求/响应模型 =====
class VideoAnalysisRequest(BaseModel):
    """视频文字提取请求"""
    base64_video: str = Field(..., description="视频文件的 base64 编码字符串（不包含 data:video/mp4;base64, 前缀）")
    question: Optional[str] = Field(
        default=(
            "请先在内部识别并理解整个视频画面中出现的所有文字内容（包括字幕、标牌、界面文字、弹幕等），"
            "然后基于这些文字内容，用自然、连贯的中文写出一篇完整的讲解稿/文章，"
            "大致复现原视频的讲述逻辑和信息点。\n"
            "要求：\n"
            "1. 输出为一整篇连续的中文文章，不要带时间戳、不要用项目符号列表；\n"
            "2. 内容可以适当合并相近句子，但不要凭空杜撰与画面文字无关的信息；\n"
            "3. 如果有品牌名、车型名、数字等关键信息，请尽量原样保留；\n"
            "4. 语气可以口语化，类似主持人口播文案。"
        ),
        description="对视频文字提取和整合方式的额外说明（可选）"
    )
    max_tokens: Optional[int] = Field(default=2048, description="最大生成 token 数")
    temperature: Optional[float] = Field(default=0.7, description="温度参数，控制输出的随机性")


class VideoAnalysisResponse(BaseModel):
    """视频文字提取响应"""
    success: bool
    summary: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[dict] = None


# ===== API 端点 =====
@app.get("/")
async def root():
    """根路径，返回服务信息"""
    return {
        "service": "Qwen3.6 视频文字提取服务",
        "version": "1.0.0",
        "model": MODEL,
        "model_url": BASE_URL,
        "endpoints": {
            "analyze": "/analyze",
            "health": "/health"
        }
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "model": MODEL,
        "model_url": BASE_URL
    }


@app.post("/analyze", response_model=VideoAnalysisResponse)
async def analyze_video(request: VideoAnalysisRequest):
    """
    从视频中提取文字
    
    接收 base64 编码的视频，调用 Qwen3.6-35B-A3B 模型，从视频画面中提取文字（如字幕、标牌、界面文字等）
    """
    try:
        base64_video = request.base64_video
        
        # 移除可能的前缀
        if base64_video.startswith("data:video"):
            base64_video = base64_video.split(",")[-1]
        
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
                        "text": request.question
                    }
                ]
            }
        ]
        
        # 调用模型
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=0.9,
            presence_penalty=1.0,
        )
        
        # 获取结果（兼容多模态返回格式）
        message_content = response.choices[0].message.content
        if isinstance(message_content, list):
            # 新版多模态接口：content 是若干 text / other part 组成的列表
            text_parts = []
            for part in message_content:
                # 只拼接文本内容
                if getattr(part, "type", None) == "text":
                    text_parts.append(getattr(part, "text", ""))
            result_text = "\n".join([t for t in text_parts if t])
        else:
            # 旧版：content 直接是字符串
            result_text = message_content
        
        # 构建响应
        usage_info = None
        if response.usage:
            usage_info = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        
        return VideoAnalysisResponse(
            success=True,
            summary=result_text,
            model=MODEL,
            usage=usage_info
        )
        
    except Exception as e:
        return VideoAnalysisResponse(
            success=False,
            error=str(e),
            error_type=type(e).__name__
        )


def main():
    """启动服务器"""
    import argparse
    import uvicorn
    
    parser = argparse.ArgumentParser(description="Qwen3.6 视频分析 HTTP 服务器")
    parser.add_argument("--host", default=SERVER_HOST, help=f"监听地址（默认: {SERVER_HOST}）")
    parser.add_argument("--port", type=int, default=SERVER_PORT, help=f"监听端口（默认: {SERVER_PORT}）")
    args = parser.parse_args()
    
    print("=" * 60)
    print("🚀 Qwen3.6 视频文字提取 HTTP 服务器")
    print("=" * 60)
    print(f"📡 监听地址: {args.host}:{args.port}")
    print(f"🤖 模型服务: {BASE_URL}")
    print(f"📦 模型名称: {MODEL}")
    print("=" * 60)
    print("\nAPI 端点:")
    print(f"  GET  /          - 服务信息")
    print(f"  GET  /health    - 健康检查")
    print(f"  POST /analyze   - 分析视频")
    print("\n启动中...")
    print(f"\n💡 客户端可以通过以下地址访问:")
    print(f"   http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}")
    
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info"
    )


if __name__ == "__main__":
    main()
