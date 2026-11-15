#!/bin/bash
# madeira-bcd (Windows Vulkan): Wine's winevulkan.dll and vulkan-1.dll from the wine submodule, into the bundle's vulkan folder
# (NOT the farms: those are linked into every prefix, and a program that probes for vulkan-1.dll must keep finding what it found before:
# the stand-in for a 64-bit program, no file at all for a 32-bit one). Two sets:
#   app/Madeira/vulkan/arm64ec-windows/   for 64-bit programs. The same build as upstream Madeira's build/windows-runtime/build.sh does;
#                                         build-modules.sh shares wine/build-arm64ec with the other modules.
#   app/Madeira/vulkan/i386-windows/      for 32-bit programs (build 660, docs/VULKAN.md "32-bit programs"): the same two Wine modules
#                                         for i386, from wine/build-i386, the tree of the i386 farm (build/wine-i386/build.sh keeps both
#                                         names out of the farm on purpose). When the farm came out of the CI cache the tree is not
#                                         there, so it is configured here exactly as that script does (tests/host/check-vulkan-wow64.py
#                                         compares the two configure lines); make then builds the tools and import libraries the two
#                                         DLLs need and nothing else of the farm.
# Both are PE code only: the unix side is in libntdll_unix.a and libwin32u_unix.a (tools/patch-wine-vulkan-wow64.py for 32-bit callers).
#   usage (repository root):  bash tools/build-vulkan-dlls.sh            both sets
#                             VULKAN_ARCHS=i386 bash tools/build-vulkan-dlls.sh   one of them (arm64ec | i386)
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHS="${VULKAN_ARCHS:-arm64ec i386}"
rc=0

case " $ARCHS " in *" arm64ec "*)
    if DEST="$R/app/Madeira/vulkan/arm64ec-windows" bash "$R/build/wine-pe/build-modules.sh" winevulkan vulkan-1; then
        ls -l "$R/app/Madeira/vulkan/arm64ec-windows"
    else
        echo "build-vulkan-dlls: the arm64ec set failed" >&2; rc=1
    fi ;;
esac

case " $ARCHS " in *" i386 "*)
    TC="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
    export PATH="$TC:$PATH"
    if command -v brew >/dev/null 2>&1; then
        BISON_BIN="$(brew --prefix bison 2>/dev/null)/bin"
        [ -x "$BISON_BIN/bison" ] && export PATH="$BISON_BIN:$PATH"
    fi
    B="$R/wine/build-i386"
    DEST32="$R/app/Madeira/vulkan/i386-windows"
    JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 8)}"
    STRIP="$TC/i686-w64-mingw32-strip"
    if [ ! -x "$STRIP" ]; then
        echo "build-vulkan-dlls: llvm-mingw not found at $TC (docs/BUILDING.md); the i386 set is not built" >&2; rc=1
    else
        if [ ! -f "$B/config.status" ]; then
            mkdir -p "$B"
            # the i386 farm's configure line (build/wine-i386/build.sh), word for word
            (cd "$B" && ../configure --enable-archs=i386 --without-x --without-vulkan \
                                     --without-freetype --without-gnutls --disable-tests \
                                     --enable-winegstreamer)
        fi
        T32="dlls/winevulkan/i386-windows/winevulkan.dll dlls/vulkan-1/i386-windows/vulkan-1.dll"
        # shellcheck disable=SC2086 # two targets
        if make -C "$B" -j"$JOBS" $T32; then
            mkdir -p "$DEST32"
            for t in $T32; do
                out="$DEST32/$(basename "$t")"
                cp "$B/$t" "$out.tmp"
                "$STRIP" --strip-debug "$out.tmp"
                mv "$out.tmp" "$out"
            done
            ls -l "$DEST32"
        else
            echo "build-vulkan-dlls: the i386 set failed (32-bit programs get no Vulkan from this build; 64-bit programs are not affected)" >&2; rc=1
        fi
    fi ;;
esac
exit "$rc"
