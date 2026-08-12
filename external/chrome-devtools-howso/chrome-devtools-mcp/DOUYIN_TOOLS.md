# 抖音相关 MCP 工具文档

本文档说明抖音相关的 MCP 工具，这些工具定义在 `src/tools/script.ts` 中。

## 工具位置

- **定义文件**: `src/tools/script.ts`
- **注册方式**: 通过 `Object.values(scriptTools)` 自动注册到 `src/tools/tools.ts`
- **工具类别**: `ToolCategory.DEBUGGING`

## 工具列表

### fetch_douyin_video_links

一键获取抖音视频链接。该工具整合了导航、网络劫持、等待、自动播放、链接提取等所有功能。

**工具名称**: `fetch_douyin_video_links`

**参数**:
- `url` (string, 必需): 要打开的抖音页面 URL
- `initialWaitMs` (number, 可选): 导航/注入后的初始等待时间（毫秒），默认 8000ms
- `playWaitMs` (number, 可选): 自动播放后的等待时间（毫秒），默认 5000ms
- `networkLimit` (number, 可选): 网络请求回退时检查的最近请求数量，默认 20
- `includeAllVideos` (boolean, 可选): 是否包含所有视频元素（不仅限于正在播放的），默认 true

**返回**:
```json
{
  "url": "https://www.douyin.com/...",
  "elapsedMs": 15000,
  "totalVideos": 3,
  "links": [
    "https://douyinvod.com/..."
  ],
  "count": 1,
  "steps": [
    {"name": "navigate", "ok": true},
    {"name": "inject_network_hook", "ok": true},
    {"name": "initial_wait", "ok": true},
    {"name": "autoplay", "ok": true},
    {"name": "play_wait", "ok": true},
    {"name": "extract_from_dom", "ok": true}
  ],
  "network": null
}
```

**使用示例**:
```python
# 基本使用
result = await session.call_tool(
    "fetch_douyin_video_links",
    {
        "url": "https://www.douyin.com/jingxuan?modal_id=123456"
    }
)

# 自定义参数
result = await session.call_tool(
    "fetch_douyin_video_links",
    {
        "url": "https://www.douyin.com",
        "initialWaitMs": 10000,
        "playWaitMs": 8000,
        "networkLimit": 30,
        "includeAllVideos": True
    }
)
```

**定义**: `fetchDouyinVideoLinks` in `script.ts`

**功能说明**:
1. **可选导航**: 如果提供了 `url`，会先导航到该页面
2. **注入网络劫持**: 自动注入网络拦截脚本，捕获包含 `douyinvod.com` 的请求
3. **等待页面加载**: 等待指定时间让页面和视频加载
4. **自动播放**: 尝试自动播放页面上的所有视频
5. **提取链接**: 优先从 DOM 中的 `<video>` 元素提取链接
6. **网络回退**: 如果 DOM 中没有找到链接，从网络请求中获取

**优势**:
- **一键完成**: 只需一次工具调用即可完成所有操作
- **自动回退**: 如果一种方法失败，自动尝试其他方法
- **详细反馈**: 返回每个步骤的执行情况，便于调试
- **灵活配置**: 支持自定义等待时间和参数

## 工具注册

所有工具通过以下方式自动注册：

1. 在 `src/tools/script.ts` 中使用 `export const` 导出工具定义
2. 在 `src/tools/tools.ts` 中通过 `...Object.values(scriptTools)` 导入
3. 工具按名称排序后注册到 MCP 服务器

## 编译和测试

### 编译

```bash
cd chrome-devtools-mcp
npm run build
```

### 测试工具

```bash
# 使用测试脚本
python3 scripts/test_fetch_links.py

# 或指定 URL
python3 scripts/test_fetch_links.py "https://www.douyin.com"
```

## 注意事项

1. 工具返回的数据格式为 JSON，需要通过解析 `result.content` 获取
2. 返回的 `links` 数组包含去重后的视频链接
3. `steps` 数组显示每个步骤的执行情况，可用于调试
4. 如果 `count` 为 0，说明未找到视频链接，可能需要：
   - 增加等待时间（`initialWaitMs` 或 `playWaitMs`）
   - 检查页面是否正确加载
   - 手动播放视频后再调用工具

### download_douyin_video

下载抖音视频文件。

**工具名称**: `download_douyin_video`

**参数**:
- `url` (string, 必需): 视频直链 URL（例如：`https://v26-web.douyinvod.com/...`）
- `filePath` (string, 可选): 保存文件的路径。如果省略，会自动从 URL 中提取视频 ID 作为文件名
- `referer` (string, 可选): Referer 请求头，默认 `https://www.douyin.com`

**返回**:
```json
{
  "success": true,
  "filename": "douyin_video_7592579306938811875.mp4",
  "fileSize": 12345678,
  "fileSizeMB": "11.77",
  "url": "https://v26-web.douyinvod.com/..."
}
```

**使用示例**:
```python
# 基本使用（自动生成文件名）
result = await session.call_tool(
    "download_douyin_video",
    {
        "url": "https://v26-web.douyinvod.com/..."
    }
)

# 指定输出文件名
result = await session.call_tool(
    "download_douyin_video",
    {
        "url": "https://v26-web.douyinvod.com/...",
        "filePath": "my_video.mp4"
    }
)

# 自定义 Referer
result = await session.call_tool(
    "download_douyin_video",
    {
        "url": "https://v26-web.douyinvod.com/...",
        "filePath": "video.mp4",
        "referer": "https://www.douyin.com"
    }
)
```

**定义**: `downloadDouyinVideo` in `script.ts`

**功能说明**:
1. 使用 Node.js `fetch` API 下载视频
2. 自动设置必要的请求头（Referer、User-Agent）
3. 自动从 URL 提取视频 ID 作为文件名（如果未指定）
4. 使用 `context.saveFile` 保存文件
5. 返回下载结果（文件名、大小等）

---

## 规范结果约定（与 Python 端一致，避免 file_path/file_name 为 null）

凡会落盘并需向 Python 返回文件路径的工具，应在响应**末尾**输出一行：

```
MCP_TOOL_RESULT:{"success":true,"file_path":"/abs/path/to/file.mp4","file_name":"file.mp4"}
```

- 前缀固定为 `MCP_TOOL_RESULT:`（与 `src/tools/mcp/chrome_devtools.py` 中 `MCP_TOOL_RESULT_PREFIX` 一致）。
- 后面为**单行** JSON，建议字段：`success` (bool)、`file_path` (str|null)、`file_name` (str|null)。
- 失败或无文件时：`MCP_TOOL_RESULT:{"success":false,"file_path":null,"file_name":null}`。

Python 端会优先用 `parse_mcp_tool_result()` 解析该行，再回退到正则/非结构化解析。新增「保存文件并返回路径」类工具时请遵循此约定。

---

## 迁移说明

**已废弃的工具**（已被 `fetch_douyin_video_links` 整合）:
- ~~`wait_for_douyin_page_load`~~ - 已整合到 `fetch_douyin_video_links`
- ~~`inject_douyin_network_hook`~~ - 已整合到 `fetch_douyin_video_links`
- ~~`auto_play_douyin_videos`~~ - 已整合到 `fetch_douyin_video_links`
- ~~`get_douyin_video_urls`~~ - 已整合到 `fetch_douyin_video_links`
- ~~`get_douyin_video_urls_from_network`~~ - 已整合到 `fetch_douyin_video_links`

**推荐使用**:
- `fetch_douyin_video_links` - 一键获取视频链接
- `download_douyin_video` - 下载视频文件
