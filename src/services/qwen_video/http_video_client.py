#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen3.6 视频文字提取 HTTP 客户端

功能：
- 读取本地视频文件并编码为 base64
- 通过 HTTP API 调用视频文字提取服务
- 显示从视频画面中提取到的文字结果

使用方法：
python http_video_client.py <video_path> [question] [--server http://192.168.x.x:8000]
"""

import argparse
import base64
import json
import os
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple

try:
    import requests
except ImportError:
    print("错误: 需要安装 requests 库", file=sys.stderr)
    print("请运行: pip install requests", file=sys.stderr)
    sys.exit(1)


def encode_video_to_base64(video_path: str) -> str:
    """将本地视频文件编码为 base64"""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")
    
    print(f"📹 正在读取视频文件: {video_path}")
    with open(video_path, "rb") as video_file:
        video_data = video_file.read()
        base64_video = base64.b64encode(video_data).decode('utf-8')
    
    file_size_mb = len(video_data) / (1024 * 1024)
    print(f"   文件大小: {file_size_mb:.2f} MB")
    print(f"   Base64 编码长度: {len(base64_video)} 字符")
    
    return base64_video


def get_video_duration(video_path: str) -> Optional[float]:
    """
    使用 ffprobe 获取视频总时长（秒）
    依赖本地已安装 ffprobe（通常随 ffmpeg 提供）
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        duration_str = result.stdout.strip()
        return float(duration_str)
    except Exception:
        print("⚠️ 无法通过 ffprobe 获取视频时长，将不进行时间切片。", file=sys.stderr)
        return None


def slice_video_by_time(video_path: str, slice_seconds: int) -> List[Tuple[float, float, Path]]:
    """
    使用 ffmpeg 按时间切片视频，返回 (start, end, slice_path) 列表
    依赖本地已安装 ffmpeg。
    """
    if slice_seconds <= 0:
        return []

    duration = get_video_duration(video_path)
    if duration is None or duration <= 0:
        return []

    slices: List[Tuple[float, float, Path]] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="video_slices_"))
    print(f"⏱ 总时长约: {duration:.2f} 秒，将按每段 {slice_seconds} 秒进行切片，临时目录: {tmp_dir}")

    start = 0.0
    idx = 0
    while start < duration:
        end = min(start + slice_seconds, duration)
        out_path = tmp_dir / f"slice_{idx:04d}.mp4"
        # 使用 -ss / -t + -c copy 做无损切片
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            video_path,
            "-t",
            str(end - start),
            "-c",
            "copy",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if out_path.exists():
                slices.append((start, end, out_path))
                print(f"  ✅ 生成切片 {idx}：[{start:.1f}s - {end:.1f}s] -> {out_path.name}")
            else:
                print(f"  ⚠️ 切片 {idx} 生成失败，文件不存在。", file=sys.stderr)
        except Exception as e:
            print(f"  ⚠️ 切片 {idx} 生成失败: {e}", file=sys.stderr)
            break

        idx += 1
        start += slice_seconds

    if not slices:
        print("⚠️ 未成功生成任何切片，将退回整体视频模式。", file=sys.stderr)

    return slices


def analyze_video_http(
    video_path: str,
    server_url: str,
    question: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.7
) -> dict:
    """
    通过 HTTP API 从视频中提取文字（单段视频）
    
    Args:
        video_path: 视频文件路径
        server_url: 服务器地址（如 http://192.168.1.100:8000）
        question: 文字提取方式说明（可选，例如是否需要标注时间/位置）
        max_tokens: 最大 token 数
        temperature: 温度参数
    
    Returns:
        文字提取结果字典
    """
    # 编码视频
    base64_video = encode_video_to_base64(video_path)
    
    # 准备请求数据
    payload = {
        "base64_video": base64_video,
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    
    if question:
        payload["question"] = question
    
    # 构建 API URL
    api_url = f"{server_url.rstrip('/')}/analyze"
    
    print(f"\n🔗 连接到服务器: {server_url}")
    print(f"📤 发送请求到: {api_url}")
    
    try:
        # 发送 POST 请求
        response = requests.post(
            api_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=300  # 5分钟超时（视频分析可能需要较长时间）
        )
        
        # 检查 HTTP 状态码
        response.raise_for_status()
        
        # 解析响应
        result = response.json()
        return result
        
    except requests.exceptions.ConnectionError:
        raise ConnectionError(f"无法连接到服务器: {server_url}\n请检查服务器是否运行，以及地址是否正确")
    except requests.exceptions.Timeout:
        raise TimeoutError("请求超时，视频分析可能需要较长时间")
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP 错误: {e.response.status_code}"
        try:
            error_detail = e.response.json()
            error_msg += f"\n详情: {error_detail}"
        except:
            error_msg += f"\n响应: {e.response.text}"
        raise Exception(error_msg)
    except Exception as e:
        raise Exception(f"请求失败: {e}")


def integrate_texts_via_api(
    text_parts: List[str],
    server_url: str,
    video_carrier_path: str,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> Optional[str]:
    """
    通过调用模型API整合多段文字为一篇连贯文章
    使用一个有效的视频文件作为载体（通常使用第一个切片），实际内容在question中传递
    """
    # 使用提供的视频文件作为载体（通常是第一个切片）
    try:
        base64_video = encode_video_to_base64(video_carrier_path)
    except Exception as e:
        print(f"⚠️ 无法读取载体视频文件: {e}", file=sys.stderr)
        return None
    
    # 整合所有文字片段
    combined_input = "\n\n".join([f"片段{i+1}：{text}" for i, text in enumerate(text_parts) if text.strip()])
    
    # 构建整合提示词
    integration_prompt = (
        f"以下是按时间顺序从视频不同片段中提取的文字内容，请将这些内容整合成一篇连贯、流畅的中文文章。\n\n"
        f"要求：\n"
        f"1. 保持原文的核心信息和关键内容；\n"
        f"2. 去除重复和冗余信息；\n"
        f"3. 用自然流畅的语言连接各个片段；\n"
        f"4. 输出一篇完整的文章，不要分段标记或时间戳；\n"
        f"5. 确保文章逻辑连贯，读起来像一篇完整的讲解稿或文章。\n\n"
        f"提取的文字内容：\n{combined_input}\n\n"
        f"请整合输出："
    )
    
    # 准备请求数据
    payload = {
        "base64_video": base64_video,
        "question": integration_prompt,
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    
    # 构建 API URL
    api_url = f"{server_url.rstrip('/')}/analyze"
    
    try:
        response = requests.post(
            api_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=300
        )
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            return result.get("summary")
        else:
            print(f"⚠️ 文字整合失败: {result.get('error', '未知错误')}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"⚠️ 调用整合API失败: {e}，将使用简单拼接方式", file=sys.stderr)
        return None


def analyze_video_http_with_slices(
    video_path: str,
    server_url: str,
    slice_seconds: int,
    question: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
) -> dict:
    """
    按时间切片后多次调用 HTTP API，从整段视频中提取文字，最后整合为一篇完整文章。
    """
    slices = slice_video_by_time(video_path, slice_seconds)
    if not slices:
        # 回退到单段模式
        return analyze_video_http(video_path, server_url, question, max_tokens, temperature)

    all_text_parts: List[str] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    for idx, (start, end, slice_path) in enumerate(slices):
        print("\n" + "-" * 60)
        print(f"⏱ 正在处理切片 {idx} [{start:.1f}s - {end:.1f}s] ...")
        try:
            # 对每个时间段可以复用同一个 question，提示词仍由服务端控制
            result = analyze_video_http(
                video_path=str(slice_path),
                server_url=server_url,
                question=question,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            return {
                "success": False,
                "error": f"处理切片 {idx} [{start:.1f}s - {end:.1f}s] 失败: {e}",
                "error_type": type(e).__name__,
            }

        if not result.get("success"):
            return {
                "success": False,
                "error": f"处理切片 {idx} [{start:.1f}s - {end:.1f}s] 失败: {result.get('error', '未知错误')}",
                "error_type": result.get("error_type", "Unknown"),
            }

        slice_text = result.get("summary") or ""
        all_text_parts.append(slice_text.strip())

        usage = result.get("usage") or {}
        total_prompt_tokens += usage.get("prompt_tokens") or 0
        total_completion_tokens += usage.get("completion_tokens") or 0
        total_tokens += usage.get("total_tokens") or 0

    # 整合所有切片的结果
    print("\n" + "=" * 60)
    print("📝 正在整合所有切片提取的文字为完整文章...")
    print("=" * 60)
    
    # 使用第一个切片作为载体视频（因为我们已经有了有效的切片文件）
    carrier_video = str(slices[0][2]) if slices else video_path
    
    integrated_text = integrate_texts_via_api(
        all_text_parts,
        server_url=server_url,
        video_carrier_path=carrier_video,
        max_tokens=max(4096, max_tokens * 2),  # 整合时使用更大的token数
        temperature=temperature,
    )
    
    # 如果整合失败，使用简单拼接
    if integrated_text is None or not integrated_text.strip():
        print("⚠️ 使用简单拼接方式整合文字", file=sys.stderr)
        integrated_text = "\n\n".join([text for text in all_text_parts if text.strip()])
    
    usage_info = {
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_tokens or total_prompt_tokens + total_completion_tokens,
    }

    return {
        "success": True,
        "summary": integrated_text,
        "model": os.getenv("QWEN_VL_MODEL", os.getenv("LLM_MODEL_NAME", "Qwen3.6-35B-A3B")),
        "usage": usage_info,
    }


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Qwen3.6 视频文字提取 HTTP 客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认服务器地址，从视频中提取文字
  python http_video_client.py ../car.mp4
  
  # 指定服务器地址和自定义提取说明
  python http_video_client.py ../car.mp4 "请只提取底部字幕的文字" --server http://192.168.1.100:8000
  
  # 使用环境变量指定服务器
  export SERVER_URL=http://192.168.1.100:8000
  python http_video_client.py ../car.mp4
        """
    )
    
    parser.add_argument(
        "video_path",
        help="视频文件路径"
    )
    
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="文字提取方式说明（可选）"
    )
    
    parser.add_argument(
        "--server",
        default=os.getenv("SERVER_URL", "http://localhost:8000"),
        help="服务器地址（默认: http://localhost:8000，可通过 SERVER_URL 环境变量设置）"
    )
    
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="最大 token 数（默认: 2048）"
    )
    
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="温度参数（默认: 0.7）"
    )

    parser.add_argument(
        "--slice-seconds",
        type=int,
        default=10,
        help="按时间切片的秒数（默认 10；设置为 0 表示不切片，整体一次性处理）",
    )
    
    args = parser.parse_args()
    
    try:
        # 从视频中提取文字
        if args.slice_seconds and args.slice_seconds > 0:
            result = analyze_video_http_with_slices(
                video_path=args.video_path,
                server_url=args.server,
                slice_seconds=args.slice_seconds,
                question=args.question,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
            )
        else:
            result = analyze_video_http(
                video_path=args.video_path,
                server_url=args.server,
                question=args.question,
                max_tokens=args.max_tokens,
                temperature=args.temperature
            )
        
        # 显示结果
        if result.get("success"):
            print("\n" + "=" * 60)
            print("✅ 视频文字提取成功")
            print("=" * 60)
            print(f"\n📝 提取到的文字：")
            print(result["summary"])
            
            if result.get("usage"):
                usage = result["usage"]
                print(f"\n📊 Token 使用情况：")
                if usage.get("prompt_tokens"):
                    print(f"   Prompt tokens: {usage['prompt_tokens']}")
                if usage.get("completion_tokens"):
                    print(f"   Completion tokens: {usage['completion_tokens']}")
                if usage.get("total_tokens"):
                    print(f"   Total tokens: {usage['total_tokens']}")
            
            print("=" * 60)
        else:
            print("\n❌ 分析失败：")
            print(f"错误: {result.get('error', '未知错误')}")
            print(f"错误类型: {result.get('error_type', 'Unknown')}")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
