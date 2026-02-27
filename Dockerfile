# ─────────────────────────────────────────────
# stockexchange_V0.1 — ARM32 (Raspberry Pi 5)
# DEFINITIVE BUILD: apt + PiWheels
# ─────────────────────────────────────────────
FROM python:3.11-slim-bookworm

# Step 1: Install heavy libraries via apt (pre-compiled for ARM32 by Debian)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-numpy \
    python3-pandas \
    python3-yaml \
    python3-requests \
    && rm -rf /var/lib/apt/lists/*

# Step 2: Allow Python to see the apt-installed packages
ENV PYTHONPATH=/usr/lib/python3/dist-packages

WORKDIR /app

# Step 3: Upgrade pip toolchain
RUN pip install --upgrade pip

# Step 4: Install all packages EXCEPT google-genai first (with full deps)
# This lets alpaca-trade-api lock websockets to <11
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --extra-index-url https://www.piwheels.org/simple \
    $(grep -v google-genai requirements.txt | grep -v '^#' | grep -v '^$' | tr '\n' ' ')

# Step 5: Install google-genai WITHOUT its deps to bypass websockets>=13 conflict
# Our code only uses REST generate_content(), not live streaming, so websockets is irrelevant
RUN pip install --no-cache-dir --no-deps \
    --extra-index-url https://www.piwheels.org/simple \
    google-genai==0.3.0
# Install google-genai's non-websockets deps manually
RUN pip install --no-cache-dir \
    --extra-index-url https://www.piwheels.org/simple \
    google-auth httpx pydantic

# Step 5: Copy app code and create persistent dirs
COPY . .
RUN mkdir -p /app/logs /app/data

# Healthcheck: verify supervisor heartbeat file is < 5 min old
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

CMD ["python", "-u", "supervisor.py"]
