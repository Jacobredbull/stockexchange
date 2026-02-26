# ─────────────────────────────────────────────
# stockexchange_V0.1 — ARM64 (Raspberry Pi 5)
# ─────────────────────────────────────────────
FROM python:3.11-slim

# System deps for pandas / numpy wheels on ARM64
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Create persistent dirs
RUN mkdir -p /app/logs /app/data

# Healthcheck: verify supervisor heartbeat file is < 5 min old
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD python -c "from supervisor import check_heartbeat; exit(0 if check_heartbeat(300) else 1)"

CMD ["python", "-u", "supervisor.py"]
