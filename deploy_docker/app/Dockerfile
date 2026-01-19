FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY app.py .
CMD ["sh", "-c", "gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT --access-logfile - --error-logfile - --log-level warning"]
