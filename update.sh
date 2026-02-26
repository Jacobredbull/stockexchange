#!/bin/bash
# ---------------------------------------------------------------------------
# stockexchange_V0.1 — Remote Workflow Updater
# ---------------------------------------------------------------------------

echo "🔄 Fetching latest stockexchange_V0.1 updates..."
git pull origin main

echo "⚠️  Restarting Docker container..."
docker compose up -d --build

echo "✅ Update Successful."
