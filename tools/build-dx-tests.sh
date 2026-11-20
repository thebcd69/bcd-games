#!/bin/bash
# Builds BCD's Direct3D 9, 10 and 11 test programs (tests/dxtests) as x86-64 Windows programs with llvm-mingw
# and ships them in app/Madeira/arm64ec-windows, next to d3d12-cube-x64.exe (which tools/build-madeira-d3d12-dll.sh
# builds), so that every session finds them in C:\windows\system32 and the Wine desktop's Start menu
# (app/Madeira/BCDStartMenu.c) can start them: Start > Programs > BCD > DirectX 9 test ... DirectX 12 test.
#
# Round 602 added the graphics, sound, video and input tests (tests/dxtests/bcdtest.h), both bitnesses:
#   x86-64, into arm64ec-windows (C:\windows\system32):  d3d9-cube-x64 (the loop below), frametime-test-x64, resolution-test-x64,
#                                                        sound-test-x64, video-test-x64, controller-test-x64, input-test-x64
#   i686,   into i386-windows (C:\windows\syswow64):      d3d9-test-x86 (the same d3d9_cube.c), d3d8-test-x86, ddraw-test-x86,
#                                                        sound-test-x86, video-test-x86
# Round 603 added the MIDI test (tests/dxtests/midi_test.c, ajan48's source, winmm only): midi-test-x64 and midi-test-x86.
# 2026-11-16 added the OpenGL and Vulkan tests (tests/dxtests/gl_test.c, vulkan_test.c; the shaders of the Vulkan one are vk_shaders.h,
# made offline by tools/gen-vk-test-shaders.py), both bitnesses: gl-test-x64/-x86 and vulkan-test-x64/-x86 (tests/host/check-vk-gl-tests.py).
# The 32-bit programs are tracked in app/Madeira/i386-windows (git add -f: that folder is otherwise the CI-built Wine farm and
# ignored), so a build whose compiler is missing still ships them.
#
# A program that does not build keeps the tracked binary (a warning, not a failure): the tests are
# diagnostics and no game depends on them. The shaders of the Direct3D 10 and 11 programs are bytecode in
# tests/dxtests/cube_shaders.h, made offline by tools/gen-dx-test-shaders.sh, so nothing here compiles HLSL.
# Run from the repository root.
set -u
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
SRC="$R/tests/dxtests"
OUT="$R/build/dx-tests"
SHIP="${SHIP_DIR:-$R/app/Madeira/arm64ec-windows}"
CC="$MINGW/x86_64-w64-mingw32-clang"
mkdir -p "$OUT"

[ -x "$CC" ] || { echo "::warning::build-dx-tests: no x86_64-w64-mingw32-clang at $MINGW -- keeping the tracked test programs"; exit 0; }

# The PE header of what was built: x86-64 (0x8664), a GUI program, and imports only Windows' own DLLs.
# check_pe <file> [machine]: machine 0x8664 (x86-64, the default) or 0x14c (i386; then the large-address-aware bit must be set too).
check_pe() {
    python3 - "$1" "${2:-0x8664}" <<'PY'
import struct, sys
data = open(sys.argv[1], "rb").read()
want = int(sys.argv[2], 0)
pe = struct.unpack_from("<I", data, 0x3C)[0]
assert data[pe:pe + 4] == b"PE\0\0", "not a PE image"
machine = struct.unpack_from("<H", data, pe + 4)[0]
chars = struct.unpack_from("<H", data, pe + 22)[0]
subsystem = struct.unpack_from("<H", data, pe + 24 + 68)[0]
assert machine == want, "machine %#x is not %#x" % (machine, want)
assert subsystem == 2, "subsystem %d is not Windows GUI" % subsystem
if want == 0x14c:
    assert chars & 0x20, "the large-address-aware bit is not set (a 32-bit session's 4 GB window needs it)"
PY
}

for t in d3d9 d3d10 d3d11; do
    name="$t-cube-x64.exe"
    echo "=== $name ==="
    libs=(-luuid)
    [ "$t" = d3d9 ] || libs=(-ldxguid -luuid)
    if "$CC" -O2 -Wall -Wextra -mwindows -Wl,--no-insert-timestamp -I"$SRC" -o "$OUT/$name" "$SRC/${t}_cube.c" "${libs[@]}" 2> "$OUT/$t.err" &&
       check_pe "$OUT/$name" 2>> "$OUT/$t.err"; then
        cp "$OUT/$name" "$SHIP/$name"
        echo "::notice::$name rebuilt ($(wc -c < "$OUT/$name" | tr -d ' ') bytes) and shipped"
    else
        grep -m 20 "error\|Error" "$OUT/$t.err"
        echo "::warning::$name did not build -- keeping the tracked test"
    fi
done

# ---------------------------------------------------------------------------------------------------------------------
# Round 602: the graphics, sound, video and input tests, 64-bit and 32-bit. build_one <arch> <output name> <source> <ship dir> <libs...>
build_one() {
    local arch="$1" name="$2" src="$3" ship="$4"; shift 4
    local cc="$MINGW/$arch-w64-mingw32-clang" machine=0x8664 extra=()
    if [ "$arch" = i686 ]; then machine=0x14c; extra=(-Wl,--large-address-aware); fi
    echo "=== $name ==="
    if [ ! -x "$cc" ]; then
        echo "::warning::$name: no $arch-w64-mingw32-clang at $MINGW -- keeping the tracked test"
        return 0
    fi
    mkdir -p "$ship"
    if "$cc" -O2 -Wall -Wextra -mwindows -Wl,--no-insert-timestamp ${extra[@]+"${extra[@]}"} -I"$SRC" -o "$OUT/$name" "$SRC/$src" "$@" 2> "$OUT/$name.err" &&
       check_pe "$OUT/$name" "$machine" 2>> "$OUT/$name.err"; then
        cp "$OUT/$name" "$ship/$name"
        echo "::notice::$name rebuilt ($(wc -c < "$OUT/$name" | tr -d ' ') bytes) and shipped"
    else
        grep -m 20 "error\|Error" "$OUT/$name.err"
        echo "::warning::$name did not build -- keeping the tracked test"
    fi
}

SHIP32="${SHIP32_DIR:-$R/app/Madeira/i386-windows}"
# The Vulkan test takes Wine's own Vulkan header (wine/include/wine/vulkan.h, what winevulkan is built with): the wine submodule's include
# folder, searched only for "..." includes so that its windows.h never stands in for the toolchain's. No submodule: the tracked programs stay.
WINE_INC="${WINE_INC:-$R/wine/include}"
# 64-bit: C:\windows\system32
build_one x86_64 frametime-test-x64.exe   frametime_test.c  "$SHIP" -ldxguid -luuid
build_one x86_64 resolution-test-x64.exe  resolution_test.c "$SHIP" -ldxguid -luuid
build_one x86_64 sound-test-x64.exe       sound_test.c      "$SHIP" -lwinmm -luuid -lole32
build_one x86_64 video-test-x64.exe       video_test.c      "$SHIP" -lmfuuid -luuid -lole32
build_one x86_64 controller-test-x64.exe  controller_test.c "$SHIP" -ldinput8 -ldxguid -luuid
build_one x86_64 input-test-x64.exe       input_test.c      "$SHIP" -luuid
build_one x86_64 midi-test-x64.exe        midi_test.c       "$SHIP" -lwinmm -luser32 -lgdi32
build_one x86_64 gl-test-x64.exe          gl_test.c         "$SHIP" -lgdi32
build_one x86_64 vulkan-test-x64.exe      vulkan_test.c     "$SHIP" -iquote "$WINE_INC"
# 32-bit: C:\windows\syswow64 (d3d9_cube.c is the one source of both bitnesses of the Direct3D 9 test)
build_one i686   d3d9-test-x86.exe        d3d9_cube.c       "$SHIP32" -luuid
build_one i686   d3d8-test-x86.exe        d3d8_cube.c       "$SHIP32" -luuid
build_one i686   ddraw-test-x86.exe       ddraw_test.c      "$SHIP32" -ldxguid -luuid
build_one i686   sound-test-x86.exe       sound_test.c      "$SHIP32" -lwinmm -luuid -lole32
build_one i686   video-test-x86.exe       video_test.c      "$SHIP32" -lmfuuid -luuid -lole32
build_one i686   midi-test-x86.exe        midi_test.c       "$SHIP32" -lwinmm -luser32 -lgdi32
build_one i686   gl-test-x86.exe          gl_test.c         "$SHIP32" -lgdi32
build_one i686   vulkan-test-x86.exe      vulkan_test.c     "$SHIP32" -iquote "$WINE_INC"
