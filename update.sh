#!/bin/bash
# ---------------------------------------------------------------------------
# Antigravity V3.1 — Remote Workflow Updater
# ---------------------------------------------------------------------------

echo "🔄 Fetching latest Antigravity updates..."
git pull origin main

echo "⚠️  Restarting Docker container..."
docker compose up -d --build

echo "✅ Update Successful."
