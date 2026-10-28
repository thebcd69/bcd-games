#!/bin/bash
# Keep the present shader's metadata layout paired with the Auto HDR presenter.
# Some ARM64EC recipes normally reuse a metallib from the shipped d3d11.dll.
set -eu
CORE="$1"
OUT="$2"
if ! grep -qF 'madeira-bcd: auto HDR' "$CORE/dxmt_command.metal"; then
    exit 0
fi
mkdir -p "$OUT"
xcrun -sdk macosx metal -std=metal3.1 --target=air64-apple-macos14.0 \
    -c "$CORE/dxmt_command.metal" -o "$OUT/dxmt_command.air"
xcrun -sdk macosx metallib "$OUT/dxmt_command.air" -o "$OUT/dxmt_command.metallib"
xxd -n dxmt_command -i "$OUT/dxmt_command.metallib" > "$OUT/dxmt_command.h"
echo "Auto HDR: compiled the matching present shader (Metal 3.1)"
