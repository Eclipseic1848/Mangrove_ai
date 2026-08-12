#!/usr/bin/env python3
"""
测试懂车帝提取工具（使用MCP的extract_dcd_by_url工具）
"""

import sys
import json
import asyncio
import os
import traceback
from pathlib import Path
from datetime import datetime

try:
    from mcp.client.stdio import stdio_client
    from mcp import ClientSession, StdioServerParameters
except ImportError:
    print("❌ 错误: 需要安装 mcp 库")
    print("   请运行: pip install mcp")
    sys.exit(1)


def parse_json_result(result):
    """解析MCP返回的JSON结果"""
    if not result:
        return None
    
    if hasattr(result, 'content'):
        result_content = result.content if hasattr(result, 'content') else []
        for item in result_content:
            text = None
            if hasattr(item, 'type') and item.type == 'text':
                text = item.text
            elif isinstance(item, dict) and 'text' in item:
                text = item['text']
            
            if text:
                # 查找JSON代码块
                if '```json' in text:
                    json_start = text.find('```json') + 7
                    json_end = text.find('```', json_start)
                    if json_end != -1:
                        text = text[json_start:json_end].strip()
                
                # 尝试解析JSON
                json_start = text.find('[')
                if json_start == -1:
                    json_start = text.find('{')
                
                if json_start != -1:
                    json_text = text[json_start:]
                    try:
                        return json.loads(json_text)
                    except:
                        pass
    
    return None


async def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("使用方法: python3 test_extract_dcd_mcp.py <URL>")
        print("示例: python3 test_extract_dcd_mcp.py https://www.dongchedi.com/article/123456")
        sys.exit(1)
    
    url = sys.argv[1]
    if not url.startswith('http://') and not url.startswith('https://'):
        print("错误: 请输入有效的URL")
        sys.exit(1)
    
    # 从URL提取文章ID
    import re
    article_id_match = re.search(r'/article/(\d+)', url)
    article_id = article_id_match.group(1) if article_id_match else "article"
    
    # 设置MCP服务器
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
    
    try:
        print("=" * 80)
        print("🚀 开始提取懂车帝文章数据")
        print("=" * 80)
        print(f"URL: {url}")
        print()
        
        # 连接MCP服务器
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                print("      ⏳ 调用 extract_dcd_by_url 工具...")
                
                # 调用提取工具
                result = await session.call_tool("extract_dcd_by_url", {
                    "url": url
                })
                
                # 解析结果
                data = parse_json_result(result)
                
                if data and isinstance(data, list) and len(data) > 0:
                    result_data = data[0]  # 取第一个结果
                    
                    # 保存JSON文件
                    script_dir = Path(__file__).parent
                    json_data_dir = script_dir / "json_data"
                    json_data_dir.mkdir(parents=True, exist_ok=True)
                    
                    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
                    json_file = json_data_dir / f"dongchedi_{article_id}_{timestamp}.json"
                    
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(result_data, f, ensure_ascii=False, indent=2)
                    
                    print(f"\n      ✓ JSON文件已保存: {json_file}")
                    print()
                    print("=" * 80)
                    print("📊 提取结果统计")
                    print("=" * 80)
                    
                    post = result_data.get('post', {})
                    allcomments = result_data.get('allcomments', [])
                    
                    print(f"文章URL: {result_data.get('url', '')}")
                    print(f"提取时间: {result_data.get('extractedAt', '')}")
                    print()
                    print("📝 主贴信息:")
                    print(f"  作者: {post.get('author', '')}")
                    print(f"  发布时间: {post.get('timeRaw', '')}")
                    print(f"  正文长度: {len(post.get('content', ''))} 字符")
                    print(f"  图片数量: {len(post.get('images', []))} 张")
                    print()
                    print("💬 评论信息:")
                    print(f"  评论总数: {len(allcomments)} 条")
                    
                    # 统计回复数
                    total_replies = sum(len(c.get('replies', [])) for c in allcomments)
                    print(f"  回复总数: {total_replies} 条")
                    
                    # 统计图片
                    comment_images = sum(len(c.get('images', [])) for c in allcomments)
                    print(f"  评论图片: {comment_images} 张")
                    
                    print()
                    print("=" * 80)
                    print("✅ 提取完成！")
                    print("=" * 80)
                else:
                    print("      ❌ 提取失败或返回数据格式不正确")
                    if result:
                        print(f"      原始返回: {result}")
        
        print("      ✓ MCP连接已关闭")
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断操作")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        traceback.print_exc()
        sys.exit(1)
