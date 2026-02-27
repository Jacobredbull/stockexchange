FROM python:3.11-slim-bookworm

# 1. 安装 NumPy 和 Pandas 运行所需的底层共享库 (Shared Objects)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libopenblas0 \
    liblapack3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. 升级 pip
COPY requirements.txt .
RUN pip install --upgrade pip

# 3. 安装除 google-genai 之外的所有依赖
# 用 --prefer-binary 避免从源码编译
RUN pip install --no-cache-dir --prefer-binary \
    --extra-index-url https://www.piwheels.org/simple \
    $(grep -v google-genai requirements.txt | grep -v '^#' | grep -v '^$' | tr '\n' ' ')

# 4. 单独安装 google-genai (--no-deps 绕过 websockets>=13 冲突)
# alpaca-trade-api 需要 websockets<11，google-genai 需要 websockets>=13，二者冲突
# 我们只用 REST generate_content()，不需要 websockets，所以跳过它的依赖安全
RUN pip install --no-cache-dir --no-deps google-genai==0.3.0
RUN pip install --no-cache-dir --prefer-binary google-auth httpx pydantic

# 5. 拷贝代码并创建持久化目录
COPY . .
RUN mkdir -p /app/logs /app/data

# 健康检查
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

CMD ["python", "-u", "supervisor.py"]
