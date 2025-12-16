#!/bin/bash

# === 停止旧的 zprogress.py 进程（不依赖端口，直接按命令杀）===
echo "正在停止旧的 zprogress.py 进程..."
pkill -f "zprogress.py" 2>/dev/null
sleep 1

# 如果还有残留，强制 kill -9
if pgrep -f "zprogress.py" > /dev/null; then
    echo "强制杀死残留进程..."
    pkill -9 -f "zprogress.py" 2>/dev/null
    sleep 1
fi

# === 启动新服务（后台运行）===
echo "正在后台启动服务..."

# 使用 nohup + & 实现后台运行，并捕获 PID
nohup python3 zprogress.py > zprogress.log 2>&1 &
PID=$!

# 等待1秒让进程稳定
sleep 1

# 检查进程是否还在运行（即是否启动成功）
if ! kill -0 "$PID" 2>/dev/null; then
    echo "启动失败！进程已崩溃。" >&2
    exit 1
fi

echo "✅ 服务已在后台启动，PID: $PID，日志：zprogress.log"

#!/bin/bash

# === 停止旧的 zGetContentByXpath.py 进程（不依赖端口，直接按命令杀）===
echo "正在停止旧的 zGetContentByXpath.py 进程..."
pkill -f "zGetContentByXpath.py" 2>/dev/null
sleep 1

# 如果还有残留，强制 kill -9
if pgrep -f "zGetContentByXpath.py" > /dev/null; then
    echo "强制杀死残留进程..."
    pkill -9 -f "zGetContentByXpath.py" 2>/dev/null
    sleep 1
fi

# === 启动新服务（后台运行）===
echo "正在后台启动服务..."

# 使用 nohup + & 实现后台运行，并捕获 PID
nohup python3 zGetContentByXpath.py api > zGetContentByXpath.log 2>&1 &
PID=$!

# 等待1秒让进程稳定
sleep 1

# 检查进程是否还在运行（即是否启动成功）
if ! kill -0 "$PID" 2>/dev/null; then
    echo "启动失败！进程已崩溃。" >&2
    exit 1
fi

echo "✅ 服务已在后台启动，PID: $PID，日志：zGetContentByXpath.log"