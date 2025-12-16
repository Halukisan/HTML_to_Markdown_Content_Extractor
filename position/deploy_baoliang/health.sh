#!/bin/bash
# health_check.sh - 检查指定端口的服务状态

PORT=$1
HEALTH_CHECK_URL="http://127.0.0.1:${PORT}/health"

# 检查服务是否健康
response=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 $HEALTH_CHECK_URL)

if [ "$response" = "200" ]; then
    echo "✅ 端口 $PORT 服务正常 (HTTP $response)"
    exit 0
else
    echo "❌ 端口 $PORT 服务异常 (HTTP $response)"
    exit 1
fi