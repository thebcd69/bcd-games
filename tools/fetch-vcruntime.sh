#!/usr/bin/env bash
# Portions copyright 2026 JMRBDev: code taken from https://github.com/willfaust/Madeira (pull request #462)
# under GPL-3.0-or-later and modified for BCD in October 2026. See THIRD-PARTY-NOTICES.md.
# Fetch Microsoft's x64 Visual C++ runtime (the fourteen DLLs listed in
# tools/fetch-vcruntime.md) from VC_redist.x64.exe into app/Madeira/x86_64-vcruntime/,
# byte for byte as Microsoft ships them, then check them with
# tests/host/check-vcruntime.py.
#
# The extraction is the one the build workflow uses ("Fetch the VC++ runtime DLLs from
# Microsoft" in .github/workflows/build-ipa.yml): the download is a WiX Burn bundle, a
# small PE stub with the real payload appended after the image, so 7-Zip only ever sees
# the stub. cabextract scans the file for the embedded cabinets instead, and the layers
# inside them (cabinets, MSI packages) are peeled until every name turns up. Each
# candidate's PE header is read and only the AMD64 build is taken (the package also holds
# an ARM64 build under the same name; FEX emulates x86-64, so an ARM64 DLL in a file with
# the right name would fail silently).
#
# Needs: curl, cabextract, python3; msitools (msiextract) when a layer is an MSI.
#   brew install cabextract msitools
#
# Environment (all optional):
#   VCRUNTIME_INSTALLER  a VC_redist.x64.exe you already have; nothing is downloaded
#   VCRUNTIME_DEST       where the DLLs go (default: app/Madeira/x86_64-vcruntime)
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${VCRUNTIME_DEST:-$R/app/Madeira/x86_64-vcruntime}"
URL="https://aka.ms/vs/17/release/vc_redist.x64.exe"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/bcd-vcrt.XXXXXX" 2>/dev/null || mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

WANTED=(
    concrt140.dll
    msvcp140.dll
    msvcp140_1.dll
    msvcp140_2.dll
    msvcp140_atomic_wait.dll
    msvcp140_codecvt_ids.dll
    vcamp140.dll
    vccorlib140.dll
    vcomp140.dll
    vcruntime140.dll
    vcruntime140_1.dll
    vcruntime140_threads.dll
    mfc140.dll
    mfc140u.dll
)

if ! command -v cabextract >/dev/null 2>&1; then
    echo "error: cabextract not found (brew install cabextract msitools)" >&2
    exit 1
fi

INSTALLER="${VCRUNTIME_INSTALLER:-}"
if [ -n "$INSTALLER" ]; then
    [ -s "$INSTALLER" ] || { echo "error: VCRUNTIME_INSTALLER is empty or missing: $INSTALLER" >&2; exit 1; }
else
    INSTALLER="$TMP/VC_redist.x64.exe"
    echo "fetching VC_redist.x64.exe..."
    curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fL --retry 3 -o "$INSTALLER" "$URL"
    [ -s "$INSTALLER" ] || { echo "error: the download is empty" >&2; exit 1; }
fi
if command -v shasum >/dev/null 2>&1; then
    echo -n "installer sha256: "
    shasum -a 256 "$INSTALLER" | awk '{print $1}'
fi

X="$TMP/x"
mkdir -p "$X/a" "$X/carve" "$TMP/stage"
cabextract -q -d "$X/a" "$INSTALLER" 2>&1 | tail -3 || true

# Only if the scan came up empty: carve every cabinet out by its MSCF header and its
# own declared length.
if [ "$(find "$X/a" -type f | wc -l | tr -d ' ')" -lt 2 ]; then
    python3 - "$INSTALLER" "$X/carve" <<'PYEOF'
import struct, sys
d = open(sys.argv[1], 'rb').read()
n = off = 0
while True:
    i = d.find(b'MSCF', off)
    if i < 0:
        break
    cb = struct.unpack_from('<I', d, i + 8)[0] if i + 36 <= len(d) else 0
    if 1000 < cb <= len(d) - i:
        open('%s/c%d.cab' % (sys.argv[2], n), 'wb').write(d[i:i + cb])
        n += 1
        off = i + cb
    else:
        off = i + 4
print('carved %d cabinet(s) by MSCF header' % n)
PYEOF
fi

is_x64() {
    python3 -c '
import struct, sys
try:
    d = open(sys.argv[1], "rb").read()
    pe = struct.unpack_from("<I", d, 0x3c)[0] if len(d) > 0x40 else 0
    ok = d[pe:pe + 4] == b"PE\0\0" and struct.unpack_from("<H", d, pe + 4)[0] == 0x8664
except Exception:
    ok = False
sys.exit(0 if ok else 1)
' "$1" 2>/dev/null
}

FOUND=0
MISSING=()
collect() {
    FOUND=0
    MISSING=()
    rm -f "$TMP/stage/"*.dll
    local name base src cand
    for name in "${WANTED[@]}"; do
        base="${name%.dll}"
        src=""
        while IFS= read -r cand; do
            if is_x64 "$cand"; then src="$cand"; break; fi
        done < <(find "$X" -type f \( -iname "$base.dll*" -o -iname "F_CENTRAL_${base}_*" \) -print 2>/dev/null)
        if [ -n "$src" ]; then
            cp "$src" "$TMP/stage/$name"
            FOUND=$((FOUND + 1))
        else
            MISSING+=("$name")
        fi
    done
}

# Peel containers until all of them turn up, or nothing new appears.
for pass in 1 2 3 4; do
    BEFORE=$(find "$X" -type f 2>/dev/null | wc -l | tr -d ' ')
    while IFS= read -r f; do
        case "${f##*/}" in *.dll | *.DLL | *.exe | *.EXE | *_amd64 | *_arm64 | *_x86) continue ;; esac
        [ -d "$f.d" ] && continue
        mkdir -p "$f.d"
        cabextract -q -d "$f.d" "$f" >/dev/null 2>&1 \
            || { command -v msiextract >/dev/null 2>&1 && msiextract -C "$f.d" "$f" >/dev/null 2>&1; } \
            || rmdir "$f.d" 2>/dev/null \
            || true
    done < <(find "$X" -type f -size +20k 2>/dev/null)
    AFTER=$(find "$X" -type f 2>/dev/null | wc -l | tr -d ' ')
    collect
    echo "pass $pass: $BEFORE -> $AFTER files, $FOUND of ${#WANTED[@]} recovered"
    [ "$FOUND" -eq "${#WANTED[@]}" ] && break
    [ "$BEFORE" = "$AFTER" ] && break
done

if [ "$FOUND" -ne "${#WANTED[@]}" ]; then
    echo "error: ${#MISSING[@]} of ${#WANTED[@]} files not found in the installer:" >&2
    for m in "${MISSING[@]}"; do echo "  - $m" >&2; done
    exit 1
fi

mkdir -p "$DEST"
cp "$TMP/stage/"*.dll "$DEST/"
echo "${#WANTED[@]}/${#WANTED[@]} runtime files copied to $DEST"

VCRUNTIME_DIR="$DEST" python3 "$R/tests/host/check-vcruntime.py" --strict
