#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
#
# fetch-cached.sh OUT URL [curl options...]
#
# Download URL to OUT with the given curl options, as the workflow's plain `curl ... -o OUT URL` did, but on a machine that
# keeps a download cache (BCD_DOWNLOAD_CACHE, set by a local runner) a file whose server copy has not changed is not
# downloaded again; a new version or a damaged copy is (2026-11-19).
#   - "Changed" is asked with one HEAD request (redirects followed): the final URL, ETag, Last-Modified and size must all be
#     the ones of the cached copy (aka.ms names the newest VC++ runtime; a new release has a new final URL and size).
#   - A cached copy is used only if its SHA-256 is still the one recorded when it was downloaded; otherwise it is fetched again.
#   - If the server cannot be asked or the download fails, the last good copy is used and a ::warning:: says so (a slow or
#     dropping line; before this a failed download built an IPA without the VC++ DLLs).
# Without BCD_DOWNLOAD_CACHE (GitHub's runners) it is exactly the plain curl call.
set -u
OUT=${1:?usage: fetch-cached.sh OUT URL [curl options...]}
URL=${2:?usage: fetch-cached.sh OUT URL [curl options...]}
shift 2

if [ -z "${BCD_DOWNLOAD_CACHE:-}" ]; then
    exec curl "$@" -o "$OUT" "$URL"
fi

mkdir -p "$BCD_DOWNLOAD_CACHE" || exec curl "$@" -o "$OUT" "$URL"
KEY=$(printf '%s' "$URL" | shasum -a 256 | cut -c1-16)
FILE="$BCD_DOWNLOAD_CACHE/$KEY-$(basename "${URL%%\?*}")"
META="$FILE.meta"

# the server's view now: final URL, ETag, Last-Modified, Content-Length of the last response
remote() {
    curl -fsSIL --max-time 30 -w '%{url_effective}\n' "$URL" 2>/dev/null | tr -d '\r' | awk '
        tolower($1) == "etag:" { e = $2 } tolower($1) == "last-modified:" { $1 = ""; m = $0 }
        tolower($1) == "content-length:" { l = $2 } /^https?:\/\// { u = $0 }
        /^HTTP\// { e = ""; m = ""; l = "" }
        END { if (u != "") printf "url=%s\netag=%s\nmodified=%s\nlength=%s\n", u, e, m, l }'
}
good_copy() {   # the cached file is there and still the bytes recorded at download
    [ -s "$FILE" ] && [ -f "$META" ] || return 1
    [ "$(shasum -a 256 "$FILE" | cut -d' ' -f1)" = "$(sed -n 's/^sha256=//p' "$META")" ]
}
use_copy() {
    cp -f "$FILE" "$OUT" && echo "fetch-cached: $(basename "$FILE") from the cache ($1)"
}

NOW=$(remote)
if good_copy && [ -n "$NOW" ] && [ "$NOW" = "$(grep -v '^sha256=' "$META")" ]; then
    use_copy "unchanged on the server"; exit $?
fi

if curl "$@" -o "$FILE.part" "$URL" && [ -s "$FILE.part" ]; then
    mv -f "$FILE.part" "$FILE"
    { [ -n "$NOW" ] && echo "$NOW"; echo "sha256=$(shasum -a 256 "$FILE" | cut -d' ' -f1)"; } > "$META"
    cp -f "$FILE" "$OUT" && echo "fetch-cached: $(basename "$FILE") downloaded ($(wc -c < "$FILE" | tr -d ' ') bytes)"
    exit $?
fi
rc=$?
rm -f "$FILE.part"
if good_copy; then
    echo "::warning::fetch-cached: $URL could not be downloaded; the last good copy is used"
    use_copy "download failed"; exit $?
fi
[ "$rc" -ne 0 ] || rc=1
exit "$rc"
