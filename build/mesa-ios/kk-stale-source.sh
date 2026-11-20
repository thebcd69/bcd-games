#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
#
# madeira-bcd (build 676): a KosmicKrisp source tree whose patches are not the series any more is unpacked again.
#
#   kk-stale-source.sh SRC PATCH_DIR
#
# build-kosmickrisp.sh applies each patch once and leaves SRC/.madeira-applied-<name> holding the patch's SHA-256.
# Build 676 found the old way wanting: the marker was empty and named the file only, so 0035, corrected under the same
# name, was taken for applied and the library kept the old code (a kept work tree; a fresh checkout never saw it).
# Here, before the recipe runs: when a marker's patch changed, left the series, or the marker is an old empty one,
# SRC is removed and the recipe unpacks the pinned source again (fetch-playport.py, or the release tarball in
# build-kosmickrisp-bcd.sh). A tree with no marker of ours is never touched (a fresh tree, or somebody's own).
set -eu
SRC=$1
DIR=$2
[ -d "$SRC" ] || exit 0
stale=
for m in "$SRC"/.madeira-applied-*; do
    [ -f "$m" ] || continue
    name=${m##*/.madeira-applied-}
    p="$DIR/$name"
    if [ ! -f "$p" ]; then
        stale="$name is no longer in the series"
        break
    fi
    if [ "$(cat "$m")" != "$(shasum -a 256 < "$p" | cut -d' ' -f1)" ]; then
        stale="$name is not the one that was applied"
        break
    fi
done
[ -n "$stale" ] || exit 0
echo "kosmickrisp: $stale; removing $SRC so the pinned source is unpacked and patched again"
rm -rf "$SRC"
