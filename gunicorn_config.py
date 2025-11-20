# Gunicorn配置文件 - 高并发优化
import multiprocessing

# 服务器绑定
bind = "0.0.0.0:8000"

# Worker配置 - 根据CPU核心数调整
workers = multiprocessing.cpu_count() * 2 + 1  # 推荐公式
worker_class = "uvicorn.workers.UvicornWorker"

# 并发配置
worker_connections = 1500  # 每个worker的最大并发连接数
max_requests = 2000  # worker处理多少请求后重启（防止内存泄漏）
max_requests_jitter = 200  # 随机抖动，避免所有worker同时重启

# 超时配置
timeout = 120  # 请求超时时间（秒）
graceful_timeout = 30  # 优雅关闭超时
keepalive = 5  # Keep-Alive连接超时

# 日志配置
accesslog = "access.log"
errorlog = "error.log"
loglevel = "warning"  # 降低日志级别，减少IO

# 进程命名
proc_name = "zGetContentByXpath"

# 预加载应用（提高启动速度，但会增加内存使用）
preload_app = True

# 性能优化
worker_tmp_dir = "/dev/shm"  # 使用内存作为临时目录（Linux）
