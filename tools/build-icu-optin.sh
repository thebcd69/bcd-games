#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# icuuc.dll and icuin.dll for 64-bit Windows programs: ICU4C 78.3 (Unicode licence) built from source for ARM64EC and shipped as
# app/Madeira/arm64ec-windows/icuuc-optin.dll and icuin-optin.dll, which a session links as icuuc.dll and icuin.dll only for a game whose file
# says env.MADEIRA_OPTIN_DLLS = icuuc,icuin (app/Madeira/WineProcessBridge.m).
#
# Why: Qt 6 programs (PySide6 and PyInstaller launchers: Bloodborne's launcher, Anki, the Albion launcher) import icuuc.dll and icuin.dll from
# Qt6Core.dll. Windows 10 (1903 and newer) has both in System32 (thin forwarders to icu.dll); Wine has neither, and the loader stops the
# program with c0000135 ("Library icuuc.dll (which is needed by ...\Qt6Core.dll) not found"). These two DLLs are ICU itself, as a Windows
# program meets it: the C API exported UNVERSIONED (u_strToUpper, ucol_open, ubrk_open, ucnv_open, ucal_open, ... not ucol_open_78), common
# in icuuc.dll (with the data), i18n in icuin.dll.
#
# Source: the ICU 78.3 release tarball as Debian's archive keeps it (icu_78.3.orig.tar.gz, from deb.debian.org with snapshot.debian.org as
# the second source; NOT GitHub), checked by SHA-256 against Debian's icu_78.3-2.dsc (Checksums-Sha256) before anything is unpacked: a
# different file stops the script by name. Its LICENSE is checked against build/icu-win/ICU-LICENSE.txt, which ships (LICENSES/ and the app's
# legal/LICENSES/). No patch: ICU's own configure and makefiles, --disable-renaming, common + i18n only. See build/icu-win/BCD-SOURCE.txt.
#
# The data (icudt78l.dat, 33 MB, the one package of the tarball: its CLDR text sources are not in it) is trimmed to what a launcher needs with
# icupkg (build/icu-win/trim-data.py): root and English, the Unicode property and normalisation data, the converters of the encodings a Windows
# program meets, the time zone and collation root data; no other locale, no word-break dictionaries, no transliteration rules. About 6.8 MB,
# linked into icuuc.dll as the symbol icudt78_dat (no icudt78.dll). Before the DLLs are built, the trimmed package must answer
# build/icu-win/icu-probe.c exactly as ICU's full data does (the probe runs on the Mac, against the host build).
#
# Needs (CI has all of it): macOS with /usr/bin/clang, /usr/bin/make (GNU make 3.81 is enough), /bin/bash, python3, curl, tar and shasum; the
# tracked llvm-mingw (clang, lld, llvm-strip, the arm64ec-w64-mingw32 and x86_64-w64-mingw32 front ends). No Wine, no network after the first
# download, nothing from GitHub, no new tool. About 2 minutes on a 10-core Mac, 5 to 10 on a 4-core runner.
#
#   MINGW=<llvm-mingw bin dir>   default <repo>/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin
#   SHIP_DIR=<dir>               default <repo>/app/Madeira/arm64ec-windows (icuuc-optin.dll and icuin-optin.dll go there)
#   WORK=<dir>                   default <repo>/build/icu-win/work (tarball, source, host tools, cross build; about 450 MB)
#   OUT=<dir>                    default <repo>/build/icu-win/out (the stripped DLLs and their stamp: what CI caches)
#   JOBS=<n>                     compile jobs, default the CPU count
#   ICU_TARBALL=<file>           an icu_78.3.orig.tar.gz already at hand (checked by SHA-256 like a download)
#   ICU_TARGET=<triple>          default arm64ec-w64-mingw32; x86_64-w64-mingw32 builds the TEST flavour that runs under a host Wine
#                                (tests/host/check-icu-optin.py): it is built and checked, never shipped, and OUT gets icu-probe.exe too
#
# OUT holding DLLs whose stamp matches the inputs (pin, this script, the trim rules, the probe, the licence, the compiler) is shipped as it
# is; anything else is built. With no llvm-mingw the script says so and exits 0 (nothing is shipped; tools/check-optin-dlls-shipped.py then
# names every list line or rule that wants the DLLs). Exit 1: the download, a checksum, the build or a check failed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MINGW="${MINGW:-$ROOT/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
SHIP_DIR="${SHIP_DIR:-$ROOT/app/Madeira/arm64ec-windows}"
WORK="${WORK:-$ROOT/build/icu-win/work}"
OUT="${OUT:-$ROOT/build/icu-win/out}"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 4)}"
ICU_TARGET="${ICU_TARGET:-arm64ec-w64-mingw32}"
ICU_TARBALL="${ICU_TARBALL:-}"
HOST_CC="${HOST_CC:-/usr/bin/clang}"
HOST_CXX="${HOST_CXX:-/usr/bin/clang++}"

ICU_VERSION=78.3
ICU_MAJOR=78
ICU_FILE="icu_$ICU_VERSION.orig.tar.gz"
# Debian's icu_78.3-2.dsc, Checksums-Sha256: 3a2e7a47...e0c0 27977255 icu_78.3.orig.tar.gz
ICU_SHA256=3a2e7a47604ba702f345878308e6fefeca612ee895cf4a5f222e7955fabfe0c0
ICU_URLS=(https://deb.debian.org/debian/pool/main/i/icu/$ICU_FILE
          https://snapshot.debian.org/archive/debian/20260510T000000Z/pool/main/i/icu/$ICU_FILE)
# The tarball's icu/LICENSE (Unicode License V3 with ICU's third-party notices), kept as received in build/icu-win/ICU-LICENSE.txt.
ICU_LICENSE_SHA256=e55522d81edc687a341a4411e0776e54ca654e90147f354a90458aaced4116af
DIR="$ROOT/build/icu-win"
LICENSE_FILE="$DIR/ICU-LICENSE.txt"
TRIM="$DIR/trim-data.py"
PROBE="$DIR/icu-probe.c"
EXPECTED="$DIR/icu-probe.expected"
CHECK="$DIR/check-icu-dlls.py"
SELF="$ROOT/tools/$(basename "${BASH_SOURCE[0]}")"

die() { echo "build-icu-optin: $*" >&2; exit 1; }
case "$WORK" in /*) ;; *) die "WORK must be an absolute path (-ffile-prefix-map names it)" ;; esac
sha() { shasum -a 256 "$1" | cut -c1-64; }

CC="$MINGW/$ICU_TARGET-clang"
if [ ! -x "$CC" ]; then
    echo "build-icu-optin: no llvm-mingw ($ICU_TARGET-clang) at $MINGW; icuuc-optin.dll and icuin-optin.dll are not built"
    exit 0
fi
case "$ICU_TARGET" in
    arm64ec-w64-mingw32) FLAVOUR=arm64ec; SHIPS=1 ;;
    x86_64-w64-mingw32)  FLAVOUR=x86_64;  SHIPS=0 ;;
    *) die "ICU_TARGET $ICU_TARGET: only arm64ec-w64-mingw32 (ships) and x86_64-w64-mingw32 (test flavour) are supported" ;;
esac
stamp() {
    { echo "icu $ICU_VERSION $ICU_SHA256 $ICU_TARGET"
      shasum -a 256 "$SELF" "$TRIM" "$PROBE" "$EXPECTED" "$LICENSE_FILE" | cut -c1-64
      "$CC" --version | head -1; } | shasum -a 256 | cut -c1-64
}
STAMP="$(stamp)"
ship() {
    python3 -I "$CHECK" --machine "$FLAVOUR" "$OUT/icuuc.dll" "$OUT/icuin.dll"
    if [ "$SHIPS" != 1 ]; then
        echo "build-icu-optin: the $ICU_TARGET flavour is for tests; nothing shipped (DLLs and icu-probe.exe in $OUT)"
        return 0
    fi
    mkdir -p "$SHIP_DIR"
    for d in icuuc icuin; do
        cp "$OUT/$d.dll" "$SHIP_DIR/$d-optin.dll.tmp"
        mv "$SHIP_DIR/$d-optin.dll.tmp" "$SHIP_DIR/$d-optin.dll"
    done
    echo "build-icu-optin: shipped $SHIP_DIR/icuuc-optin.dll ($(wc -c < "$SHIP_DIR/icuuc-optin.dll" | tr -d ' ') bytes) and icuin-optin.dll" \
         "($(wc -c < "$SHIP_DIR/icuin-optin.dll" | tr -d ' ') bytes), ICU $ICU_VERSION"
}
if [ -f "$OUT/icuuc.dll" ] && [ -f "$OUT/icuin.dll" ] && [ "$(cat "$OUT/stamp" 2>/dev/null)" = "$STAMP" ] \
   && { [ "$SHIPS" = 1 ] || [ -f "$OUT/icu-probe.exe" ]; }; then
    echo "build-icu-optin: $OUT holds current DLLs (stamp $STAMP)"
    ship
    exit 0
fi

# 1. The tarball, by SHA-256. A download that fails tries the next source; a file that arrives and is not the pinned one stops the script.
TAR="$WORK/dl/$ICU_FILE"
verify_tar() {   # verify_tar FILE WHERE
    local got; got="$(sha "$1")"
    [ "$got" = "$ICU_SHA256" ] || die "ICU_SHA256 mismatch: $ICU_FILE ($2) is $got, the pin is $ICU_SHA256 (Debian's icu_$ICU_VERSION-2.dsc); the file is refused"
}
mkdir -p "$WORK/dl"
if [ -n "$ICU_TARBALL" ]; then
    [ -f "$ICU_TARBALL" ] || die "ICU_TARBALL $ICU_TARBALL is not a file"
    verify_tar "$ICU_TARBALL" "ICU_TARBALL"
    TAR="$ICU_TARBALL"
elif [ -f "$TAR" ] && [ "$(sha "$TAR")" = "$ICU_SHA256" ]; then
    :
else
    rm -f "$TAR" "$TAR.part"
    for url in "${ICU_URLS[@]}"; do
        if bash "$ROOT/tools/fetch-cached.sh" "$TAR.part" "$url" -fsSL --retry 3 --connect-timeout 30 --max-time 900 && [ -s "$TAR.part" ]; then
            if [ "$(sha "$TAR.part")" != "$ICU_SHA256" ]; then
                got="$(sha "$TAR.part")"; rm -f "$TAR.part"
                die "ICU_SHA256 mismatch: $ICU_FILE from $url is $got, the pin is $ICU_SHA256 (Debian's icu_$ICU_VERSION-2.dsc); the file is refused"
            fi
            mv "$TAR.part" "$TAR"
            break
        fi
        echo "build-icu-optin: $url could not be downloaded" >&2
        rm -f "$TAR.part"
    done
    [ -f "$TAR" ] || die "$ICU_FILE could not be downloaded from any source; ICU_TARBALL=<file> names one already at hand"
fi

# 2. The source, unpacked fresh for a new tarball, with its licence checked against the one that ships.
SRC="$WORK/src"
if [ "$(cat "$WORK/src.stamp" 2>/dev/null)" != "$ICU_SHA256" ]; then
    rm -rf "$SRC" "$WORK/src.stamp" "$WORK/host" "$WORK/data" "$WORK/probe-host"
    mkdir -p "$SRC"
    tar -xzf "$TAR" -C "$SRC"
    echo "$ICU_SHA256" > "$WORK/src.stamp"
fi
ICU="$SRC/icu"
[ -f "$ICU/source/configure" ] || die "$ICU_FILE has no icu/source/configure: not an ICU source tarball"
[ "$(sha "$ICU/LICENSE")" = "$ICU_LICENSE_SHA256" ] || die "ICU_LICENSE_SHA256 mismatch: the tarball's LICENSE is $(sha "$ICU/LICENSE"), the pin is $ICU_LICENSE_SHA256"
cmp -s "$ICU/LICENSE" "$LICENSE_FILE" || die "$LICENSE_FILE is not the tarball's LICENSE byte for byte (it ships as LICENSES/ICU-LICENSE.txt)"

# 3. ICU for the Mac: its tools (icupkg trims the data) and its static libraries (the probe that judges the trimmed data links them).
HOST="$WORK/host"
if [ ! -x "$HOST/bin/icupkg" ] || [ ! -f "$HOST/lib/libicuuc.a" ]; then
    rm -rf "$HOST"; mkdir -p "$HOST"
    (cd "$HOST" && CC="$HOST_CC" CXX="$HOST_CXX" "$ICU/source/configure" --disable-samples --disable-tests --disable-extras --disable-layoutex \
        --enable-static --disable-shared --disable-renaming) > "$WORK/host-configure.log" 2>&1 || { tail -30 "$WORK/host-configure.log"; die "the host configure failed"; }
    make SHELL=/bin/bash -j"$JOBS" -C "$HOST" > "$WORK/host-build.log" 2>&1 || { tail -30 "$WORK/host-build.log"; die "the host build failed"; }
fi

# 4. The data: the tarball's package trimmed by the rules of trim-data.py. icupkg checks that no kept item needs a removed one; the five
# word-break dictionaries that brkitr/root.res names are the one deliberate exception, so any other complaint stops the script.
DATA="$WORK/data"
PKG="$ICU/source/data/in/icudt${ICU_MAJOR}l.dat"
[ -f "$PKG" ] || die "$PKG is not in the tarball"
rm -rf "$DATA"; mkdir -p "$DATA"
"$HOST/bin/icupkg" -l "$PKG" > "$DATA/all.lst"
python3 -I "$TRIM" "$DATA/all.lst" > "$DATA/remove.lst"
python3 -I "$TRIM" --keep "$DATA/all.lst" > "$DATA/keep.lst"
"$HOST/bin/icupkg" --ignore-deps -r "$DATA/remove.lst" "$PKG" "$DATA/icudt${ICU_MAJOR}l.dat" > "$DATA/icupkg.log" 2>&1 || { cat "$DATA/icupkg.log"; die "icupkg could not trim the data"; }
"$HOST/bin/icupkg" -w "$DATA/icudt${ICU_MAJOR}l.dat" "$DATA/recheck.dat" > "$DATA/deps.log" 2>&1 || true
rm -f "$DATA/recheck.dat"
unmet="$(grep -v -E '^Item brkitr/root\.res depends on missing item brkitr/(cj|khmer|lao|burmese|thai)dict\.dict$' "$DATA/deps.log" || true)"
[ -z "$unmet" ] || { echo "$unmet" >&2; die "the trimmed data has an item that needs a removed one (add the removed item to the keep rules in trim-data.py)"; }
echo "build-icu-optin: data trimmed: $(wc -l < "$DATA/keep.lst" | tr -d ' ') of $(wc -l < "$DATA/all.lst" | tr -d ' ') items, $(wc -c < "$DATA/icudt${ICU_MAJOR}l.dat" | tr -d ' ') bytes of $(wc -c < "$PKG" | tr -d ' ')"

# 5. The probe on the Mac with the trimmed data: it must answer what it answers with ICU's full data (lines "info:" differ by design).
HP="$WORK/probe-host"
rm -rf "$HP"; mkdir -p "$HP"
"$HOST_CC" -std=c11 -O1 -DU_DISABLE_RENAMING=1 -I"$ICU/source/common" -I"$ICU/source/i18n" -o "$HP/icu-probe" "$PROBE" \
    "$HOST/lib/libicui18n.a" "$HOST/lib/libicuuc.a" "$HOST/stubdata/libicudata.a" -lc++ -lm > "$HP/build.log" 2>&1 || { cat "$HP/build.log"; die "the probe does not build for the Mac"; }
ICU_DATA="$DATA" "$HP/icu-probe" > "$HP/all.txt" || { cat "$HP/all.txt"; die "the probe failed with the trimmed data"; }
grep -v '^info:' "$HP/all.txt" > "$HP/out.txt" || true
diff -u "$EXPECTED" "$HP/out.txt" || die "the trimmed data answers differently from $EXPECTED (ICU's full data)"

# 6. The data as an object for the target: icudt78_dat, the symbol ICU's udata.cpp looks the common data up by, in .rdata (16-byte aligned, as
# ICU's own packaging does). The assembler pulls the file in with .incbin, so no 40 MB C array is compiled.
B="$WORK/$ICU_TARGET"
rm -rf "$B"; mkdir -p "$B/lib" "$B/data-embed"
cp "$DATA/icudt${ICU_MAJOR}l.dat" "$B/data-embed/"
cat > "$B/data-embed/icudt${ICU_MAJOR}_dat.S" <<ASM
	.section .rdata,"dr"
	.globl icudt${ICU_MAJOR}_dat
	.balign 16
icudt${ICU_MAJOR}_dat:
	.incbin "icudt${ICU_MAJOR}l.dat"
ASM
(cd "$B/data-embed" && "$CC" -c -x assembler -o "icudt${ICU_MAJOR}_dat.o" "icudt${ICU_MAJOR}_dat.S") || die "the data object did not assemble"

# 7. ICU for Windows: ICU's own configure and makefiles with the cross compiler, common and i18n only, shared (the DLLs' exports are the C and C++
# API). No renaming: unversioned C names. The runtime is linked in (no libc++.dll, no libunwind.dll). The makefile fragment of MinGW names the
# DLLs icuuc78.dll and icuin78.dll and links the data as icudt78.dll: SO_TARGET_VERSION_MAJOR= drops the 78, LIBICUDT puts the data object in
# icuuc.dll in place of the data DLL, LIBICUUC links icuin to the import library icuuc made. SHELL=/bin/bash because the fragment's dependency
# rule uses echo -n, which macOS's /bin/sh prints as text. -ffile-prefix-map keeps the build machine's paths out of the DLLs, and
# --no-insert-timestamp leaves the PE time stamp at 0: the same inputs give the same bytes (two builds were compared).
LD="-static-libgcc -static-libstdc++ -Wl,--no-insert-timestamp"
(cd "$B" && CC="$CC" CXX="$MINGW/$ICU_TARGET-clang++" AR="$MINGW/llvm-ar" RANLIB="$MINGW/llvm-ranlib" \
    CPPFLAGS="-ffile-prefix-map=$WORK=build" LDFLAGS="$LD" \
    "$ICU/source/configure" --host="$ICU_TARGET" --with-cross-build="$HOST" --disable-tools --disable-tests --disable-samples --disable-extras \
        --disable-layoutex --disable-icuio --disable-renaming --enable-shared --disable-static --with-data-packaging=archive) \
    > "$WORK/$ICU_TARGET-configure.log" 2>&1 || { tail -30 "$WORK/$ICU_TARGET-configure.log"; die "the $ICU_TARGET configure failed"; }
make SHELL=/bin/bash -j"$JOBS" -C "$B/common" SO_TARGET_VERSION_MAJOR= LDFLAGS="$LD" LIBICUDT="$B/data-embed/icudt${ICU_MAJOR}_dat.o" \
    > "$WORK/$ICU_TARGET-common.log" 2>&1 || { tail -30 "$WORK/$ICU_TARGET-common.log"; die "icuuc.dll did not build"; }
make SHELL=/bin/bash -j"$JOBS" -C "$B/i18n" SO_TARGET_VERSION_MAJOR= LDFLAGS="$LD" LIBICUUC="$B/lib/libicuuc.dll.a" \
    > "$WORK/$ICU_TARGET-i18n.log" 2>&1 || { tail -30 "$WORK/$ICU_TARGET-i18n.log"; die "icuin.dll did not build"; }
[ -f "$B/lib/icuuc.dll" ] && [ -f "$B/lib/icuin.dll" ] || die "the makefiles did not leave lib/icuuc.dll and lib/icuin.dll"

# 7b. No runtime pseudo relocations: MinGW auto-import would have the DLL rewrite its own read-only data at load time (for a data symbol it
# takes from the other DLL); lld resolved every import through the import tables here (the list is empty in all four DLLs built and compared:
# ARM64EC and x86-64, icuuc and icuin), and a later toolchain that does not must be looked at before the DLL goes to a phone.
for d in icuuc icuin; do
    range="$("$MINGW/llvm-nm" "$B/lib/$d.dll" | awk '$3 == "__RUNTIME_PSEUDO_RELOC_LIST__" { a = $1 } $3 == "__RUNTIME_PSEUDO_RELOC_LIST_END__" { b = $1 } END { print a, b }')"
    [ -n "${range% *}" ] && [ "${range% *}" = "${range#* }" ] || die "$d.dll has runtime pseudo relocations ($range): its imports of data from icuuc.dll / the runtime need auto-import fix-ups at load time"
done

# 8. A Qt-like program built against the two DLLs' import libraries: proves they link the way Qt6Core.dll links them (unversioned names).
mkdir -p "$B/probe"
"$CC" -std=c11 -O1 -DU_DISABLE_RENAMING=1 -I"$ICU/source/common" -I"$ICU/source/i18n" -o "$B/probe/icu-probe.exe" "$PROBE" \
    "$B/lib/libicuin.dll.a" "$B/lib/libicuuc.dll.a" > "$B/probe/build.log" 2>&1 || { cat "$B/probe/build.log"; die "the probe does not link against the new DLLs"; }

# 9. Stripped, checked (the exports of every public C function of both header sets among them), stamped, shipped.
mkdir -p "$OUT"
rm -f "$OUT/stamp" "$OUT/icu-probe.exe"
for d in icuuc icuin; do "$MINGW/llvm-strip" -o "$OUT/$d.dll" "$B/lib/$d.dll"; done
python3 -I "$CHECK" --machine "$FLAVOUR" --icu-source "$ICU/source" "$OUT/icuuc.dll" "$OUT/icuin.dll"
[ "$SHIPS" = 1 ] || cp "$B/probe/icu-probe.exe" "$OUT/icu-probe.exe"
echo "$STAMP" > "$OUT/stamp"
ship
