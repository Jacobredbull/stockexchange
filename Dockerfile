FROM python:3.11-slim-bookworm

# 1. Build tools + runtime libs for numpy/pandas on ARM
#    gcc/g++/gfortran: compile C extensions when no binary wheel exists
#    libopenblas-dev/liblapack-dev: linear algebra backends for numpy
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran \
    libopenblas-dev liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Upgrade pip
RUN pip install --upgrade pip

# 3. Install all packages except google-genai
#    --prefer-binary: use pre-built wheels over sdist (key for ARM speed)
#    piwheels.org: Raspberry Pi pre-compiled ARM wheels repository
COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary \
    --extra-index-url https://www.piwheels.org/simple \
    -r requirements.txt

# 4. Install google-genai without its deps (avoids websockets conflict)
#    alpaca-trade-api needs websockets<11
#    google-genai needs websockets>=13  →  irreconcilable conflict
#    We only use google-genai's REST generate_content(), not streaming,
#    so skipping websockets is safe.
COPY requirements-genai.txt .
RUN pip install --no-cache-dir --no-deps -r requirements-genai.txt
RUN pip install --no-cache-dir --prefer-binary google-auth httpx pydantic

# 5. Copy application code and create persistent data directories
COPY . .
RUN mkdir -p /app/logs /app/data

# 6. Docker health check (file-based heartbeat written by supervisor.py)
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

CMD ["python", "-u", "supervisor.py"]
