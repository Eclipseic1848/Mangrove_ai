# VOC处理MCP服务器使用说明

## 概述
此文件只适用于处理懂车帝和汽车之家的主贴数据，不包含两个平台的视频数据。
`main_mcp.py` 将VOC用户声音处理功能暴露为MCP（Model Context Protocol）服务器，可通过 MCP 协议调用。

## 安装依赖

```bash
pip install mcp
```

或者安装所有依赖：

```bash
pip install -r requirements.txt
```

## 使用方法

### 1. 作为MCP服务器运行

启动MCP服务器（通过stdio通信）：

```bash
# 方式一：从项目根目录以模块方式运行（推荐）
python -m src.services.voc_processor.main_mcp

# 方式二：从 voc_processor 目录直接运行
cd src/services/voc_processor && python main_mcp.py
```

### 2. 命令行模式（备用）

若 MCP 库未安装，可使用命令行模式（先过滤再分析，一步完成）：

```bash
python -m src.services.voc_processor.main_mcp <input_json_file> [output_json_file]
```

示例：

```bash
python -m src.services.voc_processor.main_mcp data/input.json
python -m src.services.voc_processor.main_mcp data/input.json data/output.json
```

## MCP工具说明

MCP 服务器提供两个独立工具，便于调用方按需组合：

### 工具一：`filter_voc`

仅对 VOC 用户声音 JSON 进行内容筛选，返回是否需要进一步分析。

**参数：**
- `input_file` (必需): 输入 JSON 文件路径

**返回值：**
```json
{
  "success": true,
  "message": "筛选完成",
  "input_file": "输入文件路径",
  "need_analysis": true/false,
  "filter_result": {
    "result": "是/否",
    "analysis": "LLM 分析说明"
  }
}
```

若清洗后文本为空，则返回 `need_analysis: false`，`filter_result: null`。

### 工具二：`analyze_voc`

对已通过筛选的 VOC JSON 进行内容解析与标签提取。

**参数：**
- `input_file` (必需): 输入 JSON 文件路径
- `output_file` (可选): 输出 JSON 文件路径，默认使用输入文件名加 `_analyzed` 后缀

**返回值：**
```json
{
  "success": true/false,
  "message": "解析完成 / 解析处理失败",
  "input_file": "输入文件路径",
  "output_file": "输出文件路径（成功时）"
}
```

**使用方式：**
- `filter_voc` 与 `analyze_voc` 为独立工具，可单独或组合使用
- 可直接调用 `analyze_voc` 进行解析；或先调用 `filter_voc` 判断 `need_analysis`，再决定是否调用 `analyze_voc`

> 说明：Agent 内置 `browser_filter_voc`、`browser_analyze_voc` 两个独立工具，与 MCP 的 filter_voc、analyze_voc 对应。

## 配置MCP客户端

在 MCP 客户端配置中添加：

```json
{
  "mcpServers": {
    "voc-processor": {
      "command": "python",
      "args": ["-m", "src.services.voc_processor.main_mcp"]
    }
  }
}
```

或使用脚本绝对路径：

```json
{
  "mcpServers": {
    "voc-processor": {
      "command": "python",
      "args": ["/path/to/project/src/services/voc_processor/main_mcp.py"]
    }
  }
}
```

## 注意事项

- MCP 服务器使用 stdio 进行通信
- 分析链和 RAG 映射规则会被缓存，提高性能
- `filter_voc` 与 `analyze_voc` 为独立工具，调用方需自行决定是否在筛选通过后调用分析
