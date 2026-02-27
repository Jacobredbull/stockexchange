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
    python3-pytz \
    && rm -rf /var/lib/apt/lists/*

# Step 2: Allow Python to see the apt-installed packages
ENV PYTHONPATH=/usr/lib/python3/dist-packages

WORKDIR /app

# Step 3: Upgrade pip toolchain
RUN pip install --upgrade pip

# Step 4: Install remaining lightweight packages
# Uses PiWheels as extra index to find ARM pre-built wheels
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --extra-index-url https://www.piwheels.org/simple \
    -r requirements.txt

# Step 5: Copy app code and create persistent dirs
COPY . .
RUN mkdir -p /app/logs /app/data

# Healthcheck: verify supervisor heartbeat file is < 5 min old
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

CMD ["python", "-u", "supervisor.py"]
