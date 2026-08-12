# Scripts 目录说明

本目录包含与**汽车之家**、**懂车帝**和**抖音**相关的数据提取和处理脚本。

## 目录结构

### 汽车之家相关脚本

- `qichezhijia_simple.py` - 汽车之家简单提取脚本
- `qichezhijia-zhutie.py` - 汽车之家主贴提取脚本

### 懂车帝相关脚本

- `test_extract_dcd_mcp.py` - 懂车帝提取工具（使用MCP的extract_dcd_by_url工具）

### 抖音相关脚本

- `download_douyin_video.py` - 下载抖音视频
- `test_download_video.py` - 测试下载抖音视频
- `test_fetch_links.py` - 测试获取抖音视频链接
- `test_search_and_click.py` - 测试抖音搜索页面交互
- `douyin_videos.json` - 抖音视频数据（示例）
- `USE_DOWNLOAD_TOOL.md` - 抖音下载工具使用文档

### 通用工具和文档

- `list_mcp_tools.py` - 列出所有可用的MCP工具
- `README_LIST_TOOLS.md` - MCP工具列表文档
- `README_MCP_DEMO.md` - MCP Demo使用文档
- `START_CHROME.md` - Chrome浏览器启动说明
- `start_chrome.sh` - Chrome启动脚本
- `mcp_tools_from_source.json` - MCP工具源数据（JSON格式）
- `UPGRADE_NODE.md` - Node.js升级文档

### 数据目录

- `json_data/` - 提取的JSON数据文件
- `html_data/` - 保存的HTML快照文件

## 快速开始

### 1. 启动Chrome（调试模式）

```bash
bash scripts/start_chrome.sh
# 或
google-chrome --remote-debugging-port=9222
```

### 2. 提取懂车帝文章

```bash
python3 scripts/test_extract_dcd_mcp.py https://www.dongchedi.com/article/123456
```

### 3. 提取汽车之家帖子

```bash
python3 scripts/qichezhijia_simple.py <URL>
# 或
python3 scripts/qichezhijia-zhutie.py <URL>
```

### 4. 下载抖音视频

```bash
python3 scripts/test_download_video.py <视频URL>
```

## 依赖安装

```bash
# Python依赖
pip install mcp playwright beautifulsoup4

# Node.js依赖（用于MCP服务器）
cd chrome-devtools-mcp
npm install
npm run build
```

## 注意事项

1. **Chrome必须运行**：所有脚本都需要Chrome在调试模式下运行（端口9222）
2. **网络连接**：需要能够访问目标网站
3. **提取时间**：根据内容数量，提取可能需要几分钟时间
4. **数据保存**：提取的数据会自动保存到 `json_data/` 目录

## 相关文档

- [抖音下载工具说明](./USE_DOWNLOAD_TOOL.md)
- MCP 工具列表：运行 `python list_mcp_tools.py` 获取当前实际工具清单。
- [Chrome启动说明](./START_CHROME.md)
