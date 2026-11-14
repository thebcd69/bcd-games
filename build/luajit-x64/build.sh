#!/bin/bash
# Copyright 2026 Connor Gow. This file was taken from https://github.com/willfaust/Madeira (pull request #462)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
# GPL-3.0-or-later WITH the Madeira Converter Exception, version 1.
# Build an x86-64 Windows lua51.dll (LuaJIT 2.1) that Madeira can run, and
# stage the standard and C-style comment variants under app/Madeira/compat.
#
# Why this exists: LOVE games (Balatro, ...) ship LuaJIT built in the old
# 32-bit-GC-pointer mode, which requires every GC object to live below 2 GB.
# iOS cannot map anything down there, so luaL_newstate() returns NULL and
# love.exe crashes on its first Lua call. The wineserver maps this build in
# place of such a lua51.dll when it is loaded, leaving the game's file alone
# (build/wineserver/luajit_compat.c).
#
# Build options, all deliberate:
#   GC64 (the v2.1 default on x64)  64-bit GC references: no low-2GB heap.
#   LUAJIT_DISABLE_JIT              interpreter only. The trace compiler wants
#                                   its machine code within +-2 GB of the VM
#                                   and rewrites it in place; under FEX that is
#                                   self-modifying x86 code on every trace. The
#                                   interpreter is static code FEX translates once.
#   LUAJIT_NO_UNWIND                internal error unwinding. The Windows default
#                                   raises an SEH exception for EVERY lua_error
#                                   (pcall/error are routine in LOVE); internal
#                                   unwinding never leaves the VM.
#
# Source: toolchains/LuaJIT (https://github.com/LuaJIT/LuaJIT, branch v2.1, at the pinned commit below;
# the script clones it when missing and refuses any other commit).
#
# madeira-bcd: the same script as Madeira's apart from the place and the pin of the source, and the
# check at the end that the DLL it built really is the GC64 build.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${LUAJIT_SRC:-$R/toolchains/LuaJIT}"
OUT="$R/app/Madeira/compat/love"
LUAJIT_COMMIT=ea9eb7edb5eac4a9d175d74523db34fac0fa5180
export PATH="${LLVM_MINGW_BIN:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}:$PATH"
export MACOSX_DEPLOYMENT_TARGET=14.0   # LuaJIT's Makefile demands it for the macOS-hosted build tools

if [ ! -f "$SRC/src/luajit.h" ] && [ ! -f "$SRC/src/luajit_rolling.h" ]; then
    echo "LuaJIT source not found at $SRC; cloning branch v2.1"
    mkdir -p "$(dirname "$SRC")"
    git clone -q -b v2.1 https://github.com/LuaJIT/LuaJIT "$SRC"
fi
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$LUAJIT_COMMIT" ]; then
    git -C "$SRC" fetch -q origin v2.1 2>/dev/null || true
    git -C "$SRC" checkout -q "$LUAJIT_COMMIT" || { echo "LuaJIT $LUAJIT_COMMIT is not in $SRC" >&2; exit 1; }
fi

build_dll() {
    local source="$1" destination="$2"
    make -C "$source/src" clean >/dev/null
    make -C "$source/src" -j"${LUAJIT_JOBS:-$(sysctl -n hw.ncpu)}" \
        HOST_CC="xcrun clang" \
        CROSS=x86_64-w64-mingw32- \
        TARGET_SYS=Windows \
        BUILDMODE=dynamic \
        XCFLAGS="-DLUAJIT_DISABLE_JIT -DLUAJIT_NO_UNWIND" \
        lua51.dll

    mkdir -p "$destination"
    x86_64-w64-mingw32-strip -o "$destination/lua51.dll" "$source/src/lua51.dll"
    if LC_ALL=C grep -aq NtAllocateVirtualMemory "$destination/lua51.dll"; then
        echo "$destination/lua51.dll still holds the low-memory allocator: it is not a GC64 build" >&2
        rm -f "$destination/lua51.dll"
        return 1
    fi
}

# Build each variant from a clean copy of the pin.
staging="$(mktemp -d "${TMPDIR:-/tmp}/bcd-luajit.XXXXXXXX")"
trap 'rm -rf "$staging"' EXIT
set -o pipefail

build_variant() {
    local name="$1" patch_name="$2" destination="$3"
    local source="$staging/$name"
    mkdir -p "$source"
    git -C "$SRC" archive "$LUAJIT_COMMIT" | tar -xf - -C "$source"
    # LuaJIT's rolling release build derives the timestamp from git.
    git -C "$SRC" show -s --format=%ct "$LUAJIT_COMMIT" > "$source/.relver"
    (cd "$source" && patch -s -p1 < "$R/build/luajit-x64/$patch_name")
    build_dll "$source" "$destination"
}

build_variant stock c-style-comments-diagnostic.patch "$OUT"
git -C "$SRC" rev-parse --short HEAD > "$OUT/lua51.dll.version"
cp "$SRC/COPYRIGHT" "$R/app/Madeira/licenses/LuaJIT-MIT.txt"
build_variant comments c-style-comments.patch "$R/app/Madeira/compat/lua-c-comments"
