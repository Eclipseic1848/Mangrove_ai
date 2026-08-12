# 使用 download_douyin_video MCP 工具

## 工具说明

`download_douyin_video` 是一个 MCP 工具，用于直接下载抖音视频文件。

## 使用方法

### 方法 1: 使用 Python 脚本（推荐）

```bash
# 基本使用（自动生成文件名）
python3 scripts/test_download_video.py "https://v26-web.douyinvod.com/..."

# 指定输出文件名
python3 scripts/test_download_video.py "https://v26-web.douyinvod.com/..." video.mp4
```

### 方法 2: 在 Python 代码中调用

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def download_video():
    server_params = StdioServerParameters(
        command="node",
        args=["chrome-devtools-mcp/build/src/index.js", "--browser-url=http://127.0.0.1:9222"]
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # 调用下载工具
            result = await session.call_tool(
                "download_douyin_video",
                {
                    "url": "https://v26-web.douyinvod.com/...",
                    "filePath": "video.mp4"  # 可选
                }
            )
            
            print(result)

asyncio.run(download_video())
```

### 方法 3: 完整流程（获取链接 + 下载）

```python
# 1. 先获取视频链接
links_result = await session.call_tool(
    "fetch_douyin_video_links",
    {"url": "https://www.douyin.com/..."}
)

# 解析链接
links_data = parse_mcp_result(links_result)
video_url = links_data["links"][0]  # 取第一个链接

# 2. 下载视频
download_result = await session.call_tool(
    "download_douyin_video",
    {
        "url": video_url,
        "filePath": "downloaded_video.mp4"
    }
)
```

## 参数说明

- **`url`** (必需): 视频直链 URL
  - 示例: `https://v26-web.douyinvod.com/9d6826f34c65ac344a85d0c108e1fcc4/...`
  
- **`filePath`** (可选): 输出文件路径
  - 如果省略，会自动从 URL 的 `__vid` 参数提取视频 ID 作为文件名
  - 示例: `video.mp4` 或 `videos/my_video.mp4`

- **`referer`** (可选): Referer 请求头
  - 默认: `https://www.douyin.com`
  - 一般不需要修改

## 返回结果

成功时返回：
```json
{
  "success": true,
  "filename": "douyin_video_7592579306938811875.mp4",
  "fileSize": 12345678,
  "fileSizeMB": "11.77",
  "url": "https://v26-web.douyinvod.com/..."
}
```

## 完整示例

```python
#!/usr/bin/env python3
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    # 1. 连接 MCP 服务器
    server_params = StdioServerParameters(
        command="node",
        args=["chrome-devtools-mcp/build/src/index.js", "--browser-url=http://127.0.0.1:9222"]
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # 2. 下载视频
            video_url = "https://v26-web.douyinvod.com/9d6826f34c65ac344a85d0c108e1fcc4/69773984/video/tos/cn/tos-cn-ve-15/ok8r6pXKfABLgnDRAQXeJuIgUF97kXA7SDEQAS/?a=6383&br=799&bt=799&btag=c0000e00030000&cd=0%7C0%7C0%7C3&ch=26&cquery=100B_100x_100z_100o_100w&cr=3&cs=0&cv=1&dr=0&ds=6&dy_q=1769410084&feature_id=0ea98fd3bdc3c6c14a3d0804cc272721&ft=pEaFx4hZffPdr5~-v1jNvAq-antLjrKqqiJnRkaG4zstejVhWL6&is_ssr=1&l=202601261448036E1CC90E9CC3DA9DE3A5&lr=all&mime_type=video_mp4&qs=12&rc=OTM5ZDQ1ODZnMzo1aTw5OUBpang7bm85cm43ODMzNGkzM0BjNjAuXmIuNmExNTQzLTJgYSMxZnFvMmRjMWNhLS1kLTBzcw%3D%3D&__vid=7592579306938811875"
            
            result = await session.call_tool(
                "download_douyin_video",
                {
                    "url": video_url,
                    "filePath": "my_video.mp4"
                }
            )
            
            print("下载结果:", result)

if __name__ == "__main__":
    asyncio.run(main())
```

## 注意事项

1. **需要先编译**: 确保已运行 `cd chrome-devtools-mcp && npm run build`
2. **Chrome 调试端口**: 虽然下载工具不需要浏览器，但 MCP 服务器仍需要 Chrome 在调试模式运行
3. **文件路径**: 可以使用相对路径或绝对路径
4. **自动命名**: 如果不指定 `filePath`，工具会从 URL 的 `__vid` 参数提取视频 ID 作为文件名

## 与命令行下载的区别

| 特性 | MCP 工具 | curl/wget |
|------|---------|-----------|
| 调用方式 | Python 代码调用 | 命令行 |
| 集成性 | 可集成到自动化流程 | 独立命令 |
| 错误处理 | 结构化错误信息 | 基础错误提示 |
| 进度显示 | 通过 MCP 响应 | 命令行进度条 |
| 适用场景 | 自动化脚本 | 手动下载 |
