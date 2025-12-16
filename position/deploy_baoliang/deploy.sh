#!/bin/bash
# 高并发部署脚本 - 多实例模式

# 定义要启动的端口列表
PORTS=(8001 8002)
PROJECT_NAME="zGetContentByXpath"

# 1. 停止旧进程
echo "=== 停止旧进程 ==="
# 匹配 proc_name 或者命令行参数中的端口
pkill -f "gunicorn.*${PROJECT_NAME}"
sleep 2

# 再次检查并强制清理（防止僵尸进程）
if pgrep -f "gunicorn.*${PROJECT_NAME}" > /dev/null; then
    echo "强制清理残留进程..."
    pkill -9 -f "gunicorn.*${PROJECT_NAME}"
    sleep 1
fi

# 2. 循环启动新进程
echo "=== 启动服务 ==="

for PORT in "${PORTS[@]}"; do
    echo "正在启动端口: $PORT ..."
    
    # -b 指定绑定地址和端口
    # -n 指定进程名称（方便grep）
    # --access-logfile / --error-logfile 分离不同端口的日志，方便排查
    nohup gunicorn -c gunicorn_config.py \
        -b 0.0.0.0:${PORT} \
        --access-logfile access_${PORT}.log \
        --error-logfile error_${PORT}.log \
        ${PROJECT_NAME}:app \
        > gunicorn_${PORT}.log 2>&1 &
        
    # 简单的启动间隔，避免瞬间CPU飙升
    sleep 1
done

# 3. 检查状态
sleep 3
echo "=== 进程状态检查 ==="
# 统计启动了多少个worker进程
PROCESS_COUNT=$(ps aux | grep "gunicorn.*${PROJECT_NAME}" | grep -v grep | wc -l)

if [ "$PROCESS_COUNT" -gt 0 ]; then
    echo "✓ 服务启动成功"
    echo "总共运行进程数: $PROCESS_COUNT"
    echo "监听端口:"
    netstat -ntlp | grep python | grep -E "$(IFS=\|; echo "${PORTS[*]}")"
else
    echo "✗ 服务启动失败，请检查 gunicorn_800x.log"
    exit 1
fi