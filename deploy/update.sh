#!/bin/bash
set -e

# CodeYun Update Script (Run by GitHub Actions)

PROJECT_DIR="$HOME/codeyun"

echo ">>> Starting update process..."
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

# 4. Restart Backend Service
echo ">>> Restarting backend service..."
systemctl --user restart codeyun-backend

# 3. Check status
if systemctl --user is-active --quiet codeyun-backend; then
    echo "✅ Backend service restarted successfully."
else
    echo "❌ Backend service failed to start. Check logs: journalctl --user -u codeyun-backend"
    exit 1
fi

echo ">>> Update complete!"
