#!/bin/bash
set -e

# CodeYun Server Setup Script (Ubuntu 24.04 + uv + systemd + nginx)
# Run this script AS THE USER who will run the application (e.g., chenkunze)

CURRENT_USER=$(whoami)
echo ">>> Starting CodeYun deployment setup for user: $CURRENT_USER"

# 1. Kill old processes (from any user)
echo ">>> Stopping old processes (if any)..."
# Kill uvicorn on port 8000
if pgrep -f "uvicorn backend.app:app" > /dev/null; then
    sudo pkill -f "uvicorn backend.app:app" || true
fi
# Kill vite on port 5173
if pgrep -f "vite" > /dev/null; then
    sudo pkill -f "vite" || true
fi

# 2. Check Prerequisites
if ! command -v uv &> /dev/null; then
    echo "❌ Error: 'uv' is not installed. Please install it first (e.g., curl -LsSf https://astral.sh/uv/install.sh | sh)"
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo "❌ Error: 'npm' is not installed. Please install Node.js."
    exit 1
fi

# Check Node version (v20+ required for Vite 6+)
NODE_VERSION=$(node -v | cut -d. -f1 | tr -d 'v')
if [ "$NODE_VERSION" -lt 20 ]; then
    echo "⚠️ Node.js version $NODE_VERSION is too old. Upgrading to v20..."
    # Install NodeSource repo and upgrade
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
    echo "✅ Node.js upgraded to $(node -v)"
fi

# 3. Setup Project Environment
# We assume the user is already in the project directory or has cloned it
# If the script is run from inside the project, PROJECT_DIR should be PWD
# But we need to handle cases where user runs it from outside

# Detect if we are inside the project (check for pyproject.toml or similar)
if [ -f "pyproject.toml" ]; then
    PROJECT_DIR=$(pwd)
    echo ">>> Running inside project directory: $PROJECT_DIR"
else
    # Fallback to home/codeyun if not inside
    PROJECT_DIR="$HOME/codeyun"
    if [ ! -d "$PROJECT_DIR" ]; then
        echo ">>> Project directory not found at $PROJECT_DIR and not in current directory."
        echo ">>> Please run this script from inside the project root."
        exit 1
    fi
fi

echo ">>> Syncing backend dependencies with uv..."
cd "$PROJECT_DIR"
uv sync

echo ">>> Building frontend..."
cd "$PROJECT_DIR/frontend"
npm install
npm run build

echo ">>> Checking environment configuration..."
if [ ! -f .env ]; then
    echo ">>> Creating .env from example..."
    cp .env.example .env
    
    # Generate a random secret key
    RANDOM_KEY=$(openssl rand -hex 32)
    
    # Replace the placeholder with the random key
    # Use different delimiter for sed to avoid issues with special chars
    sed -i "s|CODEYUN_SECRET_KEY=change-me-to-a-secure-random-string|CODEYUN_SECRET_KEY=$RANDOM_KEY|g" .env
    
    echo "✅ Generated new CODEYUN_SECRET_KEY in .env"
else
    echo "✅ .env file already exists."
fi

# 4. Setup Systemd Service (User Level)
echo ">>> Setting up systemd service..."
mkdir -p "$HOME/.config/systemd/user"

# Replace %h with actual home if needed, but systemd supports %h natively
# However, we need to ensure the service file uses dynamic paths if not using %h
cp "$PROJECT_DIR/deploy/systemd/codeyun-backend.service" "$HOME/.config/systemd/user/"

# Reload systemd and enable service
systemctl --user daemon-reload
systemctl --user enable codeyun-backend
systemctl --user restart codeyun-backend

echo ">>> Enabling Linger for $CURRENT_USER (requires sudo)..."
# This ensures user services keep running after logout
if ! loginctl show-user "$CURRENT_USER" --property=Linger | grep -q "yes"; then
    sudo loginctl enable-linger "$CURRENT_USER" || echo "⚠️ Warning: Could not enable linger. Service might stop after logout."
else
    echo "✅ Linger already enabled."
fi

echo "✅ Backend service restarted via systemd."
echo "   Status: systemctl --user status codeyun-backend"

# 5. Setup Nginx (Requires Sudo)
NGINX_CONF="/etc/nginx/sites-available/code4101.com"
if [ -f "$PROJECT_DIR/deploy/nginx/codeyun.conf" ]; then
    echo ">>> Configuring Nginx..."
    echo "   Note: This step requires sudo privileges."
    
    # Generate Nginx config with correct paths
    TEMP_CONF=$(mktemp)
    cp "$PROJECT_DIR/deploy/nginx/codeyun.conf" "$TEMP_CONF"
    
    # Replace placeholder or hardcoded paths with current user's home
    # We assume the template might have /home/ubuntu or similar
    # It's better if the template has a placeholder like %HOME% but we can try to replace known patterns
    # Or just ensure we point to $PROJECT_DIR
    
    # Using sed to replace /home/ubuntu with $HOME (careful with slashes)
    # Also replace /srv/codeyun just in case
    ESCAPED_HOME=$(echo "$HOME" | sed 's/\//\\\//g')
    sed -i "s/\/home\/ubuntu\/codeyun/$ESCAPED_HOME\/codeyun/g" "$TEMP_CONF"
    sed -i "s/\/srv\/codeyun/$ESCAPED_HOME\/codeyun/g" "$TEMP_CONF"
    
    # Backup existing config if any
    if [ -f "$NGINX_CONF" ]; then
        sudo cp "$NGINX_CONF" "$NGINX_CONF.bak"
    fi
    
    # Copy new config (Overwriting existing code4101.com config)
    sudo cp "$TEMP_CONF" "$NGINX_CONF"
    rm "$TEMP_CONF"
    
    # Enable site if not already enabled
    if [ ! -L "/etc/nginx/sites-enabled/code4101.com" ]; then
        sudo ln -s "$NGINX_CONF" "/etc/nginx/sites-enabled/"
    fi
    
    # Ensure Nginx (www-data) can read the frontend files
    # This is tricky if home dir is 700.
    echo ">>> Checking permissions for Nginx..."
    # Check if home dir is executable by others (at least +x)
    if [ ! -x "$HOME" ]; then
        echo "⚠️ Warning: Your home directory $HOME might not be accessible by Nginx user."
        echo "   Running: chmod o+x $HOME"
        chmod o+x "$HOME"
    fi
    
    # Test and reload
    if sudo nginx -t; then
        sudo systemctl reload nginx
        echo "✅ Nginx configuration reloaded."
    else
        echo "❌ Nginx configuration test failed. Please check $NGINX_CONF"
    fi
else
    echo "⚠️ Nginx config file not found in repo."
fi

echo ">>> Setup complete!"
