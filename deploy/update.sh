#!/bin/bash
set -e

# CodeYun Update Script (Run by GitHub Actions)

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo ">>> Starting update process in $PROJECT_DIR..."
cd "$PROJECT_DIR"

# 1. Update Code
echo ">>> Pulling latest code..."
git fetch origin main
git reset --hard origin/main

# 2. Sync dependencies (fast if no changes)
if command -v uv &> /dev/null; then
    echo ">>> Syncing dependencies with uv..."
    uv sync
else
    echo "⚠️ Warning: 'uv' not found. Skipping dependency sync."
fi

# 3. Build Frontend (if package.json changed or dist missing)
echo ">>> Checking frontend..."
cd "$PROJECT_DIR/frontend"
if [ ! -d "dist" ] || git diff --name-only HEAD@{1} HEAD | grep -q "frontend/"; then
    echo ">>> Rebuilding frontend..."
    npm install
    npm run build
else
    echo ">>> Frontend up to date."
fi

# 4. Restart Backend Service (System Level)
echo ">>> Restarting backend service..."
# GitHub Actions runs as a normal user, so the deploy user must have
# NOPASSWD permission for this exact restart command.
sudo -n /usr/bin/systemctl restart codeyun-backend

# 5. Check status
if systemctl is-active --quiet codeyun-backend; then
    echo "✅ Backend service restarted successfully."
else
    echo "❌ Backend service failed to become active. Check logs: sudo journalctl -u codeyun-backend"
    exit 1
fi

echo ">>> Update complete!"
