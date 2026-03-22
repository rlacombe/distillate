#!/bin/bash
# install.sh — Install Distillate CLI + desktop app in one go
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$DESKTOP_DIR/.." && pwd)"

APP_NAME="Distillate"
APP_SRC="$DESKTOP_DIR/dist/mac-arm64/$APP_NAME.app"
APP_DEST="/Applications/$APP_NAME.app"

# ── 1. Install Python CLI into the app's venv ──
VENV_DIR="$HOME/Library/Application Support/Distillate/python-env"

echo "==> Setting up Python environment..."
if ! command -v uv &>/dev/null; then
  echo "    Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Always recreate venv to ensure correct arch and Python version
if [ -d "$VENV_DIR" ]; then
  rm -rf "$VENV_DIR"
fi
# Use native arch — uv may default to an x86_64 install otherwise
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
  PY_SPEC="cpython-3.12-macos-aarch64-none"
else
  PY_SPEC="3.12"
fi
echo "    Creating venv ($ARCH)..."
uv venv --python "$PY_SPEC" "$VENV_DIR"

echo "    Installing distillate..."
VIRTUAL_ENV="$VENV_DIR" uv pip install -e "$PROJECT_DIR[desktop]"

# ── 2. Install npm dependencies ──
echo "==> Installing npm dependencies..."
cd "$DESKTOP_DIR"
npm install

# ── 3. Build Electron app ──
echo "==> Building $APP_NAME..."
npm run build:mac

if [ ! -d "$APP_SRC" ]; then
  echo "Error: build failed — $APP_SRC not found."
  exit 1
fi

# ── 4. Install to /Applications ──
if [ -d "$APP_DEST" ]; then
  echo "==> Removing previous install..."
  rm -rf "$APP_DEST"
fi

echo "==> Installing to /Applications..."
cp -R "$APP_SRC" "$APP_DEST"

# ── 5. Add to Dock (if not already there) ──
if ! defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "$APP_NAME.app"; then
  echo "==> Adding to Dock..."
  defaults write com.apple.dock persistent-apps -array-add \
    "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>$APP_DEST</string><key>_CFURLStringType</key><integer>0</integer></dict></dict></dict>"
  killall Dock
fi

# ── 6. Launch ──
echo "==> Launching $APP_NAME..."
open "$APP_DEST"
