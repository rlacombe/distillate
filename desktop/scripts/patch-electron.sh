#!/bin/bash
# Patch the Electron.app bundle for dev mode so macOS shows "Distillate"
# in the dock, Cmd+Tab switcher, and menu bar instead of "Electron".
#
# This runs as part of postinstall and must be re-run after npm install/ci.

set -e

DIST="node_modules/electron/dist"
SRC="$DIST/Electron.app"
DST="$DIST/Distillate.app"

# Rename the .app bundle (Cmd+Tab reads the folder name)
if [ -d "$SRC" ]; then
  rm -rf "$DST"
  mv "$SRC" "$DST"
fi

# Update path.txt so the electron CLI finds the renamed binary
printf "Distillate.app/Contents/MacOS/Electron" > node_modules/electron/path.txt

# Patch Info.plist
PLIST="$DST/Contents/Info.plist"
plutil -replace CFBundleDisplayName -string Distillate "$PLIST"
plutil -replace CFBundleName -string Distillate "$PLIST"

# Copy our icon into the bundle
cp resources/icon.icns "$DST/Contents/Resources/electron.icns"

echo "Electron.app patched → Distillate.app"
