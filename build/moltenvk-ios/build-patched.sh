#!/bin/bash
# Portions copyright 2026 Connor Gow: code taken from https://github.com/willfaust/Madeira (pull request #462)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
# Portions copyright 2026 RageMC: code taken from https://github.com/thebcd69/bcd-games (pull request #57 by RageMC)
# under GPL-3.0-or-later with the Madeira Converter Exception and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
# GPL-3.0-or-later WITH the Madeira Converter Exception, version 1.
#
# Build the PATCHED MoltenVK (Vulkan on Metal) for iOS and stage it at app/Madeira/gl/libMoltenVK_patched.dylib, the second,
# opt-in Vulkan library: only a game whose own file says `vulkan = moltenvk-patched` loads it (WineProcessBridge.m), and then Zink
# loads it too (build/win32u-unix/opengl_ios.c), so one MoltenVK runs in the process. libMoltenVK.dylib (build.sh: plain v1.4.2, unmodified)
# is not touched and stays the library of `vulkan = moltenvk` and of Zink. A failure of this script only leaves the second library out
# of the build (the workflow step continues on error), and a game that asks for it then keeps Vulkan off and the log says so.
#
# Source (own folders, so the plain build's checkout and its External/ stay as they are):
#   toolchains/MoltenVK-patched               `git clone -b v1.4.2 https://github.com/KhronosGroup/MoltenVK`, commit
#                                              db66022459ffb663aa2b50f6b018bc2e124f5edf (checked), with patches/0001-moltenvk-enshrouded.patch
#                                              applied to the working tree: v1.4.2 plus MoltenVK commits made after it, open MoltenVK pull
#                                              requests and the fixes of github.com/nivogli/MoltenVK, and a few local fixes (the set that gets
#                                              Enshrouded, Steam 1203620, compiling its shaders on an iPad, bcd-games pull request #57).
#                                              The patch moves ExternalRevisions/SPIRV-Cross_repo_revision to aa217aeb.
#   toolchains/MoltenVK-patched-SPIRV-Cross   https://github.com/KhronosGroup/SPIRV-Cross at that commit, with
#                                              patches/spirv-cross-0001-instance-relative-patch-id.patch committed on top (instance_relative_patch_id
#                                              for tessellation's gl_PrimitiveID); fetchDependencies is given it with --spirv-cross-root.
# Everything else fetchDependencies downloads is what v1.4.2 pins (ExternalRevisions, which the patch leaves alone): cereal, Vulkan-Headers,
# SPIRV-Tools with SPIRV-Headers, Vulkan-Tools and volk. External/build is made again only when one of those pins or the SPIRV-Cross patch
# changes (the stamp External/.bcd-patched-deps); the first run takes a while, later ones only rebuild MoltenVK.
#
# Environment: MOLTENVK_PATCHED_SRC, MOLTENVK_PATCHED_SPIRV_CROSS_SRC (the two folders), MOLTENVK_PATCHED_OUT (the output folder, default
# app/Madeira/gl), MOLTENVK_JOBS (xcodebuild's -jobs).
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${MOLTENVK_PATCHED_SRC:-$R/toolchains/MoltenVK-patched}"
SPC="${MOLTENVK_PATCHED_SPIRV_CROSS_SRC:-$R/toolchains/MoltenVK-patched-SPIRV-Cross}"
OUT="${MOLTENVK_PATCHED_OUT:-$R/app/Madeira/gl}"
P="$R/build/moltenvk-ios/patches"
MVK_PATCH="$P/0001-moltenvk-enshrouded.patch"
SPC_PATCH="$P/spirv-cross-0001-instance-relative-patch-id.patch"
MVK_COMMIT=db66022459ffb663aa2b50f6b018bc2e124f5edf
SPC_COMMIT=aa217aeb6c9f0ace7a0ab233b28807edf45eb165
LIB=libMoltenVK_patched.dylib
trap 'rm -f "$OUT/$LIB.tmp"' EXIT

for f in "$MVK_PATCH" "$SPC_PATCH"; do
    [ -f "$f" ] || { echo "build-patched.sh: $f is missing" >&2; exit 1; }
done

# The destructive git commands below (reset, clean, checkout --force) run only in a repository that is the folder itself, never in the one above it.
own_repo() { [ "$(git -C "$1" rev-parse --show-toplevel 2>/dev/null)" = "$(cd "$1" && pwd -P)" ]; }

# 1. MoltenVK v1.4.2 in a folder of its own.
if [ ! -f "$SRC/fetchDependencies" ]; then
    echo "MoltenVK source not found at $SRC; cloning v1.4.2"
    mkdir -p "$(dirname "$SRC")"
    git clone -q -b v1.4.2 https://github.com/KhronosGroup/MoltenVK "$SRC"
fi
own_repo "$SRC" || { echo "$SRC is not a git repository of its own" >&2; exit 1; }
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$MVK_COMMIT" ]; then
    echo "MoltenVK at $SRC is $(git -C "$SRC" rev-parse HEAD), expected $MVK_COMMIT (v1.4.2)" >&2
    exit 1
fi

# 2. The patch, on the working tree, and nothing else. This folder belongs to this script alone: a stamp in .git holds the patch's hash and the
# hash of the whole working tree (ignored External/ and Package/ left out) as the patch left it. The same stamp: nothing to do. Anything else
# (the first run, a new patch, an interrupted or edited folder): back to the pinned commit, then the patch.
tree_id() {
    local d; d="$(mktemp -d)"
    GIT_INDEX_FILE="$d/index" git add -A . >/dev/null 2>&1 && GIT_INDEX_FILE="$d/index" git write-tree
    rm -rf "$d"
}
cd "$SRC"
MVK_STAMP="$(git rev-parse --absolute-git-dir)/bcd-patched-tree"
MVK_WANT="$(shasum -a 256 < "$MVK_PATCH" | cut -c1-16)"
if [ "$(cat "$MVK_STAMP" 2>/dev/null)" != "$MVK_WANT $(tree_id)" ]; then
    git reset -q --hard "$MVK_COMMIT"
    git clean -fdq
    git apply "$MVK_PATCH"
    printf '%s %s\n' "$MVK_WANT" "$(tree_id)" > "$MVK_STAMP"
fi
if [ "$(cat ExternalRevisions/SPIRV-Cross_repo_revision)" != "$SPC_COMMIT" ]; then
    echo "the patched MoltenVK wants SPIRV-Cross $(cat ExternalRevisions/SPIRV-Cross_repo_revision), this script has $SPC_COMMIT" >&2
    exit 1
fi

# 3. SPIRV-Cross at that commit with the instance_relative_patch_id patch committed on top (a mailbox patch: git am), the same way: a stamp
# with the patch's hash, and a clean tree on the commit made from it.
if [ ! -d "$SPC/.git" ]; then
    mkdir -p "$(dirname "$SPC")"
    git clone -q https://github.com/KhronosGroup/SPIRV-Cross "$SPC"
fi
own_repo "$SPC" || { echo "$SPC is not a git repository of its own" >&2; exit 1; }
SPC_STAMP="$(git -C "$SPC" rev-parse --absolute-git-dir)/bcd-patch-sha"
SPC_WANT="$(shasum -a 256 < "$SPC_PATCH" | cut -c1-16)"
if [ "$(cat "$SPC_STAMP" 2>/dev/null)" != "$SPC_WANT" ] || [ "$(git -C "$SPC" rev-parse HEAD^ 2>/dev/null)" != "$SPC_COMMIT" ] \
   || [ -n "$(git -C "$SPC" status --porcelain --untracked-files=no)" ]; then
    git -C "$SPC" am --abort >/dev/null 2>&1 || true
    git -C "$SPC" cat-file -e "$SPC_COMMIT^{commit}" 2>/dev/null || git -C "$SPC" fetch -q origin "$SPC_COMMIT"
    git -C "$SPC" checkout -q --force "$SPC_COMMIT"
    git -C "$SPC" clean -fdq
    git -C "$SPC" -c user.name=BCD -c user.email=noreply@users.noreply.github.com am -q "$SPC_PATCH" \
        || { git -C "$SPC" am --abort >/dev/null 2>&1 || true; echo "the SPIRV-Cross patch does not apply to $SPC_COMMIT" >&2; exit 1; }
    printf '%s\n' "$SPC_WANT" > "$SPC_STAMP"
fi

# 4. The dependencies (External/): once per set of pins and patch.
DEPS_ID="$({ find ExternalRevisions -name '*_repo_revision' | LC_ALL=C sort | xargs cat; cat "$SPC_PATCH"; } | shasum -a 256 | cut -c1-16)"
if [ ! -d External/build ] || [ "$(cat External/.bcd-patched-deps 2>/dev/null)" != "$DEPS_ID" ]; then
    rm -rf External/build External/.bcd-patched-deps
    ./fetchDependencies --ios --spirv-cross-root "$SPC"
    printf '%s\n' "$DEPS_ID" > External/.bcd-patched-deps
fi

# 5. MoltenVK. MOLTENVK_JOBS: xcodebuild's -jobs (default: Xcode's own choice). The Makefile finds xcodebuild on the PATH and turns every
# variable given on its command line into a preprocessor definition, so a shim in front of it is the way to add the flag.
if [ -n "${MOLTENVK_JOBS:-}" ]; then
    shim="$(mktemp -d)"
    printf '#!/bin/sh\nexec "%s" -jobs %s "$@"\n' "$(command -v xcodebuild)" "$MOLTENVK_JOBS" > "$shim/xcodebuild"
    chmod +x "$shim/xcodebuild"
    export PATH="$shim:$PATH"
fi
make ios

# 6. Stage it: under its own name and install name, written to a temporary file first so that a failure leaves no half-made library.
BIN="$SRC/Package/Release/MoltenVK/dynamic/MoltenVK.xcframework/ios-arm64/MoltenVK.framework/MoltenVK"
[ -f "$BIN" ] || { echo "the MoltenVK build left no $BIN" >&2; exit 1; }
mkdir -p "$OUT"
cp "$BIN" "$OUT/$LIB.tmp"
install_name_tool -id "@rpath/$LIB" "$OUT/$LIB.tmp"
mv "$OUT/$LIB.tmp" "$OUT/$LIB"
printf '%s+bcd-patched.%s spirv-cross=%s\n' "$(git -C "$SRC" describe --tags --always)" \
    "$(cat "$MVK_PATCH" "$SPC_PATCH" | shasum -a 256 | cut -c1-12)" "$(printf '%s' "$SPC_COMMIT" | cut -c1-8)" > "$OUT/libMoltenVK_patched.version"
ls -l "$OUT/$LIB" "$OUT/libMoltenVK_patched.version"
