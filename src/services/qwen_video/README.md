# Qwen3.6 视频文字提取服务

基于 HTTP API 的视频文字提取服务，使用 Qwen3.6-35B-A3B 模型从视频画面中提取文字（如字幕、标牌、界面文字等）。

支持局域网部署：客户端和服务端可以在不同的机器上，通过 HTTP 协议通信。

## 功能

- 📹 接收客户端传来的 base64 编码视频
- 🤖 转发给本地 Qwen3.6-35B-A3B 模型，从视频画面中提取文字（字幕、标牌、界面文字、弹幕等）
- 📝 返回按时间顺序整理的文字结果，尽量保持原始文本，不做扩写/总结

## 文件说明

- `http_video_server.py` - HTTP 服务器（用于不同电脑之间的通信）
- `http_video_client.py` - HTTP 客户端（用于不同电脑之间的通信）
- `mcp_video_server.py` - MCP 服务器（用于同一台电脑）
- `mcp_video_client.py` - MCP 客户端（用于同一台电脑）
- `test.py` - 直接调用模型的测试脚本

## 安装依赖

### HTTP 方式（不同电脑）

```bash
pip install fastapi uvicorn requests openai
```

### MCP 方式（同一台电脑）

```bash
pip install mcp openai
```

## 配置

### 环境变量

可以通过环境变量配置模型服务：

```bash
export QWEN_VL_BASE_URL="http://192.168.1.20:6012/v1"
export QWEN_VL_MODEL="Qwen3.6-35B-A3B"
export QWEN_VL_API_KEY="not-needed"
```

### 默认配置

- 模型服务地址：`http://192.168.1.20:6012/v1`
- 模型名称：`Qwen3.6-35B-A3B`
- API 密钥：`not-needed`

### 配置优先级与重启

模型名按 `QWEN_VL_MODEL` → `LLM_MODEL_NAME` → `Qwen3.6-35B-A3B` 解析；模型地址按 `QWEN_VL_BASE_URL` → `LLM_BASE_URL` → `http://192.168.1.20:6012/v1` 解析。因此主项目只配置 `LLM_MODEL_NAME` 与 `LLM_BASE_URL` 也能使用视频服务。

修改上述环境变量或服务源码后，必须重启已运行的 HTTP 服务；MCP 客户端会自动拉起 Server 时，在下一次调用创建的新进程中生效。

## 使用方法

### HTTP 方式（不同电脑，支持局域网）

#### 1. 启动服务器（服务端机器）

```bash
# 在服务端机器上运行
python http_video_server.py

# 或指定监听地址和端口
python http_video_server.py --host 0.0.0.0 --port 8000

# 或使用环境变量
export SERVER_HOST=0.0.0.0
export SERVER_PORT=8000
python http_video_server.py
```

服务器启动后会显示：
```
🚀 Qwen3.6 视频分析 HTTP 服务器
📡 监听地址: 0.0.0.0:8000
🤖 模型服务: http://192.168.1.20:6012/v1
📦 模型名称: Qwen3.6-35B-A3B
```

#### 2. 使用客户端（客户端机器）

```bash
# 基本用法（使用默认问题）
python http_video_client.py ../car.mp4 --server http://192.168.1.100:8000

# 自定义问题
python http_video_client.py ../car.mp4 "这个视频中出现了哪些车辆？" --server http://192.168.1.100:8000

# 使用环境变量指定服务器地址
export SERVER_URL=http://192.168.1.100:8000
python http_video_client.py ../car.mp4
```

### MCP 方式（同一台电脑）

```bash
# 基本用法（使用默认问题）
python mcp_video_client.py ../car.mp4

# 自定义问题
python mcp_video_client.py ../car.mp4 "这个视频中出现了哪些车辆？"
```

#### 在同一台电脑上运行 `mcp_video_client.py` 和 `mcp_video_server.py`

- **推荐方式：直接用客户端脚本，自动拉起 MCP Server**

  在 `qwen-video` 目录下执行，`mcp_video_client.py` 会通过 MCP 的 stdio 协议自动启动 `mcp_video_server.py`，不需要你手动起 server：

  ```bash
  cd /opt/mangrove/qwen-video

  # 安装依赖
  pip install mcp openai

  # 配置模型服务（按需修改为你自己的服务地址/模型）
  export QWEN_VL_BASE_URL="http://192.168.1.20:6012/v1"
  export QWEN_VL_MODEL="Qwen3.6-35B-A3B"
  export QWEN_VL_API_KEY="not-needed"

  # 整体视频一次性处理（默认会按 --slice-seconds 进行时间切片）
  python mcp_video_client.py ../car.mp4

  # 禁用时间切片：一次性整体发送视频
  python mcp_video_client.py ../car.mp4 --slice-seconds 0

  # 长视频按时间切片，例如每 10 秒一段
  python mcp_video_client.py ../car.mp4 --slice-seconds 10
  ```

  内部实现等价于：

  ```python
  from mcp.client.stdio import stdio_client
  from mcp import ClientSession, StdioServerParameters

  server_params = StdioServerParameters(
      command="python3",
      args=["mcp_video_server.py", "--mcp"],
  )

  async with stdio_client(server_params) as (read, write):
      async with ClientSession(read, write) as session:
          await session.initialize()
          await session.call_tool("analyze_video", {...})
  ```

- **方式二：配置到外部 MCP 客户端（如 IDE / Agent）**

  如果你有自己的 MCP 客户端（而不是直接用 `mcp_video_client.py`），可以在 MCP 客户端的配置文件中注册一个名为 `qwen-video-analysis` 的服务器，示例：

  ```json
  {
    "mcpServers": {
      "qwen-video-analysis": {
        "command": "python3",
        "args": [
          "/opt/mangrove/qwen-video/mcp_video_server.py",
          "--mcp"
        ],
        "env": {
          "QWEN_VL_BASE_URL": "http://192.168.1.20:6012/v1",
          "QWEN_VL_MODEL": "Qwen3.6-35B-A3B",
          "QWEN_VL_API_KEY": "not-needed"
        }
      }
    }
  }
  ```

  之后在 MCP 客户端里直接调用工具 `analyze_video`，参数与 HTTP 版的 `/analyze` 接口相同（`base64_video`、`question` 等），但走的是本地 stdio MCP 协议。

### 配置到 MCP 客户端（同一台电脑）

在 MCP 客户端配置文件中添加：

```json
{
  "mcpServers": {
    "qwen-video-analysis": {
      "command": "python3",
      "args": [
        "/path/to/qwen-video/mcp_video_server.py",
        "--mcp"
      ],
      "env": {
        "QWEN_VL_BASE_URL": "http://192.168.1.20:6012/v1",
        "QWEN_VL_MODEL": "Qwen3.6-35B-A3B",
        "QWEN_VL_API_KEY": "not-needed"
      }
    }
  }
}
```

### 直接调用模型（不使用服务器）

```bash
python test.py
```

## API 说明

### HTTP API

#### POST /analyze

从视频中提取文字

**请求体：**

```json
{
  "base64_video": "base64编码的视频字符串",
  "question": "请从视频画面中只提取出现的所有文字，按时间顺序输出，保留原文，不要扩写或总结。",
  "max_tokens": 2048,
  "temperature": 0.7
}
```

**参数：**

- `base64_video` (string, 必需): 视频文件的 base64 编码字符串（不包含 `data:video/mp4;base64,` 前缀）
- `question` (string, 可选): 对文字提取方式的说明，例如是否需要标注时间/位置等。若不提供，默认按时间顺序提取视频画面中的文字。
- `max_tokens` (integer, 可选): 最大生成 token 数，默认 2048
- `temperature` (number, 可选): 温度参数，控制输出的随机性，默认 0.7

**响应：**

```json
{
  "success": true,
  "summary": "视频分析结果文本...",
  "model": "Qwen3.6-35B-A3B",
  "usage": {
    "prompt_tokens": 1234,
    "completion_tokens": 567,
    "total_tokens": 1801
  }
}
```

#### GET /health

健康检查端点

**响应：**

```json
{
  "status": "healthy",
  "model": "Qwen3.6-35B-A3B",
  "model_url": "http://192.168.1.20:6012/v1"
}
```

### MCP API（仅本地）

#### analyze_video 工具

参数和返回格式与 HTTP API 相同

## Python 客户端示例

### HTTP 方式（不同电脑）

```python
import base64
import requests

def analyze_video_http(video_path: str, server_url: str):
    # 读取并编码视频
    with open(video_path, "rb") as f:
        base64_video = base64.b64encode(f.read()).decode('utf-8')
    
    # 发送请求
    response = requests.post(
        f"{server_url}/analyze",
        json={
            "base64_video": base64_video,
            "question": "请总结这个视频的主要内容"
        },
        timeout=300
    )
    
    result = response.json()
    if result["success"]:
        print(result["summary"])
    else:
        print(f"错误: {result['error']}")

# 使用示例
analyze_video_http("car.mp4", "http://192.168.1.100:8000")
```

### MCP 方式（同一台电脑）

```python
import asyncio
import base64
from mcp.client.stdio import stdio_client
from mcp import ClientSession, StdioServerParameters

async def analyze_video_mcp(video_path: str):
    # 读取并编码视频
    with open(video_path, "rb") as f:
        base64_video = base64.b64encode(f.read()).decode('utf-8')
    
    # 连接 MCP 服务器
    server_params = StdioServerParameters(
        command="python3",
        args=["mcp_video_server.py", "--mcp"]
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # 调用工具
            result = await session.call_tool("analyze_video", {
                "base64_video": base64_video,
                "question": "请总结这个视频的主要内容"
            })
            
            print(result.content[0].text)

asyncio.run(analyze_video_mcp("car.mp4"))
```

## 注意事项

1. **视频大小限制**：base64 编码会增加约 33% 的数据量，注意视频文件大小
2. **模型服务**：确保 Qwen3.6-35B-A3B 模型服务正常运行
3. **视频格式**：当前支持 MP4 格式，其他格式可能需要调整 MIME 类型

## 故障排除

### 连接失败

- 检查模型服务地址是否正确
- 确认模型服务是否正常运行
- 检查网络连接

### 分析失败

- 检查视频文件是否损坏
- 确认 base64 编码是否正确
- 查看服务器日志获取详细错误信息
