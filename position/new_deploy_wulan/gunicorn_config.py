import multiprocessing

# 1. 注释掉或删除这行，我们将通过命令行参数传递端口
bind = "0.0.0.0:8100" 

# Worker配置
# 注意：如果你开启了多个Gunicorn实例（比如2个端口），
# 这里的worker数量可能需要除以实例数，否则总进程数过多会导致CPU切换频繁
# 如果你的CPU核心数较少，建议写死一个数字，比如 workers = 4
workers = multiprocessing.cpu_count() * 2 + 1 
worker_class = "uvicorn.workers.UvicornWorker"

# ... (其他配置保持不变) ...

# 进程命名 (为了方便脚本 kill，保持统一的前缀)
proc_name = "zGetContentByXpathls"