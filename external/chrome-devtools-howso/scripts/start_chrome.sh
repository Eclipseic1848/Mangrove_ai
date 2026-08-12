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
    CHROME_CMD="google-chrome"
elif command -v chromium &> /dev/null; then
    chromium \
        --remote-debugging-port=$CHROME_PORT \
        --user-data-dir=$USER_DATA_DIR \
        --no-first-run \
        --no-default-browser-check \
        > /dev/null 2>&1 &
    CHROME_CMD="chromium"
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
