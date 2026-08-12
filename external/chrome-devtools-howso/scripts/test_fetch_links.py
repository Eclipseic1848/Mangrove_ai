#!/usr/bin/env python3
"""
简单测试脚本：一键获取抖音视频链接
使用新的 fetch_douyin_video_links 工具
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
    url = None
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = "https://www.douyin.com"
    
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
    print("🚀 一键获取抖音视频链接")
    print("=" * 60)
    print(f"\n📌 目标 URL: {url}")
    print("\n1️⃣ 连接 MCP Server ...")
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("✓ 已连接\n")
                await run_test(session, url)
    except NameError:
        transport = StdioStdoutServerTransport(server_params)
        async with ClientSession(transport) as session:
            await session.initialize()
            print("✓ 已连接\n")
            await run_test(session, url)


async def run_test(session, url):
    """运行测试"""
    try:
        print("2️⃣ 调用 fetch_douyin_video_links 工具...")
        print("   (这将自动完成：导航 → 注入hook → 等待 → 播放 → 提取链接)\n")
        
        # 调用一键工具
        result = await session.call_tool(
            "fetch_douyin_video_links",
            {
                "url": url,
                "initialWaitMs": 8000,
                "playWaitMs": 5000,
                "networkLimit": 20,
                "includeAllVideos":False,
            }
        )
        
        # 解析结果
        data = parse_mcp_result(result)
        
        if not data:
            print("❌ 无法解析工具返回结果")
            print("\n原始返回:")
            print(result)
            return
        
        # 显示执行步骤
        if "steps" in data:
            print("📋 执行步骤:")
            for i, step in enumerate(data["steps"], 1):
                status = "✓" if step.get("ok") else "✗"
                name = step.get("name", "unknown")
                detail = step.get("detail", {})
                print(f"   {i}. {status} {name}")
                if detail and isinstance(detail, dict):
                    if "url" in detail:
                        print(f"      URL: {detail['url']}")
                    if "attempted" in detail:
                        print(f"      尝试播放: {detail.get('attempted', 0)}/{detail.get('total', 0)}")
                    if "ms" in detail:
                        print(f"      等待: {detail['ms']}ms")
                    if "error" in detail:
                        print(f"      错误: {detail['error']}")
            print()
        
        # 显示视频链接
        links = data.get("links", [])
        count = data.get("count", 0)
        
        print("=" * 60)
        print(f"🎥 找到 {count} 个视频链接:")
        print("=" * 60)
        
        if count == 0:
            print("❌ 未找到任何视频链接")
            print("\n可能的原因:")
            print("  1. 页面尚未完全加载")
            print("  2. 需要手动播放视频")
            print("  3. 页面中没有视频内容")
            return
        
        # 显示所有链接
        for i, link in enumerate(links, 1):
            print(f"\n{i}. {link}")
        
        # 保存到文件
        output_file = Path(__file__).parent / "douyin_videos.json"
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "url": url,
                    "count": count,
                    "links": links,
                    "steps": data.get("steps", []),
                }, f, ensure_ascii=False, indent=2)
            print(f"\n💾 结果已保存到: {output_file}")
        except Exception as e:
            print(f"\n⚠️  保存失败: {e}")
        
        print("\n" + "=" * 60)
        print("✅ 完成！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("\n使用方法:")
    print("  python3 test_fetch_links.py [URL]")
    print("\n示例:")
    print("  python3 test_fetch_links.py")
    print("  python3 test_fetch_links.py https://www.douyin.com")
    print("  python3 test_fetch_links.py https://www.douyin.com/jingxuan?modal_id=123456")
    print()
    
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
