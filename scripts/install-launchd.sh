#!/usr/bin/env bash
#
# Install macOS Launch Agents for distillate.
# - Sync agent: runs every 15 minutes
# - Promote agent: runs daily at 8:30am
# Logs to ~/Library/Logs/.
#
set -euo pipefail

LABEL="com.distillate.sync"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG="$HOME/Library/Logs/distillate.log"

# Resolve repo root (parent of scripts/)
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EXECUTABLE="${REPO_DIR}/.venv/bin/distillate"

# Verify prerequisites
if [[ ! -x "$EXECUTABLE" ]]; then
    echo "Error: $EXECUTABLE not found."
    echo "Run 'uv pip install -e .' from the repo root first."
    exit 1
fi

RMAPI="$(command -v rmapi 2>/dev/null || true)"
if [[ -z "$RMAPI" ]]; then
    echo "Error: rmapi not found in PATH."
    echo "Install it: https://github.com/ddvk/rmapi/releases"
    exit 1
fi

# Build PATH: include the directory containing rmapi
RMAPI_DIR="$(dirname "$RMAPI")"
LAUNCH_PATH="/usr/local/bin:/usr/bin:/bin"
if [[ ":$LAUNCH_PATH:" != *":$RMAPI_DIR:"* ]]; then
    LAUNCH_PATH="${RMAPI_DIR}:${LAUNCH_PATH}"
fi

# Unload existing agent if present
if launchctl list "$LABEL" &>/dev/null; then
    echo "Unloading existing agent..."
    launchctl unload "$PLIST" 2>/dev/null || true
fi

# Ensure LaunchAgents directory exists
mkdir -p "$HOME/Library/LaunchAgents"

# Write the plist
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${EXECUTABLE}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>

    <key>StartInterval</key>
    <integer>900</integer>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${LAUNCH_PATH}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG}</string>
    <key>StandardErrorPath</key>
    <string>${LOG}</string>

    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
EOF

# Load the agent
launchctl load "$PLIST"

echo "Installed and loaded: $LABEL"
echo ""
echo "  Plist:      $PLIST"
echo "  Executable: $EXECUTABLE"
echo "  Schedule:   every 15 minutes"
echo "  Log:        $LOG"
echo ""
echo "Useful commands:"
echo "  launchctl start $LABEL          # run sync now"
echo "  tail -f $LOG                    # watch logs"
echo "  launchctl unload $PLIST         # stop sync"

# -- Auto-promote agent (daily at 8:30am) --
PROMOTE_LABEL="com.distillate.promote"
PROMOTE_PLIST="$HOME/Library/LaunchAgents/${PROMOTE_LABEL}.plist"

# Unload existing promote agent if present
if launchctl list "$PROMOTE_LABEL" &>/dev/null; then
    echo ""
    echo "Unloading existing promote agent..."
    launchctl unload "$PROMOTE_PLIST" 2>/dev/null || true
fi

cat > "$PROMOTE_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PROMOTE_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${EXECUTABLE}</string>
        <string>--promote</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>

    <key>StartInterval</key>
    <integer>28800</integer>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${LAUNCH_PATH}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG}</string>
    <key>StandardErrorPath</key>
    <string>${LOG}</string>

    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
EOF

launchctl load "$PROMOTE_PLIST"

echo ""
echo "Installed and loaded: $PROMOTE_LABEL"
echo ""
echo "  Plist:      $PROMOTE_PLIST"
echo "  Executable: $EXECUTABLE --promote"
echo "  Schedule:   every 8 hours (fires on wake if overdue)"
echo "  Log:        $LOG"
echo ""
echo "  launchctl start $PROMOTE_LABEL    # run promote now"
echo "  launchctl unload $PROMOTE_PLIST   # stop promote"
