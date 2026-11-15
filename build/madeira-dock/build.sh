#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 125hz
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
#
# Build Madeira Dock (madeira-dock) as a stripped x86-64 PE and stage
# it in the app bundle with its notices:
#   app/Madeira/arm64ec-windows/dockhost.exe
#   app/Madeira/arm64ec-windows/dock-notices.txt
# Both are build outputs (gitignored). The app starts dockhost.exe as
# C:\windows\system32\dockhost.exe; without it, Madeira Dock stays hidden.
#
# Usage: build/madeira-dock/build.sh [--check]
#   --check  also build and run Dock's own unit tests with the host compiler
#            (madeira-dock/tools/check.sh; needs cc with ASan/UBSan).
# LLVM_MINGW=<dir with x86_64-w64-mingw32-clang> overrides the toolchain.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$DIR/../.." && pwd)"
SRC="$REPO_ROOT/madeira-dock"
MINGW="${LLVM_MINGW:-$REPO_ROOT/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
CC="$MINGW/x86_64-w64-mingw32-clang"
OUT="$REPO_ROOT/app/Madeira/arm64ec-windows"

[ -f "$SRC/src/main.c" ] || { echo "madeira-dock is missing: git submodule update --init madeira-dock" >&2; exit 1; }
[ -x "$CC" ] || { echo "missing cross compiler: $CC (set LLVM_MINGW)" >&2; exit 1; }

if [ "${1:-}" = "--check" ]; then
    (cd "$SRC" && bash tools/check.sh)
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Same flags as Dock's own tools/build.sh: warnings are errors, the runtime is
# linked statically (its notices go into dock-notices.txt) and symbols are
# stripped. -Wl,--no-insert-timestamp keeps the output reproducible.
"$CC" -std=c11 -O2 -Wall -Wextra -Werror -Wno-cast-function-type \
    -static -Wl,--strip-all -Wl,--no-insert-timestamp \
    -o "$TMP/dockhost.exe" "$SRC"/src/*.c -ladvapi32

section() { printf '\n\n==== %s ====\n\n' "$1"; }
{
    cat "$SRC/LICENSE"
    # madeira-bcd: since the Friends change (willfaust/madeira-dock pull request #10, taken before it merged) the pinned commit is one of
    # BCD's fork, which willfaust/madeira-dock does not have; the source this executable is built from is that commit plus tools/patch-dock-*.py.
    printf '\nCorresponding source: https://github.com/thebcd69/bcd-dock (commit %s), a fork of https://github.com/willfaust/madeira-dock,\n' \
        "$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
    printf 'built with the changes that tools/patch-dock-*.py of https://github.com/thebcd69/bcd-games make to it.\n'
    section 'LICENSE-EXCEPTION.md (Madeira Converter Exception)'
    cat "$SRC/LICENSE-EXCEPTION.md"
    section 'COPYING (GNU General Public License, version 3)'
    cat "$SRC/COPYING"
    section 'MinGW-w64 runtime notice (statically linked runtime)'
    cat "$SRC/notices/MinGW-w64-runtime.txt"
    section 'LLVM runtime notice'
    cat "$SRC/notices/LLVM.txt"
} > "$TMP/dock-notices.txt"

mkdir -p "$OUT"
cp "$TMP/dockhost.exe" "$TMP/dock-notices.txt" "$OUT/"
if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$OUT/dockhost.exe"; else sha256sum "$OUT/dockhost.exe"; fi
ls -la "$OUT/dockhost.exe" "$OUT/dock-notices.txt"
