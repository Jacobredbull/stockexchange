# ─────────────────────────────────────────────
# stockexchange_V0.1 — ARM64 (Raspberry Pi 5)
# ─────────────────────────────────────────────
# 使用 Debian Bookworm 作为基础，包含树莓派官方支持的包
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

WORKDIR /app

# 3. 复制依赖清单，安装剩余的小型库
COPY requirements.txt .

# 4. 只安装 requirements.txt 中剩下的库
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Create persistent dirs
RUN mkdir -p /app/logs /app/data

# Healthcheck: verify supervisor heartbeat file is < 5 min old
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

CMD ["python", "-u", "supervisor.py"]
