#!/bin/bash
# ---------------------------------------------------------------------------
# send_log.sh — Send any text file or piped text to Telegram
#
# Usage:
#   Pipe output:   docker compose build 2>&1 | bash send_log.sh "Build Output"
#   Send a file:   bash send_log.sh "Build Error" /tmp/build.log
#
# This reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env automatically.
# ---------------------------------------------------------------------------

set -e

LABEL="${1:-Log}"
FILE="${2:-}"

# Load credentials from .env
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | grep -E 'TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID' | xargs)
fi

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not found in .env"
    exit 1
fi

# Read content from file or stdin
if [ -n "$FILE" ] && [ -f "$FILE" ]; then
    CONTENT=$(tail -c 3500 "$FILE")  # Telegram max ~4096 chars
else
    CONTENT=$(cat | tail -c 3500)
fi

if [ -z "$CONTENT" ]; then
    CONTENT="(empty output)"
fi

HOSTNAME=$(hostname)
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
MESSAGE="📋 [$HOSTNAME] $LABEL
Time: $TIMESTAMP

\`\`\`
$CONTENT
\`\`\`"

# Send to Telegram
curl -s -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d parse_mode="Markdown" \
    --data-urlencode "text=${MESSAGE}" \
    > /dev/null

echo "✅ Sent to Telegram: $LABEL"
