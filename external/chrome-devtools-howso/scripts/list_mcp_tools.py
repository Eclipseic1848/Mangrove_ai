#!/usr/bin/env python3
"""
列出所有 MCP 工具及其描述
"""

import sys
import json
import asyncio
from pathlib import Path
from collections import defaultdict

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


async def main():
    """主函数"""
    import os
    from pathlib import Path
    
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
    
    print("=" * 80)
    print("📋 MCP 工具列表")
    print("=" * 80)
    print("\n1️⃣ 连接 MCP Server ...")
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("✓ 已连接\n")
                await list_tools(session)
    except NameError:
        transport = StdioStdoutServerTransport(server_params)
        async with ClientSession(transport) as session:
            await session.initialize()
            print("✓ 已连接\n")
            await list_tools(session)


async def list_tools(session):
    """列出所有工具"""
    try:
        # 使用 MCP 协议的标准方法获取工具列表
        # 尝试不同的方法名（不同版本的 SDK 可能不同）
        tools = None
        result = None
        
        # 方法1: list_tools (Python SDK 可能使用这个)
        if hasattr(session, 'list_tools'):
            try:
                result = await session.list_tools()
            except Exception as e:
                print(f"⚠️  list_tools() 失败: {e}")
        
        # 方法2: listTools (JavaScript/TypeScript 风格)
        if not result and hasattr(session, 'listTools'):
            try:
                result = await session.listTools()
            except Exception as e:
                print(f"⚠️  listTools() 失败: {e}")
        
        # 处理结果
        if result:
            if hasattr(result, 'tools'):
                tools = result.tools
            elif isinstance(result, dict) and 'tools' in result:
                tools = result['tools']
            elif isinstance(result, list):
                tools = result
            else:
                # 尝试直接访问属性
                tools = result
        
        if not tools:
            print("❌ 无法获取工具列表")
            print(f"结果类型: {type(result)}")
            print(f"结果内容: {result}")
            print("\n💡 提示：查看源代码中的工具定义")
            print("  位置: chrome-devtools-mcp/src/tools/")
            return
        
        if not tools:
            print("⚠️  未找到任何工具")
            return
        
        # 按类别分组
        categories = defaultdict(list)
        uncategorized = []
        
        for tool in tools:
            # 获取工具信息
            name = tool.get('name', 'unknown') if isinstance(tool, dict) else getattr(tool, 'name', 'unknown')
            description = tool.get('description', '无描述') if isinstance(tool, dict) else getattr(tool, 'description', '无描述')
            
            # 尝试获取类别
            category = None
            if isinstance(tool, dict):
                if 'annotations' in tool and isinstance(tool['annotations'], dict):
                    category = tool['annotations'].get('category')
            elif hasattr(tool, 'annotations'):
                category = getattr(tool.annotations, 'category', None) if hasattr(tool, 'annotations') else None
            
            # 获取参数信息
            params = {}
            if isinstance(tool, dict):
                if 'inputSchema' in tool:
                    schema = tool['inputSchema']
                    if isinstance(schema, dict) and 'properties' in schema:
                        params = schema['properties']
                elif 'schema' in tool:
                    schema = tool['schema']
                    if isinstance(schema, dict) and 'properties' in schema:
                        params = schema['properties']
            
            tool_info = {
                'name': name,
                'description': description,
                'params': params,
            }
            
            if category:
                categories[category].append(tool_info)
            else:
                uncategorized.append(tool_info)
        
        # 显示结果
        print(f"找到 {len(tools)} 个工具\n")
        
        # 显示分类的工具
        if categories:
            for category, tool_list in sorted(categories.items()):
                print(f"\n{'=' * 80}")
                print(f"📁 {category} ({len(tool_list)} 个工具)")
                print(f"{'=' * 80}")
                for i, tool in enumerate(tool_list, 1):
                    print(f"\n{i}. {tool['name']}")
                    print(f"   描述: {tool['description']}")
                    if tool['params']:
                        print(f"   参数:")
                        for param_name, param_info in tool['params'].items():
                            required = param_info.get('required', False) if isinstance(param_info, dict) else False
                            param_desc = param_info.get('description', '无描述') if isinstance(param_info, dict) else str(param_info)
                            req_mark = " (必需)" if required else " (可选)"
                            print(f"     - {param_name}{req_mark}: {param_desc}")
        
        # 显示未分类的工具
        if uncategorized:
            print(f"\n{'=' * 80}")
            print(f"📁 未分类 ({len(uncategorized)} 个工具)")
            print(f"{'=' * 80}")
            for i, tool in enumerate(uncategorized, 1):
                print(f"\n{i}. {tool['name']}")
                print(f"   描述: {tool['description']}")
                if tool['params']:
                    print(f"   参数:")
                    for param_name, param_info in tool['params'].items():
                        required = param_info.get('required', False) if isinstance(param_info, dict) else False
                        param_desc = param_info.get('description', '无描述') if isinstance(param_info, dict) else str(param_info)
                        req_mark = " (必需)" if required else " (可选)"
                        print(f"     - {param_name}{req_mark}: {param_desc}")
        
        # 保存到文件
        output_file = Path(__file__).parent / "mcp_tools_list.json"
        try:
            all_tools_data = []
            for category, tool_list in categories.items():
                for tool in tool_list:
                    all_tools_data.append({
                        'name': tool['name'],
                        'description': tool['description'],
                        'category': category,
                        'params': tool['params'],
                    })
            for tool in uncategorized:
                all_tools_data.append({
                    'name': tool['name'],
                    'description': tool['description'],
                    'category': None,
                    'params': tool['params'],
                })
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'total': len(tools),
                    'categories': {cat: len(tools) for cat, tools in categories.items()},
                    'tools': all_tools_data,
                }, f, ensure_ascii=False, indent=2)
            print(f"\n💾 工具列表已保存到: {output_file}")
        except Exception as e:
            print(f"\n⚠️  保存失败: {e}")
        
        # 特别显示抖音相关工具
        douyin_tools = [t for t in tools if 'douyin' in (t.get('name', '') if isinstance(t, dict) else getattr(t, 'name', '')).lower()]
        if douyin_tools:
            print(f"\n{'=' * 80}")
            print(f"🎬 抖音相关工具 ({len(douyin_tools)} 个)")
            print(f"{'=' * 80}")
            for i, tool in enumerate(douyin_tools, 1):
                name = tool.get('name', 'unknown') if isinstance(tool, dict) else getattr(tool, 'name', 'unknown')
                desc = tool.get('description', '无描述') if isinstance(tool, dict) else getattr(tool, 'description', '无描述')
                print(f"\n{i}. {name}")
                print(f"   {desc}")
        
        print("\n" + "=" * 80)
        print("✅ 完成！")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        
        # 如果无法通过 API 获取，提供替代方案
        print("\n" + "=" * 80)
        print("💡 替代方案：查看源代码中的工具定义")
        print("=" * 80)
        print("\n工具定义位置:")
        print("  - chrome-devtools-mcp/src/tools/script.ts (抖音工具)")
        print("  - chrome-devtools-mcp/src/tools/pages.ts (页面工具)")
        print("  - chrome-devtools-mcp/src/tools/input.ts (输入工具)")
        print("  - chrome-devtools-mcp/src/tools/network.ts (网络工具)")
        print("  - chrome-devtools-mcp/src/tools/console.ts (控制台工具)")
        print("  - chrome-devtools-mcp/src/tools/snapshot.ts (快照工具)")
        print("  - chrome-devtools-mcp/src/tools/performance.ts (性能工具)")
        print("  - chrome-devtools-mcp/src/tools/emulation.ts (模拟工具)")
        print("\n文档位置:")
        print("  - chrome-devtools-mcp/docs/tool-reference.md")
        print("  - chrome-devtools-mcp/DOUYIN_TOOLS.md (抖音工具专用)")


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
