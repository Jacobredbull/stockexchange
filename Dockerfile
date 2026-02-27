FROM python:3.11-slim-bookworm

# 1. 安装最轻量级的数学支持（不再需要 build-essential，因为我们不编译）
RUN apt-get update && apt-get install -y \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. 强制 pip 只寻找预编译包，不许编译
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

# 3. 复制逻辑代码并创建必须的文件夹
COPY . .
RUN mkdir -p /app/logs /app/data

# 4. 健康检查 (用于 Supervisor 监控)
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

CMD ["python", "supervisor.py"]
