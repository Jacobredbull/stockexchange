FROM python:3.11-slim-bookworm

# 1. Build tools + runtime libs for numpy/pandas on ARM
#    gcc/g++/gfortran: C/Fortran compilers for building from source
#    libopenblas-dev: BLAS/LAPACK backend (required by numpy)
#    curl: needed for health checks / package downloads
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran \
    libopenblas-dev liblapack-dev \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Upgrade pip and install wheel/setuptools first
#    (helps resolve build issues with older sdist packages on ARM)
RUN pip install --upgrade pip setuptools wheel

# 3. Install numpy and pandas first, separately
#    These are the most problematic on ARM — installing them first gives
#    pip a chance to pull pre-built wheels from piwheels before other
#    packages declare their own numpy constraints.
RUN pip install --no-cache-dir --prefer-binary \
    --extra-index-url https://www.piwheels.org/simple \
    "numpy==1.26.4" "pandas==2.2.2"

# 4. Install remaining packages (now includes alpaca-py which uses websockets>=10.4)
#    alpaca-py has NO upper cap on websockets, fully compatible with google-genai
COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary \
    --extra-index-url https://www.piwheels.org/simple \
    -r requirements.txt

# 5. Install google-genai — now works cleanly with alpaca-py!
#    alpaca-py requires websockets>=10.4 (no upper limit)
#    google-genai requires websockets>=13  → both satisfied by websockets 13+
#    libffi-dev (above) ensures cffi/google-auth compiles on ARM
COPY requirements-genai.txt .
RUN pip install --no-cache-dir --prefer-binary \
    --extra-index-url https://www.piwheels.org/simple \
    -r requirements-genai.txt

# 6. Copy application code and create persistent data directories
COPY . .
RUN mkdir -p /app/logs /app/data

# 7. Verify all packages loaded correctly (build-time sanity check)
RUN python build_check.py

# 8. Docker health check (file-based heartbeat written by supervisor.py)
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

CMD ["python", "-u", "supervisor.py"]
