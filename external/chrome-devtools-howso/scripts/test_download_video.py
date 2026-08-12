#!/usr/bin/env python3
"""
测试下载抖音视频 MCP 工具
"""

import sys
import json
import re
import asyncio
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import StdioStdoutServerTransport
except ImportError:
    try:
        from mcp.client.stdio import stdio_client
        from mcp import ClientSession, StdioServerParameters
    except ImportError:
        print("❌ 错误: 需要安装 mcp 库")
        print("   请运行: pip install mcp")
        sys.exit(1)


def parse_mcp_result(result):
    """解析 MCP 工具返回的结果，提取 JSON 数据"""
    data = None
    
    try:
        content = result.content if hasattr(result, 'content') else []
    except AttributeError:
        content = getattr(result, "content", [])
    
    if not content:
        if hasattr(result, 'result'):
            content = [result.result]
        elif hasattr(result, 'value'):
            content = [result.value]
    
    for item in content:
        if hasattr(item, 'type') and hasattr(item, 'text'):
            item_type = item.type
            text = item.text
        elif isinstance(item, dict):
            item_type = item.get("type")
            text = item.get("text", "")
        elif isinstance(item, str):
            text = item
            item_type = "text"
        else:
            try:
                text = str(item)
                item_type = "text"
            except:
                continue
        
        if item_type == "text" and text:
            try:
                json_match = re.search(r'```json\n(.*?)\n```', text, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(1))
                else:
                    data = json.loads(text)
                break
            except (json.JSONDecodeError, AttributeError):
                continue
    
    return data


async def main():
    """主函数"""
    import os
    from pathlib import Path
    
    # 检查命令行参数
    if len(sys.argv) < 2:
        print("使用方法: python3 test_download_video.py <视频URL> [输出文件路径]")
        print("\n示例:")
        print("  python3 test_download_video.py 'https://v26-web.douyinvod.com/...'")
        print("  python3 test_download_video.py 'https://v26-web.douyinvod.com/...' video.mp4")
        sys.exit(1)
    
    video_url = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    # 默认使用本地版本
    use_npx = os.getenv("USE_NPX_MCP", "false").lower() == "true"
    
    if use_npx:
        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "chrome-devtools-mcp@latest", "--browser-url=http://127.0.0.1:9222"]
        )
    else:
        script_dir = Path(__file__).parent
        mcp_dir = script_dir.parent / "chrome-devtools-mcp"
        index_js = mcp_dir / "build" / "src" / "index.js"
        
        if not index_js.exists():
            print("❌ 错误: 本地编译文件不存在")
            print(f"   请先运行: cd chrome-devtools-mcp && npm run build")
            sys.exit(1)
        
        server_params = StdioServerParameters(
            command="node",
            args=[str(index_js), "--browser-url=http://127.0.0.1:9222"]
        )
    
    print("=" * 60)
    print("📥 下载抖音视频（MCP 工具）")
    print("=" * 60)
    print(f"\n📌 视频 URL: {video_url[:80]}...")
    if output_path:
        print(f"📁 输出路径: {output_path}")
    print("\n1️⃣ 连接 MCP Server ...")
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("✓ 已连接\n")
                await download_video(session, video_url, output_path)
    except NameError:
        transport = StdioStdoutServerTransport(server_params)
        async with ClientSession(transport) as session:
            await session.initialize()
            print("✓ 已连接\n")
            await download_video(session, video_url, output_path)


async def download_video(session, video_url, output_path=None):
    """下载视频"""
    try:
        print("2️⃣ 调用 download_douyin_video 工具...")
        
        # 准备参数
        params = {
            "url": video_url,
            "referer": "https://www.douyin.com",
        }
        if output_path:
            params["filePath"] = output_path
        
        # 调用工具
        result = await session.call_tool("download_douyin_video", params)
        
        # 解析结果
        data = parse_mcp_result(result)
        
        # 显示结果
        print("\n" + "=" * 60)
        if data and data.get("success"):
            print("✅ 下载成功！")
            print("=" * 60)
            print(f"\n📁 文件: {data.get('filename', 'unknown')}")
            print(f"📊 大小: {data.get('fileSizeMB', '0')} MB")
            print(f"🔗 URL: {data.get('url', '')[:80]}...")
        else:
            print("❌ 下载失败")
            print("=" * 60)
            print("\n原始返回:")
            print(result)
        
        print("\n" + "=" * 60)
        print("✅ 完成！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n用户中断操作")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
