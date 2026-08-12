#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen3.6 视频文字提取 MCP 客户端示例

功能：
- 读取本地视频文件并编码为 base64
- 通过 MCP 协议调用视频文字提取服务
- 显示从视频画面中提取到的文字结果

使用方法：
python mcp_video_client.py <video_path> [question]
"""

import asyncio
import base64
import json
import os
import sys
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Tuple, Optional

try:
    from mcp.client.stdio import stdio_client
    from mcp import ClientSession, StdioServerParameters
except ImportError:
    print("错误: 需要安装 mcp 库")
    print("请运行: pip install mcp")
    sys.exit(1)


def encode_video_to_base64(video_path):
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
    """
    try:
        from imageio_ffmpeg import count_frames_and_secs
        _, duration = count_frames_and_secs(video_path)
        return float(duration)
    except Exception:
        pass
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


def get_ffmpeg_executable() -> str:
    '''优先使用 PATH 中的 ffmpeg，否则复用 imageio-ffmpeg 自带二进制。'''
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    from imageio_ffmpeg import get_ffmpeg_exe
    return get_ffmpeg_exe()


def slice_video_by_time(video_path: str, slice_seconds: int) -> List[Tuple[float, float, Path]]:
    """
    使用 ffmpeg 按时间切片视频，返回 (start, end, slice_path) 列表
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
        cmd = [
            get_ffmpeg_executable(),
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


def cleanup_video_slices(slices: List[Tuple[float, float, Path]]) -> None:
    '''删除本次调用生成的切片目录。'''
    if slices:
        shutil.rmtree(slices[0][2].parent, ignore_errors=True)


async def analyze_video_mcp_single(video_path: str, question: str = None) -> dict:
    """通过 MCP 协议从单段视频中提取文字，返回结果字典"""
    # 获取脚本所在目录
    script_dir = Path(__file__).parent
    server_script = script_dir / "mcp_video_server.py"
    
    if not server_script.exists():
        raise FileNotFoundError(f"MCP 服务器脚本不存在: {server_script}")
    
    # 编码视频
    base64_video = encode_video_to_base64(video_path)
    
    # 配置 MCP 服务器参数
    server_params = StdioServerParameters(
        command="python3",
        args=[str(server_script), "--mcp"],
        env=None
    )
    
    print(f"\n🔗 连接到 MCP 服务器...")
    print(f"   服务器脚本: {server_script}")
    
    # 连接 MCP 服务器
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 初始化会话
            await session.initialize()
            
            print("✅ MCP 连接成功（视频文字提取）")
            
            # 准备参数
            arguments = {
                "base64_video": base64_video,
            }
            
            if question:
                arguments["question"] = question
            
            print(f"\n🚀 调用 analyze_video 工具（视频文字提取）...")
            if question:
                print(f"   问题: {question}")
            
            # 调用工具
            result = await session.call_tool("analyze_video", arguments)
            
            # 解析结果
            if result.content:
                result_text = result.content[0].text
                try:
                    result_json = json.loads(result_text)
                    return result_json
                except json.JSONDecodeError:
                    return {
                        "success": False,
                        "error": f"无法解析结果: {result_text}",
                        "error_type": "JSONDecodeError",
                    }
            else:
                return {"success": False, "error": "未收到结果", "error_type": "NoContent"}
async def integrate_texts_via_mcp(
    text_parts: List[str],
    video_carrier_path: str,
    question: str = None,
) -> Optional[str]:
    """
    通过 MCP 调用模型API整合多段文字为一篇连贯文章
    使用一个有效的视频文件作为载体（通常使用第一个切片）
    """
    # 使用提供的视频文件作为载体
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
    
    try:
        script_dir = Path(__file__).parent
        server_script = script_dir / "mcp_video_server.py"
        
        if not server_script.exists():
            return None
        
        server_params = StdioServerParameters(
            command="python3",
            args=[str(server_script), "--mcp"],
            env=None
        )
        
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                arguments = {
                    "base64_video": base64_video,
                    "question": integration_prompt,
                    "max_tokens": 4096,
                }
                
                result = await session.call_tool("analyze_video", arguments)
                
                if result.content:
                    result_text = result.content[0].text
                    try:
                        result_json = json.loads(result_text)
                        if result_json.get("success"):
                            return result_json.get("summary")
                    except json.JSONDecodeError:
                        return None
        return None
    except Exception as e:
        print(f"⚠️ 调用整合API失败: {e}，将使用简单拼接方式", file=sys.stderr)
        return None


async def analyze_video_mcp(video_path: str, question: str = None, slice_seconds: int = 0) -> dict:
    """
    支持时间切片的 MCP 视频文字提取：
    - slice_seconds <= 0: 直接整体调用一次
    - slice_seconds > 0: 使用 ffmpeg 按时间切片，多次调用并聚合结果，最后整合为一篇完整文章
    """
    if slice_seconds and slice_seconds > 0:
        slices = slice_video_by_time(video_path, slice_seconds)
        if not slices:
            # 回退整体模式
            return await analyze_video_mcp_single(video_path, question)

        all_text_parts: List[str] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0

        for idx, (start, end, slice_path) in enumerate(slices):
            print("\n" + "-" * 60)
            print(f"⏱ 正在处理切片 {idx} [{start:.1f}s - {end:.1f}s] ...")
            result = await analyze_video_mcp_single(str(slice_path), question)
            if not result.get("success"):
                failed = {
                    "success": False,
                    "error": f"处理切片 {idx} [{start:.1f}s - {end:.1f}s] 失败: {result.get('error', '未知错误')}",
                    "error_type": result.get("error_type", "Unknown"),
                }
                cleanup_video_slices(slices)
                return failed

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
        
        integrated_text = await integrate_texts_via_mcp(
            all_text_parts,
            video_carrier_path=carrier_video,
            question=question,
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
        completed = {
            "success": True,
            "summary": integrated_text,
            "model": os.getenv("QWEN_VL_MODEL", os.getenv("LLM_MODEL_NAME", "Qwen3.6-35B-A3B")),
            "usage": usage_info,
        }
        cleanup_video_slices(slices)
        return completed

    # 不切片时，直接整体调用一次
    return await analyze_video_mcp_single(video_path, question)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Qwen3.6 视频文字提取 MCP 客户端（支持时间切片）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 整体视频一次性提取
  python mcp_video_client.py ../car.mp4

  # 指定自定义说明
  python mcp_video_client.py ../car.mp4 "请只提取屏幕底部的字幕"

  # 按时间切片（每 10 秒一段）
  python mcp_video_client.py ../car.mp4 --slice-seconds 10
        """,
    )

    parser.add_argument("video_path", help="视频文件路径")
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="文字提取方式说明（可选）",
    )
    parser.add_argument(
        "--slice-seconds",
        type=int,
        default=10,
        help="按时间切片的秒数（默认 10；设置为 0 表示不切片，整体一次性处理）",
    )

    args = parser.parse_args()
    video_path = args.video_path
    question = args.question
    
    try:
        result = asyncio.run(analyze_video_mcp(video_path, question, slice_seconds=args.slice_seconds))

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

            # 将完整结果保存为 JSON 文件，存储到当前工作目录
            try:
                video_stem = Path(video_path).stem
                output_path = Path.cwd() / f"{video_stem}_analysis.json"
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                print(f"\n💾 结果已保存为 JSON 文件：{output_path}")
            except Exception as save_err:
                print(f"\n⚠️  结果保存为 JSON 时出错：{save_err}", file=sys.stderr)
        else:
            print("\n❌ 文字提取失败：")
            print(result.get("error", "未知错误"))
            print(f"错误类型: {result.get('error_type', 'Unknown')}")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
