#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
#
# Builds BCD's winebrowser.exe (bcd-openurl/openurl.c, round 625, the owner's item 23): what a Windows program runs to open a web page,
# here a request the app asks the player about (bcd-openurl/core.h). With llvm-mingw, for the three farms a session links into
# C:\windows\system32 and syswow64 (WineProcessBridge.m):
#   x86-64   -> app/Madeira/arm64ec-windows/winebrowser.exe   (a direct start's system32; tracked)
#   aarch64  -> app/Madeira/aarch64-windows/winebrowser.exe   (a Dock start's system32; tracked)
#   i686     -> app/Madeira/i386-windows/winebrowser.exe      (syswow64, in place of Wine's own, which can start no browser on iOS);
#               the i386 farm is built or restored by CI and not tracked, so the program is tracked as bcd-openurl/winebrowser-x86.exe
#               and copied from there. Run after the i386 farm.
# A build without llvm-mingw, or one that does not build, keeps the tracked programs (a warning, not a failure) and still puts the
# 32-bit one into the farm. The PE headers of what was built are checked: the machine, the GUI subsystem, and imports of kernel32 and
# the C runtime's api-ms-win-crt-* only. The build is reproducible (no time stamp). Run from the repository root.
set -u
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
SRC="$R/bcd-openurl"
OUT="$R/build/bcd-openurl"
EC="$R/app/Madeira/arm64ec-windows"
AA="$R/app/Madeira/aarch64-windows"
X86="$R/app/Madeira/i386-windows"
TRACKED_X86="$SRC/winebrowser-x86.exe"
mkdir -p "$OUT"

# check_pe <file> <machine hex>
check_pe() {
    python3 - "$1" "$2" <<'PY'
import struct, sys
path, want = sys.argv[1], int(sys.argv[2], 16)
data = open(path, "rb").read()
pe = struct.unpack_from("<I", data, 0x3C)[0]
assert data[pe:pe + 4] == b"PE\0\0", "not a PE image"
m = struct.unpack_from("<H", data, pe + 4)[0]
assert m == want, "machine %#x, want %#x" % (m, want)
opt = pe + 24
magic = struct.unpack_from("<H", data, opt)[0]
pe32plus = magic == 0x20B
assert magic in (0x10B, 0x20B), "optional header magic %#x" % magic
assert pe32plus == (want != 0x14C), "PE32/PE32+ does not fit the machine"
assert struct.unpack_from("<H", data, opt + 68)[0] == 2, "not a Windows GUI program"
nsec = struct.unpack_from("<H", data, pe + 6)[0]
secs = pe + 24 + struct.unpack_from("<H", data, pe + 20)[0]
sections = [struct.unpack_from("<8sIIII", data, secs + 40 * i) for i in range(nsec)]
def rva2off(rva):
    for name, vsize, vaddr, rsize, roff in sections:
        if vaddr <= rva < vaddr + max(vsize, rsize):
            return roff + rva - vaddr
    raise AssertionError("rva %#x not in a section" % rva)
dd = opt + (112 if pe32plus else 96)
imp_rva = struct.unpack_from("<I", data, dd + 8)[0]
names = []
o = rva2off(imp_rva)
while True:
    f = struct.unpack_from("<IIIII", data, o)
    if f[3] == 0:
        break
    n = rva2off(f[3])
    names.append(data[n:data.index(b"\0", n)].decode().lower())
    o += 20
bad = [n for n in names if n != "kernel32.dll" and not n.startswith("api-ms-win-crt-")]
assert not bad, "imports outside kernel32 and the C runtime: %s" % bad
PY
}

build() {   # build <clang triple> <machine> <destination>
    local cc="$MINGW/$1-w64-mingw32-clang" out="$OUT/winebrowser-$1.exe"
    echo "=== winebrowser.exe ($1) ==="
    if "$cc" -O2 -Wall -Wextra -Werror -mwindows -Wl,--no-insert-timestamp -Wl,--strip-all -I"$SRC" -o "$out" "$SRC/openurl.c" 2> "$out.err" &&
       check_pe "$out" "$2" 2>> "$out.err"; then
        cp "$out" "$3"
        echo "::notice::winebrowser.exe ($1) rebuilt ($(wc -c < "$out" | tr -d ' ') bytes) -> ${3#$R/}"
        return 0
    fi
    grep -m 20 "error\|Error\|assert" "$out.err"
    echo "::warning::winebrowser.exe ($1) did not build -- keeping the tracked program"
    return 1
}

if [ -x "$MINGW/x86_64-w64-mingw32-clang" ]; then
    build x86_64 8664 "$EC/winebrowser.exe"
    build aarch64 AA64 "$AA/winebrowser.exe"
    build i686 14C "$TRACKED_X86"
else
    echo "::warning::build-bcd-openurl: no llvm-mingw at $MINGW -- keeping the tracked programs"
fi
# The 32-bit farm: built or restored without BCD's program (Wine's winebrowser.exe is in it); put BCD's in its place.
if [ -d "$X86" ] && [ -f "$TRACKED_X86" ]; then
    cp "$TRACKED_X86" "$X86/winebrowser.exe"
    echo "::notice::winebrowser.exe (i686) -> app/Madeira/i386-windows/winebrowser.exe"
else
    echo "::warning::build-bcd-openurl: no i386 farm or no tracked 32-bit program; 32-bit programs keep Wine's winebrowser.exe"
fi
exit 0
