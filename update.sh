#!/bin/bash
# ---------------------------------------------------------------------------
# update.sh — Pull latest code, rebuild Docker, auto-send errors to Telegram
# ---------------------------------------------------------------------------

set -e

LOG_FILE="/tmp/stockexchange_build.log"

echo "🔄 Fetching latest stockexchange_V0.5 updates..."
git pull origin main

echo "🔨 Building Docker image (logging to $LOG_FILE)..."
if docker compose build 2>&1 | tee "$LOG_FILE"; then
    echo "✅ Build successful. Starting container..."
    docker compose up -d
    echo "✅ Container started."

    # Send success notification to Telegram
    if [ -f "send_log.sh" ]; then
        echo "✅ Docker build & deploy SUCCESS on $(hostname)" | bash send_log.sh "Build OK ✅"
    fi
else
    echo ""
    echo "❌ Docker build FAILED. Sending error log to Telegram..."

    # Send the last 3500 chars of the build log to Telegram
    if [ -f "send_log.sh" ]; then
        bash send_log.sh "Build FAILED ❌" "$LOG_FILE"
    fi

    echo ""
    echo "--- Last 50 lines of build log ---"
    tail -50 "$LOG_FILE"
    exit 1
fi
