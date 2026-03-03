#!/usr/bin/env bash
# Build a self-contained Python venv for bundling with Electron.
# Usage: ./scripts/build-venv.sh
#
# Creates ../.venv-desktop with distillate + fastapi + uvicorn installed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv-desktop"

echo "==> Creating venv at $VENV_DIR"
uv venv --python 3.12 "$VENV_DIR"

echo "==> Installing distillate + server deps"
uv pip install --python "$VENV_DIR/bin/python3" \
  -e "$ROOT_DIR" \
  fastapi uvicorn[standard] websockets

echo "==> Venv ready at $VENV_DIR"
echo "    Python: $VENV_DIR/bin/python3"
echo "    Test:   $VENV_DIR/bin/python3 -m distillate.server"
