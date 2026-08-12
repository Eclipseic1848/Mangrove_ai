# 启动 Chrome 浏览器命令

用于 MCP 工具调试的 Chrome 启动命令。

## 基本命令（Linux）

### 方法 1: 使用 google-chrome（推荐）

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-profile-stable
```

### 方法 2: 使用 chromium

```bash
chromium --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-profile-stable
```

### 方法 3: 使用完整路径

```bash
/usr/bin/google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-profile-stable
```

## 其他操作系统

### macOS

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-profile-stable
```

### Windows

```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%TEMP%\chrome-profile-stable"
```

## 参数说明

- `--remote-debugging-port=9222`: 启用远程调试端口（MCP 工具通过此端口连接）
- `--user-data-dir=/tmp/chrome-profile-stable`: 使用独立的用户数据目录（安全考虑，避免暴露你的正常浏览数据）

## 完整启动脚本

创建一个启动脚本 `start_chrome.sh`：

```bash
#!/bin/bash

# 关闭已运行的 Chrome 实例（可选）
pkill -f "chrome.*remote-debugging-port" || true

# 启动 Chrome
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-profile-stable \
  --no-first-run \
  --no-default-browser-check \
  > /dev/null 2>&1 &

echo "Chrome 已启动，调试端口: 9222"
echo "等待 3 秒后可以运行 Python 脚本..."
sleep 3
```

使用：

```bash
chmod +x start_chrome.sh
./start_chrome.sh
```

## 验证 Chrome 是否启动成功

### 方法 1: 检查端口

```bash
netstat -tlnp | grep 9222
# 或
ss -tlnp | grep 9222
```

应该看到类似输出：
```
tcp  0  0  127.0.0.1:9222  0.0.0.0:*  LISTEN  <pid>/chrome
```

### 方法 2: 访问调试页面

在浏览器中打开：
```
http://localhost:9222/json
```

如果看到 JSON 格式的页面信息，说明 Chrome 调试端口已启用。

### 方法 3: 使用 curl 测试

```bash
curl http://localhost:9222/json | head -20
```

## 常见问题

### 问题 1: 端口已被占用

**错误信息**：
```
Address already in use
```

**解决方法**：
```bash
# 查找占用端口的进程
lsof -i :9222
# 或
fuser 9222/tcp

# 关闭进程
kill -9 <PID>
```

### 问题 2: Chrome 无法启动

**解决方法**：
1. 确保已安装 Chrome/Chromium
2. 检查是否有其他 Chrome 实例在运行：
   ```bash
   pkill chrome
   ```
3. 尝试使用完整路径启动

### 问题 3: 权限问题

**解决方法**：
```bash
# 确保脚本有执行权限
chmod +x start_chrome.sh

# 或直接运行命令
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-profile-stable
```

## 一键启动脚本（完整版）

创建 `scripts/start_chrome.sh`：

```bash
#!/bin/bash

CHROME_PORT=9222
USER_DATA_DIR="/tmp/chrome-profile-stable"

echo "=========================================="
echo "🚀 启动 Chrome（调试模式）"
echo "=========================================="

# 检查 Chrome 是否已运行
if pgrep -f "chrome.*remote-debugging-port=$CHROME_PORT" > /dev/null; then
    echo "⚠️  Chrome 已在运行（端口 $CHROME_PORT）"
    read -p "是否关闭并重新启动? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "关闭现有 Chrome 实例..."
        pkill -f "chrome.*remote-debugging-port=$CHROME_PORT"
        sleep 2
    else
        echo "使用现有 Chrome 实例"
        exit 0
    fi
fi

# 检查端口是否被占用
if lsof -Pi :$CHROME_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "❌ 端口 $CHROME_PORT 已被占用"
    echo "请先关闭占用该端口的程序"
    exit 1
fi

# 启动 Chrome
echo "启动 Chrome..."
if command -v google-chrome &> /dev/null; then
    google-chrome \
        --remote-debugging-port=$CHROME_PORT \
        --user-data-dir=$USER_DATA_DIR \
        --no-first-run \
        --no-default-browser-check \
        > /dev/null 2>&1 &
elif command -v chromium &> /dev/null; then
    chromium \
        --remote-debugging-port=$CHROME_PORT \
        --user-data-dir=$USER_DATA_DIR \
        --no-first-run \
        --no-default-browser-check \
        > /dev/null 2>&1 &
else
    echo "❌ 未找到 Chrome 或 Chromium"
    exit 1
fi

# 等待 Chrome 启动
echo "等待 Chrome 启动..."
sleep 3

# 验证
if curl -s http://localhost:$CHROME_PORT/json > /dev/null 2>&1; then
    echo "✅ Chrome 已成功启动"
    echo "   调试端口: $CHROME_PORT"
    echo "   用户数据目录: $USER_DATA_DIR"
    echo ""
    echo "现在可以运行 Python 脚本："
    echo "   python3 scripts/test_fetch_links.py"
else
    echo "❌ Chrome 启动失败，请检查错误信息"
    exit 1
fi
```

使用：

```bash
chmod +x scripts/start_chrome.sh
./scripts/start_chrome.sh
```

## 停止 Chrome

```bash
# 方法 1: 通过进程名
pkill -f "chrome.*remote-debugging-port=9222"

# 方法 2: 通过端口
fuser -k 9222/tcp

# 方法 3: 查找并关闭
lsof -ti:9222 | xargs kill -9
```

## 注意事项

⚠️ **安全警告**：
- 启用远程调试端口后，任何应用程序都可以连接到浏览器并控制它
- 不要在调试模式下浏览敏感网站
- 使用独立的用户数据目录（`--user-data-dir`）可以保护你的正常浏览数据

✅ **最佳实践**：
- 使用独立的用户数据目录
- 调试完成后关闭 Chrome
- 不要在生产环境或包含敏感数据的浏览器上启用调试端口
