---
name: connectors
description: Check connector status — show which integrations are connected and configured
---

# Connectors

Show the status of all configured integrations (Zotero, Email, Obsidian, reMarkable).

## Steps

1. Run `distillate --connectors` via Bash to display connector status with colored indicators
2. Report what you see to the user — which connectors are connected, which are missing
3. If any connectors are disconnected, suggest the user can run `distillate --setup <name>` or ask you to help configure them
