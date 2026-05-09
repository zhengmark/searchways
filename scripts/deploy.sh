#!/bin/bash
set -e

cd "$(dirname "$0")/.."

echo "=== 现在就出发 一键部署 ==="
echo ""

# Check .env
if [ ! -f .env ]; then
    echo "[ERROR] .env file not found. Copy .env.example and fill in your keys."
    exit 1
fi

# Pull latest
git pull --ff-only 2>/dev/null || echo "[WARN] Not a git repo or can't pull, using current code"

# Build & start
docker compose down --remove-orphans 2>/dev/null || true
docker compose build
docker compose up -d

# Health check
echo ""
echo "Waiting for service to be healthy..."
for i in $(seq 1 15); do
    if curl -sf http://localhost:${PORT:-8000}/api/health > /dev/null 2>&1; then
        echo "[OK] Service is running at http://localhost:${PORT:-8000}"
        exit 0
    fi
    sleep 2
done

echo "[ERROR] Service failed to start. Check logs: docker compose logs"
exit 1
