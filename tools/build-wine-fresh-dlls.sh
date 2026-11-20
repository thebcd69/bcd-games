#!/bin/bash
# madeira-bcd (round 2026-11-15, owner's decision on the frozen 64-bit DLLs): the rebuilt 64-bit Wine set.
#
# The Wine DLLs of app/Madeira/arm64ec-windows and app/Madeira/aarch64-windows are committed binaries that no step rebuilds (tools/build-wine-extra-dlls.sh
# builds only DLLs the folders do not have, and never replaces a name that is there), so a Wine commit or a tools/patch-wine-*.py script reaches only the
# 32-bit build. This script rebuilds every Wine module of both folders (tools/wine-fresh-modules.txt) from the PINNED Wine source plus BCD's patch scripts
# (FRESH_PATCHES below) and ships the result NEXT TO the committed files as <name>-fresh.<ext>, only where the rebuild is not the committed file
# (tools/wine-fresh-pe.py same). The files are used only by a game, a list line or bcd.cfg with env.MADEIRA_WIN64_FRESH = 1 (app/Madeira/
# WineProcessBridge.m links them over the farm names; build/ntdll-unix/loader_ios.c maps ntdll-fresh*.dll); with the key off nothing here is read and
# nothing that ships changes. Nothing in the committed folders is replaced or edited.
#
# What ships, per folder:
#   <name>-fresh.dll|exe|drv|cpl   a module whose rebuild differs from the committed file. arm64ec: the pinned source is the source of the committed
#                                  file for all but ten modules (the rebuild equals it apart from time stamps, import hints and zero fill), so the files
#                                  are the modules a Wine commit after the committed build changed (combase, dinput, dinput8, kernelbase, msvcr120,
#                                  msvcrt, opengl32, setupapi, ucrtbase, wined3d) and the modules the patch scripts change. aarch64: the committed set
#                                  was built elsewhere (the 125hz fork's WSL farm; not reproducible here: 107 of 125 modules differ in code), so "equals
#                                  the committed file" cannot select. FRESH_AARCH64=patched (the default) builds and ships ntdll and the modules whose
#                                  source a patch script reaches (tools/wine-fresh-impact.py; about a dozen), =all every module that is not byte-equal,
#                                  =0 none.
#   ntdll-fresh.dll                the rebuilt ntdll (always, when it builds), in both folders
#   ntdll-fresh-sync.dll / -xsec.dll   arm64ec only: tools/patch-wine-ntdll-sync.py and tools/patch-wine-ntdll-xsec.py applied on the rebuilt source,
#                                  so a game that asks for one of those variants and for the rebuilt set gets both (loader_ios.c ios_ec_ntdll_name)
# A patch script that does not apply stops the build: a set with one patch missing is not the set that is described here. A module that does not build
# is left out (the committed file stays). Run from anywhere; needs llvm-mingw, rsync, git and the native wine tools (the build-native directory):
#   usage: build-wine-fresh-dlls.sh [native tools dir]
#   environment: MADEIRA_WIN64_FRESH_BUILD=0 skips it; FRESH_AARCH64=patched|all|0 (the ARM64 set; default patched); FRESH_EXCLUDE="a.dll b.dll" leaves modules out; WINE_FRESH_DIR (default wine-fresh, the tree and its
#   two build directories; cached between builds: only changed files and the patched ones are compiled again); SHIP_EC and SHIP_AA (the two folders).
set -u
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ "${MADEIRA_WIN64_FRESH_BUILD:-1}" = 0 ] && { echo "build-wine-fresh-dlls: skipped (MADEIRA_WIN64_FRESH_BUILD=0)"; exit 0; }
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
export PATH="$MINGW:$PATH"
if command -v brew >/dev/null 2>&1; then
    BISON_BIN="$(brew --prefix bison 2>/dev/null)/bin"
    [ -x "$BISON_BIN/bison" ] && export PATH="$BISON_BIN:$PATH"
fi
TOOLS="${1:-}"
W="$R/wine"
F="${WINE_FRESH_DIR:-$R/wine-fresh}"
SHIP_EC="${SHIP_EC:-$R/app/Madeira/arm64ec-windows}"
SHIP_AA="${SHIP_AA:-$R/app/Madeira/aarch64-windows}"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || nproc)}"
STRIP="$MINGW/llvm-strip"
PE="$R/tools/wine-fresh-pe.py"
MODULES="$R/tools/wine-fresh-modules.txt"
AARCH64="${FRESH_AARCH64:-patched}"   # patched | all | 0
START="$(date +%s)"
fail() { echo "::error::build-wine-fresh-dlls: $*"; exit 1; }
[ -x "$STRIP" ] || fail "llvm-mingw not found at $MINGW (docs/BUILDING.md)"
[ -d "$W/dlls/ntdll" ] || fail "no wine source at $W"
git -C "$W" rev-parse HEAD >/dev/null 2>&1 || fail "$W is not a git checkout (the pinned source is read with git archive)"

# --- the patch scripts: the workflow's order (.github/workflows/build-ipa.yml), the "i386 farm only" ones too (that is what this set is for: the 64-bit
# --- builds of the same changes), then the ones that wait for this set. @W@ is the staged source. Every script is idempotent and refuses a moved anchor.
FRESH_PATCHES="
patch-wine-d3dx9-volume-resource.py @W@
patch-wine-xaudio2-callbacks.py @W@
patch-wine-vkd3d-point-ident.py @W@
patch-wine-vkd3d-register-target.py @W@
patch-wine-vkd3d-keyword-names.py @W@
patch-wine-d3dx-hresult.py @W@
patch-wine-d3dcompile-diag.py @W@
patch-wine-vkd3d-hlsl-fail-dump.py @W@
patch-wine-rpc-np-diag.py @W@
patch-wine-cpu-error-diag.py @W@
patch-wine-msgbox-timeout.py @W@
patch-wine-wintrust-diag.py @W@
patch-wine-woa-recheck.py @W@/dlls/ntdll/sync.c @W@/dlls/ntdll/unix/sync.c
patch-wine-woa-wake-order.py @W@/dlls/ntdll/sync.c
patch-wine-x64-machine.py @W@
patch-wine-veh-add-log.py @W@
patch-wine-veh-next-handler.py @W@
patch-wine-msvcrt-initdiag.py @W@
patch-wine-heap-exec-flush.py @W@/dlls/ntdll/heap.c
patch-wine-loader-import-diag.py @W@
patch-wine-loader-import-appdir.py @W@
patch-wine-ntdll-rtcs-area.py @W@
patch-wine-ntdll-cxx-diag.py @W@
patch-wine-shell32-navpane-null.py @W@
patch-wine-wow64-token-null-sid.py @W@
patch-wine-kernel32-usd-direct.py @W@/dlls/kernel32/sync.c
patch-wine-xinput-vibration.py @W@/dlls/xinput1_3/main.c
patch-wine-wgi-host-pads.py @W@/dlls/windows.gaming.input
"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/wine-fresh-stage.XXXXXX")"
PIN="$(mktemp -d "${TMPDIR:-/tmp}/wine-fresh-pin.XXXXXX")"
trap 'rm -rf "$STAGE" "$PIN"' EXIT
echo "build-wine-fresh-dlls: staging the pinned source ($(git -C "$W" rev-parse --short HEAD)) and the patch scripts"
git -C "$W" archive HEAD | tar -x -C "$STAGE" || fail "git archive of the pinned wine failed"
cp -cR "$STAGE/." "$PIN/" 2>/dev/null || cp -R "$STAGE/." "$PIN/" || fail "copy of the pinned source failed"
applied=""
while read -r script args; do
    [ -n "$script" ] || continue
    # shellcheck disable=SC2086 # the argument list is split on purpose
    set -- ${args//@W@/$STAGE}
    if ! python3 "$R/tools/$script" "$@" > "$STAGE.patch.log" 2>&1; then
        cat "$STAGE.patch.log"; rm -f "$STAGE.patch.log"
        fail "$script did not apply to the pinned source; the rebuilt set is not built"
    fi
    applied="$applied $script"
done <<EOF
$FRESH_PATCHES
EOF
rm -f "$STAGE.patch.log"
# the module directories the patch scripts reach (tools/wine-fresh-impact.py): what FRESH_AARCH64=patched ships
IMPACTED=" $(python3 "$R/tools/wine-fresh-impact.py" "$PIN" "$STAGE" | tr '\n' ' ') " || fail "the changed modules could not be listed"
mkdir -p "$F"
# -rlpgoD, not -a: a file that changed gets the time of the copy, so make compiles it (an archive's time can be older than the objects).
rsync -rlpgoD --checksum --delete --exclude '/build-*' "$STAGE/" "$F/" || fail "rsync of the staged source failed"

cfg() {   # arch
    local arch="$1" B="$F/build-$1" extra=""
    [ -f "$B/config.status" ] && return 0
    [ "$arch" = aarch64 ] && extra="--enable-conhost=aarch64 --enable-rpcss=aarch64 --enable-services=aarch64 --enable-wineboot=aarch64"
    mkdir -p "$B"
    # the arm64ec line of tools/build-wine-extra-dlls.sh and build/wine-pe/build-ntdll.sh; a relative srcdir, like the committed files were built with.
    # conhost, rpcss, services and wineboot are built by configure only for the host architecture, and both folders ship them.
    # shellcheck disable=SC2086
    ( cd "$B" && ../configure --enable-archs="$arch" --enable-winegstreamer --without-x --disable-tests --without-freetype --without-gnutls \
          $extra ${TOOLS:+--with-wine-tools="$TOOLS"} ) > "$B.cfg.log" 2>&1 || { tail -20 "$B.cfg.log"; return 1; }
}

MANIFEST="$F/manifest.tsv"
: > "$MANIFEST"
shipped=0; same=0; failed=""

# wanted ARCH NAME TARGET: FRESH_AARCH64=patched builds and ships, of the ARM64 set, ntdll and the modules whose source a patch script reaches
wanted() {
    local mdir
    case " ${FRESH_EXCLUDE:-} " in *" $2 "*) [ "$2" != ntdll.dll ] && return 1 ;; esac   # FRESH_EXCLUDE="shell32.dll wined3d.dll": names left out (size)
    [ "$1" = aarch64 ] && [ "$2" != ntdll.dll ] && [ "$AARCH64" = patched ] || return 0
    mdir="$(printf '%s' "$3" | cut -d/ -f1,2)"
    case "$IMPACTED" in *" $mdir "*) return 0 ;; esac
    return 1
}

# finish_and_decide ARCH NAME BUILT SHIPDIR -> ships SHIPDIR/<stem>-fresh.<ext> unless the rebuild is the committed file
finish_and_decide() {   # ARCH NAME BUILT SHIPDIR TARGET
    local arch="$1" name="$2" built="$3" dir="$4" base stem ext out tmp keep="" pad=0x10000 mdir
    stem="${name%.*}"; ext="${name##*.}"
    out="$dir/$stem-fresh.$ext"
    tmp="$STAGE/finished-$arch-$name"
    base="$STAGE/committed-$arch-$name"
    # the committed blob when the name is tracked (the folder may hold a rebuild of it made by another step), else the file in the folder
    if git -C "$R" cat-file -e "HEAD:app/Madeira/$arch-windows/$name" 2>/dev/null; then
        git -C "$R" show "HEAD:app/Madeira/$arch-windows/$name" > "$base"
    elif [ -f "$dir/$name" ]; then
        cp "$dir/$name" "$base"
    else
        base=""
    fi
    # FRESH_AARCH64=patched: of the ARM64 set (not reproducible here) only the modules whose source a patch script changes
    if ! wanted "$arch" "$name" "$5"; then
        rm -f "$out"; printf '%s\t%s\tnot-patched (source unchanged)\t-\n' "$arch" "$name" >> "$MANIFEST"; return 0
    fi
    [ "$arch" = arm64ec ] && [ "$name" = ntdll.dll ] && pad=0x50000
    if [ -n "$base" ] && python3 "$PE" symbols "$base"; then keep="--keep-symbols"; fi
    if ! python3 "$PE" finish "$built" "$tmp" "$STRIP" $keep --pad "$pad"; then failed="$failed $arch/$name"; return 0; fi
    if [ "$name" = ntdll.dll ]; then
        # ntdll-fresh.dll always ships: the unix loader and the session bridge look for it by name
        cp "$tmp" "$out.tmp" && mv "$out.tmp" "$out"
        shipped=$((shipped + 1)); printf '%s\t%s\tntdll\t%s\n' "$arch" "$stem-fresh.$ext" "$(shasum -a 256 "$out" | cut -d' ' -f1)" >> "$MANIFEST"
        return 0
    fi
    if [ -n "$base" ] && python3 "$PE" same "$base" "$tmp" "$STRIP" > "$STAGE/same.txt"; then
        same=$((same + 1)); rm -f "$out"
        printf '%s\t%s\tsame-as-committed\t-\n' "$arch" "$name" >> "$MANIFEST"
    else
        cp "$tmp" "$out.tmp" && mv "$out.tmp" "$out"
        shipped=$((shipped + 1))
        printf '%s\t%s\t%s\t%s\n' "$arch" "$stem-fresh.$ext" "$(cut -c1-80 "$STAGE/same.txt" 2>/dev/null || echo new)" "$(shasum -a 256 "$out" | cut -d' ' -f1)" >> "$MANIFEST"
    fi
}

build_arch() {   # arch shipdir
    local arch="$1" dir="$2" B="$F/build-$1" targets="" n t m
    cfg "$arch" || { failed="$failed configure-$arch"; return 0; }
    [ "$arch" = arm64ec ] && { mkdir -p "$B/dlls/stdole2.tlb"; ln -sfn arm64ec-windows "$B/dlls/stdole2.tlb/aarch64-windows"; }
    # a tree whose generated ntdll Makefile still names the sync variant's source (a run that was stopped before it put the tree back): regenerate it
    if [ -f "$B/dlls/ntdll/Makefile" ] && grep -q sync_x64 "$B/dlls/ntdll/Makefile" && [ ! -f "$F/dlls/ntdll/sync_x64.c" ]; then touch "$F/dlls/ntdll/Makefile.in"; fi
    while IFS="$(printf '\t')" read -r a n t; do
        case "$a" in "#"*|"") continue ;; esac
        [ "$a" = "$arch" ] && wanted "$a" "$n" "$t" && targets="$targets $t"
    done < "$MODULES"
    # shellcheck disable=SC2086
    make -C "$B" -k -j"$JOBS" $targets > "$B.build.log" 2>&1
    while IFS="$(printf '\t')" read -r a n t; do
        case "$a" in "#"*|"") continue ;; esac
        [ "$a" = "$arch" ] || continue
        wanted "$arch" "$n" "$t" || continue
        if [ -f "$B/$t" ]; then finish_and_decide "$arch" "$n" "$B/$t" "$dir" "$t"; else failed="$failed $arch/$n"; fi
    done < "$MODULES"
}

build_arch arm64ec "$SHIP_EC"
# the ntdll variants on the rebuilt source (arm64ec): the patch scripts of the opt-in variants, applied on top, each verified before it ships
variant() {   # name script patched-files...
    local vname="$1" script="$2" B="$F/build-arm64ec" built="$F/build-arm64ec/dlls/ntdll/arm64ec-windows/ntdll.dll" f
    shift 2
    [ -f "$SHIP_EC/ntdll-fresh.dll" ] || return 0
    if python3 "$R/tools/$script" "$F" > "$STAGE/variant.log" 2>&1; then
        rm -f "$built" "$B"/dlls/ntdll/arm64ec-windows/*.o
        if make -C "$B" -j"$JOBS" dlls/ntdll/arm64ec-windows/ntdll.dll >> "$B.variant.log" 2>&1 && [ -f "$built" ] &&
           python3 "$PE" finish "$built" "$SHIP_EC/ntdll-fresh-$vname.dll.tmp" "$STRIP" --pad 0x50000 &&
           python3 "$R/tools/$script" --verify "$SHIP_EC/ntdll-fresh-$vname.dll.tmp" "$SHIP_EC/ntdll-fresh.dll" > "$STAGE/variant.log" 2>&1; then
            mv "$SHIP_EC/ntdll-fresh-$vname.dll.tmp" "$SHIP_EC/ntdll-fresh-$vname.dll"
            shipped=$((shipped + 1)); printf 'arm64ec\tntdll-fresh-%s.dll\tntdll variant\t%s\n' "$vname" "$(shasum -a 256 "$SHIP_EC/ntdll-fresh-$vname.dll" | cut -d' ' -f1)" >> "$MANIFEST"
        else
            rm -f "$SHIP_EC/ntdll-fresh-$vname.dll.tmp" "$SHIP_EC/ntdll-fresh-$vname.dll"; tail -5 "$STAGE/variant.log"
            failed="$failed ntdll-fresh-$vname"
        fi
    else
        cat "$STAGE/variant.log"; rm -f "$SHIP_EC/ntdll-fresh-$vname.dll"; failed="$failed ntdll-fresh-$vname(patch)"
    fi
    # back to the staged source (the objects of the variant are rebuilt next time). Plain cp, not cp -p: the restored Makefile.in must be NEWER than the
    # generated dlls/ntdll/Makefile, or make keeps the variant's rule for sync_x64.c ("No rule to make target ../dlls/ntdll/sync_x64.c").
    for f in "$@"; do
        if [ -f "$STAGE/$f" ]; then cp "$STAGE/$f" "$F/$f"; else rm -f "$F/$f"; fi
    done
    rm -f "$built" "$B"/dlls/ntdll/arm64ec-windows/*.o
}
variant sync patch-wine-ntdll-sync.py dlls/ntdll/sync.c dlls/ntdll/ntdll.spec dlls/ntdll/Makefile.in dlls/ntdll/sync_x64.c
variant xsec patch-wine-ntdll-xsec.py dlls/ntdll/signal_arm64ec.c

if [ "$AARCH64" != 0 ]; then build_arch aarch64 "$SHIP_AA"; fi

# the manifest, for the log: what ships and what equals the committed file
secs=$(( $(date +%s) - START ))
echo "::notice::rebuilt 64-bit Wine set (env.MADEIRA_WIN64_FRESH = 1): $shipped files ship next to the committed ones, $same modules equal the committed file${failed:+; not built:$failed}; patches:$applied; $secs s"
cut -f1,2,3 "$MANIFEST" | grep -v "same-as-committed" | sed 's/^/  /'
[ -n "$failed" ] && echo "::warning::rebuilt 64-bit Wine set: could not be built:$failed (the committed files stay for those)"
exit 0
