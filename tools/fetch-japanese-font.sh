#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
#
# Fetches the Japanese font a game with the Japanese locale gets (env.MADEIRA_JAPANESE = 1, app/Madeira/BCDJapanese.c): Noto Sans JP
# Regular, the SubsetOTF file of Adobe and Google's Noto Sans CJK (4.3 MB; SIL Open Font License 1.1, see
# app/Madeira/fonts-ja/NOTICE-NotoSansJP.txt), into app/Madeira/fonts-ja/, where the Xcode project's folder reference carries it
# into the app. The file is never committed (a third party's binary, CLAUDE.md "Do not commit externally supplied binaries"): it is
# downloaded, checked against the SHA-256 below and copied byte for byte. Madeira's pull request #567 (nekodesusan-prog) bundles the
# same file; its commit is the second source. The first source that answers with the right bytes wins.
#
#   tools/fetch-japanese-font.sh [destination folder]
#
# A copy kept in $BCD_JAPANESE_FONT_CACHE (default ~/madeira-local/cache/fonts-ja) is used when its hash is right, so a local build
# does not download it again. Without the file the app still builds and runs, without the Japanese locale: the game page hides its
# switch and a line set by hand changes nothing ("[japanese] ... is not in this build: the locale is not changed"), and every other
# game is as before. Always exits 0 unless the destination cannot be written; a warning line says what happened.
set -u
NAME=NotoSansJP-Regular.otf
SHA256=dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073
SIZE=4533028
URLS=(
  "https://raw.githubusercontent.com/notofonts/noto-cjk/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/SubsetOTF/JP/NotoSansJP-Regular.otf"
  "https://raw.githubusercontent.com/willfaust/Madeira/025c0ca2e59f15af653f67d59c23c7e59020b274/app/Madeira/fonts-ja/NotoSansJP-Regular.otf"
)
if [ -n "${BCD_JAPANESE_FONT_URLS:-}" ]; then read -r -a URLS <<< "$BCD_JAPANESE_FONT_URLS"; fi   # the host check's hook
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$R/app/Madeira/fonts-ja}"
CACHE="${BCD_JAPANESE_FONT_CACHE:-$HOME/madeira-local/cache/fonts-ja}"
mkdir -p "$DEST" || { echo "::error::fetch-japanese-font: cannot write $DEST"; exit 1; }

if command -v shasum >/dev/null 2>&1; then sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; else sha() { sha256sum "$1" | cut -d' ' -f1; }; fi
host() { echo "$1" | cut -d/ -f3; }
good() { [ -f "$1" ] && [ "$(wc -c < "$1" | tr -d ' ')" = "$SIZE" ] && [ "$(sha "$1")" = "$SHA256" ]; }

if good "$DEST/$NAME"; then
  echo "japanese-font :: $NAME is already in place ($SIZE bytes, SHA-256 checked)"
  exit 0
fi
rm -f "$DEST/$NAME"
if good "$CACHE/$NAME"; then
  cp "$CACHE/$NAME" "$DEST/$NAME" && echo "japanese-font :: $NAME copied from the cache" && exit 0
fi
TMP="$(mktemp -d "${TMPDIR:-/tmp}/bcd-japanese-font.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
for url in "${URLS[@]}"; do
  if curl -fsSL --retry 3 --retry-delay 2 --max-time 120 -o "$TMP/$NAME" "$url" 2>/dev/null && good "$TMP/$NAME"; then
    cp "$TMP/$NAME" "$DEST/$NAME" || { echo "::error::fetch-japanese-font: cannot write $DEST/$NAME"; exit 1; }
    mkdir -p "$CACHE" 2>/dev/null && cp "$TMP/$NAME" "$CACHE/$NAME" 2>/dev/null
    echo "::notice::japanese-font :: $NAME fetched ($SIZE bytes, SHA-256 checked) from $(host "$url")"
    exit 0
  fi
  echo "japanese-font :: no usable file from $(host "$url")" >&2
done
echo "::warning::japanese-font :: $NAME could not be fetched (or did not match its SHA-256): this build has no Japanese locale (the game page hides the switch); every other game is unchanged"
exit 0
