#!/usr/bin/env bash
# Rasterize docs/logo.svg into the full app icon set.
# Produces:
#   desktop/resources/icon.png          1024×1024 (Linux .desktop, fallback)
#   desktop/resources/icon.icns         macOS .app bundle (16–1024 + @2x)
#   desktop/resources/icon.ico          Windows .exe (16, 32, 48, 64, 128, 256)
#   desktop/resources/icon-hf.png       512×512 (Hugging Face OAuth app upload)
#
# Requires: rsvg-convert, iconutil (macOS), Python 3.12 venv with Pillow.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$here"

svg="desktop/resources/icon.svg"
out="desktop/resources"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if ! command -v rsvg-convert >/dev/null; then
  echo "error: rsvg-convert not found (brew install librsvg)" >&2
  exit 1
fi
if ! command -v iconutil >/dev/null; then
  echo "error: iconutil not found (macOS only)" >&2
  exit 1
fi

py=".venv/bin/python"
[ -x "$py" ] || { echo "error: $py not found (run uv venv --python 3.12)" >&2; exit 1; }

echo "→ rasterizing $svg at 1024×1024 (master)"
rsvg-convert -w 1024 -h 1024 "$svg" -o "$tmp/master.png"

# icon.png (Linux + electron-builder fallback) — keep at 1024 for crispness
cp "$tmp/master.png" "$out/icon.png"

# icon-hf.png — 512×512 for HF OAuth app upload
rsvg-convert -w 512 -h 512 "$svg" -o "$out/icon-hf.png"

# tray-icon.png — 88×88 line-art glyph for the macOS menu bar (recolored per
# status at runtime). Separate source SVG because the tray needs monochrome
# white strokes on transparent bg, not the filled app icon.
if [ -f "$out/tray-icon.svg" ]; then
  rsvg-convert -w 44 -h 44 "$out/tray-icon.svg" -o "$out/tray-icon.png"
fi

# Per-size renders for the .iconset + .ico. Rendering each size directly from
# SVG (instead of downscaling a single raster) keeps small sizes readable.
iconset="$tmp/icon.iconset"
mkdir -p "$iconset"

render() {
  local size="$1" name="$2"
  rsvg-convert -w "$size" -h "$size" "$svg" -o "$iconset/$name"
}

# macOS requires these exact filenames
render   16 "icon_16x16.png"
render   32 "icon_16x16@2x.png"
render   32 "icon_32x32.png"
render   64 "icon_32x32@2x.png"
render  128 "icon_128x128.png"
render  256 "icon_128x128@2x.png"
render  256 "icon_256x256.png"
render  512 "icon_256x256@2x.png"
render  512 "icon_512x512.png"
render 1024 "icon_512x512@2x.png"

echo "→ building icon.icns"
iconutil -c icns "$iconset" -o "$out/icon.icns"

echo "→ building icon.ico"
"$py" - <<'PY'
from PIL import Image
import subprocess, tempfile, os
# Order largest-first so Pillow uses the highest-quality image as the base,
# and downscales via `sizes=` to emit a true multi-resolution ICO.
sizes = [256, 128, 64, 48, 32, 24, 16]
with tempfile.TemporaryDirectory() as td:
    master_path = os.path.join(td, "256.png")
    subprocess.run(["rsvg-convert", "-w", "256", "-h", "256",
                    "desktop/resources/icon.svg", "-o", master_path], check=True)
    Image.open(master_path).convert("RGBA").save(
        "desktop/resources/icon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
PY

echo
echo "✓ icon set rebuilt:"
ls -la "$out"/icon.png "$out"/icon.icns "$out"/icon.ico "$out"/icon-hf.png "$out"/tray-icon.png 2>/dev/null
