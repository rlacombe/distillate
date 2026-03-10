#!/bin/bash
# build-dmg.sh — Create a DMG with proper Applications folder icon
# Uses a staging directory so we can set a custom icon on the symlink
# before packaging into the DMG.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

APP_NAME="Distillate"
VERSION=$(node -p "require('$DESKTOP_DIR/package.json').version")
APP_PATH="$DESKTOP_DIR/dist/mac-arm64/$APP_NAME.app"
DMG_PATH="$DESKTOP_DIR/dist/$APP_NAME-$VERSION-arm64.dmg"
BG_IMG="$DESKTOP_DIR/resources/dmg-bg.png"
FOLDER_ICON="$DESKTOP_DIR/resources/icon.icns"  # reuse app icon as fallback

# Extract the blue folder icon from /System
FOLDER_ICNS="/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/GenericFolderIcon.icns"

if [ ! -d "$APP_PATH" ]; then
  echo "Error: $APP_PATH not found. Run 'npm run build:mac' first."
  exit 1
fi

# Clean previous DMG
rm -f "$DMG_PATH"

# Create staging directory
STAGING=$(mktemp -d)
trap "rm -rf '$STAGING'" EXIT

echo "==> Staging DMG contents..."
cp -R "$APP_PATH" "$STAGING/$APP_NAME.app"
ln -s /Applications "$STAGING/Applications"

# Set the macOS blue folder icon on the Applications symlink
if command -v fileicon &>/dev/null && [ -f "$FOLDER_ICNS" ]; then
  echo "==> Setting folder icon on Applications symlink..."
  fileicon set "$STAGING/Applications" "$FOLDER_ICNS" 2>/dev/null || true
fi

echo "==> Creating DMG with create-dmg..."
create-dmg \
  --volname "$APP_NAME" \
  --window-pos 200 120 \
  --window-size 540 380 \
  --background "$BG_IMG" \
  --icon-size 80 \
  --icon "$APP_NAME.app" 130 190 \
  --icon "Applications" 410 190 \
  --no-internet-enable \
  "$DMG_PATH" \
  "$STAGING"

echo "==> Done: $DMG_PATH"
