#!/bin/bash
set -e

echo "=========================================="
echo "🚀 SElf v3.0 - Railway Startup"
echo "=========================================="

# Create required dirs
mkdir -p data data/action downloads logs templates

# Check if templates exists, if not warn
if [ ! -f "templates/dashboard.html" ]; then
    echo "⚠️ WARNING: templates/dashboard.html not found!"
fi

# Set default port
PORT=${PORT:-8000}
echo "📡 Port: $PORT"
echo "📁 Working dir: $(pwd)"
echo "🐍 Python: $(python --version)"
echo "🔐 API_ID set: $([ -n "$API_ID" ] && echo "yes" || echo "no")"
echo "🔐 API_HASH set: $([ -n "$API_HASH" ] && echo "yes" || echo "no")"
echo "👤 OWNER_ID: ${OWNER_ID:-me}"
echo "🔑 ADMIN_PASSWORD set: $([ -n "$ADMIN_PASSWORD" ] && echo "yes" || echo "no")"
echo "=========================================="

# Start dashboard (which will auto-start bot if configured)
exec uvicorn app:app --host 0.0.0.0 --port $PORT --log-level info
