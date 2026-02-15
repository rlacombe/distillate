#!/usr/bin/env bash
#
# Uninstall macOS Launch Agents for distillate.
#
set -euo pipefail

uninstall_agent() {
    local label="$1"
    local plist="$HOME/Library/LaunchAgents/${label}.plist"

    if [[ ! -f "$plist" ]]; then
        echo "Nothing to uninstall: $plist does not exist."
        return
    fi

    launchctl unload "$plist" 2>/dev/null || true
    rm "$plist"
    echo "Uninstalled: $label"
}

uninstall_agent "com.distillate.sync"
uninstall_agent "com.distillate.promote"

echo "Log file kept at: $HOME/Library/Logs/distillate.log"
