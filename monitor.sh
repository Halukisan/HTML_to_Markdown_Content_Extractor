#!/bin/bash
# 服务监控脚本

echo "=== zGetContentByXpath 服务监控 ==="
echo ""

# 检查进程
echo "1. 进程状态:"
if pgrep -f "gunicorn.*zGetContentByXpath" > /dev/null; then
    echo "   ✓ 服务运行中"
    ps aux | grep gunicorn | grep -v grep | awk '{print "   PID: "$2" CPU: "$3"% MEM: "$4"% CMD: "$11" "$12" "$13}'
else
    echo "   ✗ 服务未运行"
fi

echo ""
echo "2. 端口监听:"
netstat -tlnp 2>/dev/null | grep :8000 || ss -tlnp | grep :8000

echo ""
echo "3. 最近错误日志 (最后10行):"
if [ -f "error.log" ]; then
    tail -n 10 error.log
else
    echo "   无错误日志"
fi

echo ""
echo "4. 系统资源:"
echo "   CPU使用率:"
top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print "   空闲: "$1"%"}'
echo "   内存使用:"
free -h | grep Mem | awk '{print "   总计: "$2" 已用: "$3" 可用: "$7}'

echo ""
echo "5. 连接数统计:"
netstat -an 2>/dev/null | grep :8000 | wc -l | awk '{print "   当前连接数: "$1}'
