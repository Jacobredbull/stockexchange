# ─────────────────────────────────────────────
# stockexchange_V0.1 — ARM32 (Raspberry Pi Build)
# ─────────────────────────────────────────────
# 使用 Debian Bookworm 作为基础，它包含树莓派官方支持的包
FROM python:3.11-slim-bookworm

# 1. 直接利用系统包管理器安装最沉重的“三巨头”以及 requests
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pandas \
    python3-numpy \
    python3-yaml \
    python3-requests \
    && rm -rf /var/lib/apt/lists/*

# 2. 设置环境变量，允许使用系统级安装的 Python 包
ENV PYTHONPATH=/usr/lib/python3/dist-packages

# 升级 pip 工具链 (可选保留，但保持官方干净)
RUN pip install --upgrade pip setuptools wheel

# 3. 设置工作目录
WORKDIR /app

# 4. 复制并安装依赖
# 注意：只安装 requirements.txt 中剩下的库
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 拷贝剩余代码并创建持久化目录
COPY . .
RUN mkdir -p /app/logs /app/data

# 6. 健康检查
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

# 启动程序
CMD ["python", "supervisor.py"]
