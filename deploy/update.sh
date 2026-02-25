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
# Since this script runs as user via GitHub Actions, we need sudo for systemctl
# Ensure the user has NOPASSWD for systemctl restart codeyun-backend in sudoers
# OR just rely on the fact that we might not have sudo access and fail gracefully?
# No, we must restart the service.
if sudo -n true 2>/dev/null; then
    sudo systemctl restart codeyun-backend
else
    echo "⚠️ Warning: No sudo access. Cannot restart service automatically."
    echo "   Please run: sudo systemctl restart codeyun-backend"
fi

# 3. Check status
if systemctl is-active --quiet codeyun-backend; then
    echo "✅ Backend service restarted successfully."
else
    echo "⚠️ Backend service status unknown or failed. Check logs: sudo journalctl -u codeyun-backend"
    # Don't exit 1 here as we might not have permission to check status
fi

echo ">>> Update complete!"
