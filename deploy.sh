#!/bin/bash
# 高并发部署脚本

# 停止旧进程
echo "停止旧进程..."
pkill -f "gunicorn.*zGetContentByXpath"
sleep 2

# 启动新进程
echo "启动服务..."
nohup gunicorn -c gunicorn_config.py zGetContentByXpath:app > gunicorn.log 2>&1 &

# 等待启动
sleep 3

# 检查状态
if pgrep -f "gunicorn.*zGetContentByXpath" > /dev/null; then
    echo "✓ 服务启动成功"
    echo "进程信息:"
    ps aux | grep gunicorn | grep -v grep
else
    echo "✗ 服务启动失败，查看日志:"
    tail -n 20 gunicorn.log
    exit 1
fi
