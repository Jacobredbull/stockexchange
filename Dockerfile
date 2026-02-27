# ─────────────────────────────────────────────
# stockexchange_V0.1 — ARM32 (Raspberry Pi Build)
# ─────────────────────────────────────────────
# 使用 Python 3.11 基础镜像
FROM python:3.11-slim

# 1. 在安装任何 Python 库之前，先安装系统级编译器
# 增加了 cmake 和 libopenblas-dev，因为 pyarrow 和 numpy 等重型库编译时非常需要它
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    cmake \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. 升级 pip 工具链
RUN pip install --upgrade pip setuptools wheel

# 3. 设置工作目录
WORKDIR /app

# 4. 复制并安装依赖
# 注意：增加 --prefer-binary 选项，这会让 pip 尽量去找现成的“零件”
COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# 5. 拷贝剩余代码并创建持久化目录
COPY . .
RUN mkdir -p /app/logs /app/data

# 6. 健康检查
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

# 启动程序
CMD ["python", "-u", "supervisor.py"]
