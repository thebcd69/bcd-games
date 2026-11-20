#!/bin/bash
# Sign the IPA with the owner's certificate and put it in the owner's PRIVATE
# Backblaze B2 bucket for over-the-air install (tap, iOS asks "Install Madeira?").
#
# Requires the secrets below. Both app identities use the same certificate and
# provisioning profile; no profile coverage check is performed.
#
#   B2_KEY_ID, B2_APP_KEY   Backblaze B2 application key (Read and Write)
#   B2_S3_ENDPOINT          the bucket's S3 endpoint, e.g. s3.eu-central-003.backblazeb2.com
#   B2_SIGN_BUCKET          the PRIVATE bucket: Development.p12 and Development.mobileprovision
#                           at its root; the signed builds go to its ota/ folder
#   SIGN_P12_PASSWORD       the .p12 password
#
# Optional, so the owner need not log in to B2 at all: with these three the
# install page's own 7-day pre-signed link is e-mailed to the owner (Gmail /
# Google Workspace SMTP, STARTTLS on smtp.gmail.com:587). Tapping it in Mail
# opens the page in Safari without any login; "Install" installs.
#   OTA_MAIL_USER           the sending Gmail/Workspace address
#   OTA_MAIL_APP_PASSWORD   an app password of that account (not its password)
#   OTA_MAIL_TO             where to send it (usually the same address)
#
# Nothing is public. The IPA, its manifest and kurulum-<version>.html (the install page)
# sit in the private bucket; the page's button and the manifest carry 7-day
# pre-signed URLs, the longest S3 allows. The owner opens kurulum-<version>.html from the
# B2 panel or app and taps "Install". The repository and its Actions logs are
# public, so nothing here prints a URL or key material (GitHub masks secrets).
# The certificate files never enter the repository.
#
# The signing mirrors the owner's Feather setup: the app and its extensions
# KEEP their own bundle identifiers (the installed Madeira has the IPA's own
# id, so an OTA install updates it in place and keeps its data); only the
# signature, the embedded profile and the entitlements change, and every
# nested dylib/framework is re-signed. For a single-IPA invocation,
# SIGN_USE_PROFILE_BUNDLE_ID=1 instead renames them to the profile's App ID.
# Paired builds retain the requested Madeira and Madeira2 identifiers, unless
# SIGN_PRIMARY_PROFILE_BUNDLE_ID=1: then Madeira alone (and its extensions, as
# <App ID>.<suffix>) takes the profile's App ID and Madeira2 keeps its own. The
# owner's local OTA sets it (2026-11-16) so the Files folder picker grants a folder
# in place (iOS hands it only to an app whose bundle id is its signed App ID); off
# by default, so CI and every other caller sign exactly as before.
#
# Usage: tools/sign-and-publish-ota.sh <Madeira unsigned.ipa> <build number> [Madeira2 unsigned.ipa]
set -euo pipefail

IPA_IN="$1"
BUILD="$2"
IPA_SECOND="${3:-}"
DUAL=0
[ -z "$IPA_SECOND" ] || DUAL=1
# Owner, 2026-11-16 (strict): GitHub's builds keep their own identifiers, never the owner's signing App ID.
if [ "${GITHUB_ACTIONS:-}" = "true" ] && { [ "${SIGN_PRIMARY_PROFILE_BUNDLE_ID:-0}" != "0" ] || [ "${SIGN_USE_PROFILE_BUNDLE_ID:-0}" != "0" ]; }; then
  echo "::error::OTA: the profile's App ID as bundle id is for the owner's local OTA only, never on GitHub"; exit 1
fi
OTA_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ota-variants.py"

# Cloudflare R2 instead of B2 (owner's decision 2026-09-30: B2's free plan
# allows 1 GB of downloads a day, ~6 installs; R2 charges no egress). When all
# four R2 secrets exist they win; the bucket layout is the same.
#   R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
if [ -n "${R2_ACCOUNT_ID:-}" ] && [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${R2_SECRET_ACCESS_KEY:-}" ] && [ -n "${R2_BUCKET:-}" ]; then
  STORE=R2
  B2_KEY_ID="$R2_ACCESS_KEY_ID" B2_APP_KEY="$R2_SECRET_ACCESS_KEY" B2_SIGN_BUCKET="$R2_BUCKET"
  B2_S3_ENDPOINT="$R2_ACCOUNT_ID.r2.cloudflarestorage.com"
else
  STORE=B2
fi

for v in B2_KEY_ID B2_APP_KEY B2_S3_ENDPOINT B2_SIGN_BUCKET SIGN_P12_PASSWORD; do
  if [ -z "${!v:-}" ]; then
    echo "::error::OTA signing unavailable: secret $v is not set (see tools/sign-and-publish-ota.sh)"
    exit 1
  fi
done
echo "OTA: store $STORE"

W="$RUNNER_TEMP/ota"
rm -rf "$W"; mkdir -p "$W"
if [ "$STORE" = R2 ]; then
  REGION=auto
else
  REGION="$(echo "$B2_S3_ENDPOINT" | sed -n 's/^s3\.\([a-z0-9-]*\)\.backblazeb2\.com$/\1/p')"
  [ -n "$REGION" ] || { echo "::error::OTA: B2_S3_ENDPOINT is not s3.<region>.backblazeb2.com"; exit 1; }
fi
export AWS_ACCESS_KEY_ID="$B2_KEY_ID" AWS_SECRET_ACCESS_KEY="$B2_APP_KEY" AWS_DEFAULT_REGION="$REGION"
# Build 269's IPA upload died on one B2 "InternalError" in UploadPart after the
# CLI's default 2 retries; give transient store errors more room.
export AWS_RETRY_MODE=adaptive AWS_MAX_ATTEMPTS=10
EP="https://$B2_S3_ENDPOINT"
s3() { aws s3 "$@" --endpoint-url "$EP" --only-show-errors; }

# --- certificate and profile from the private bucket ---------------------------
s3 cp "s3://$B2_SIGN_BUCKET/Development.p12" "$W/cert.p12"
s3 cp "s3://$B2_SIGN_BUCKET/Development.mobileprovision" "$W/profile.mobileprovision"

KC="$W/ota.keychain-db"
KCPW="$(uuidgen)"
security create-keychain -p "$KCPW" "$KC"
security set-keychain-settings -lut 3600 "$KC"
security unlock-keychain -p "$KCPW" "$KC"
security import "$W/cert.p12" -k "$KC" -P "$SIGN_P12_PASSWORD" -A -t cert -f pkcs12 >/dev/null
security set-key-partition-list -S apple-tool:,apple: -s -k "$KCPW" "$KC" >/dev/null
security list-keychains -d user -s "$KC" $(security list-keychains -d user | tr -d '"')
trap 'security delete-keychain "$KC" 2>/dev/null || true; rm -rf "$W"' EXIT
IDENTITY="$(security find-identity -v -p codesigning "$KC" | awk 'NR==1 {print $2}')"
[ -n "$IDENTITY" ] || { echo "::error::OTA signing: no code-signing identity in the .p12"; exit 1; }

security cms -D -i "$W/profile.mobileprovision" > "$W/profile.plist"
/usr/libexec/PlistBuddy -x -c "Print :Entitlements" "$W/profile.plist" > "$W/ent.plist"
APPID_FULL="$(/usr/libexec/PlistBuddy -c "Print :Entitlements:application-identifier" "$W/profile.plist")"
TEAM="$(/usr/libexec/PlistBuddy -c "Print :TeamIdentifier:0" "$W/profile.plist")"
BUNDLE_ID="${APPID_FULL#"$TEAM".}"
EXPIRES="$(/usr/libexec/PlistBuddy -c "Print :ExpirationDate" "$W/profile.plist")"
echo "OTA signing: profile expires $EXPIRES"

# --- re-sign both identities with the same certificate/profile -----------------
sign() {  # sign <path> [entitlements]
  if [ -n "${2:-}" ]; then
    codesign -f -s "$IDENTITY" --keychain "$KC" --entitlements "$2" --generate-entitlement-der "$1"
  else
    codesign -f -s "$IDENTITY" --keychain "$KC" "$1"
  fi
}
prepare_bundle() {  # prepare_bundle <bundle> <profile-derived id> <app name>
  # A paired publication keeps the two requested, distinct identifiers; with
  # SIGN_PRIMARY_PROFILE_BUNDLE_ID=1 only Madeira's become the profile's.
  if { [ "$DUAL" -eq 0 ] && [ "${SIGN_USE_PROFILE_BUNDLE_ID:-0}" = "1" ]; } ||
     { [ "${3:-}" = Madeira ] && [ "${SIGN_PRIMARY_PROFILE_BUNDLE_ID:-0}" = "1" ]; }; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $2" "$1/Info.plist"
  fi
  rm -rf "$1/_CodeSignature"
  cp "$W/profile.mobileprovision" "$1/embedded.mobileprovision"
}
VERSION=""
APP_IDS=()
resign_ipa() {  # resign_ipa <unsigned.ipa> <app name>
  local input="$1" name="$2" root="$W/$2/x" app version ex old fw f
  mkdir -p "$root"
  ditto -x -k "$input" "$root"
  # A Mac can give built files extended attributes (com.apple.provenance on the
  # owner's local build machine). ditto stores them in the zip as ._ AppleDouble
  # files, and iOS refuses a bundle holding files its seal does not list: "its
  # integrity could not be verified" (local build 528, 1,237 such files). None
  # belongs in the app: drop them, and the attributes, before signing.
  find "$root" -name '._*' -type f -delete
  xattr -cr "$root" 2>/dev/null || true
  app="$(ls -d "$root"/Payload/*.app | head -1)"
  version="$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$app/Info.plist")"
  if [ -n "$VERSION" ] && [ "$VERSION" != "$version" ]; then
    echo "::error::OTA: both IPAs must come from the same build version"; exit 1
  fi
  VERSION="$version"
  while IFS= read -r -d '' f; do
    if file -b "$f" | grep -q "Mach-O"; then sign "$f"; fi
  done < <(find "$app" \( -name "*.dylib" -o -name "*.so" \) -type f -print0)
  while IFS= read -r -d '' fw; do sign "$fw"; done < <(find "$app" -name "*.framework" -type d -print0 | sort -rz)
  for ex in "$app"/PlugIns/*.appex "$app"/Extensions/*.appex; do
    [ -d "$ex" ] || continue
    old="$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$ex/Info.plist")"
    prepare_bundle "$ex" "$BUNDLE_ID.${old##*.}" "$name"
    sign "$ex" "$W/ent.plist"
  done
  prepare_bundle "$app" "$BUNDLE_ID" "$name"
  sign "$app" "$W/ent.plist"
  APP_IDS+=("$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$app/Info.plist")")
  codesign --verify --deep --strict "$app"
  (cd "$root" && ditto -c -k --norsrc --noextattr --noqtn --noacl --keepParent Payload "$W/$name-$VERSION.ipa")
}
NAMES=(Madeira)
INPUTS=("$IPA_IN")
if [ "$DUAL" -eq 1 ]; then NAMES+=(Madeira2); INPUTS+=("$IPA_SECOND"); fi
for i in "${!NAMES[@]}"; do resign_ipa "${INPUTS[$i]}" "${NAMES[$i]}"; done

# --- render private pages/manifests before reserving storage -------------------
B="s3://$B2_SIGN_BUCKET/ota"
TTL=604800
presign() { aws s3 presign "$1" --expires-in "$TTL" --endpoint-url "$EP"; }
if [ "$DUAL" -eq 1 ]; then
  python3 "$OTA_HELPER" init "$W/publication.json" "$VERSION" "$BUILD" --secondary
else
  python3 "$OTA_HELPER" init "$W/publication.json" "$VERSION" "$BUILD"
fi
for i in "${!NAMES[@]}"; do
  name="${NAMES[$i]}"
  MANIFEST="manifest-$VERSION.plist"
  [ "$name" = Madeira ] || MANIFEST="manifest-Madeira2-$VERSION.plist"
  OTA_IPA_URL="$(presign "$B/$name-$VERSION.ipa")" \
  OTA_MANIFEST_URL="$(presign "$B/$MANIFEST")" \
  OTA_PAGE_URL="$(presign "s3://$B2_SIGN_BUCKET/kurulum-$name-$VERSION.html")" \
  OTA_BUNDLE_ID="${APP_IDS[$i]}" \
    python3 "$OTA_HELPER" links "$W/publication.json" "$name"
done
python3 "$OTA_HELPER" render "$W/publication.json" "$W/pages"

# Count a pair as ONE build. Reserve space for both IPAs and all metadata before
# uploading either; old one-IPA versions and new pairs are removed as whole groups.
# At most 5 builds, i.e. 5 Madeira and 5 Madeira2 (owner's decision 2026-10-13):
# the oldest go first, each with its manifests and install pages.
BUDGET_GB="${OTA_BUDGET_GB:-8}"
KEEP_BUILDS=5
prune() {
  local plan="$W/prune.json" key
  if ! aws s3 ls "s3://$B2_SIGN_BUCKET/" --recursive --endpoint-url "$EP" > "$W/bucket.txt"; then
    echo "::warning::OTA: could not list the bucket, storage budget not checked"
    USED=0 LIMIT=0 BUILDS=0
    return 0
  fi
  python3 "$OTA_HELPER" prune "$1" "$BUDGET_GB" "$KEEP_BUILDS" "$VERSION" "$W/bucket.txt" > "$plan"
  while IFS= read -r key; do
    s3 rm "s3://$B2_SIGN_BUCKET/$key"
    echo "OTA: removed old build object $key (keep $KEEP_BUILDS builds, budget $BUDGET_GB GB)"
  done < <(python3 - "$plan" <<'PYEOF'
import json, sys
for key in json.load(open(sys.argv[1]))['delete']: print(key)
PYEOF
  )
  read -r USED LIMIT BUILDS < <(python3 - "$plan" <<'PYEOF'
import json, sys
p = json.load(open(sys.argv[1])); print(p['used'], p['limit'], p['builds'])
PYEOF
  )
}
SIGNED_BYTES=0
for name in "${NAMES[@]}"; do
  SIZE="$(stat -f%z "$W/$name-$VERSION.ipa" 2>/dev/null || stat -c%s "$W/$name-$VERSION.ipa")"
  SIGNED_BYTES=$((SIGNED_BYTES + SIZE))
done
META_BYTES="$(python3 - "$W/pages" <<'PYEOF'
from pathlib import Path
import sys
print(sum(p.stat().st_size for p in Path(sys.argv[1]).iterdir()))
PYEOF
)"
prune "$((SIGNED_BYTES + META_BYTES))"
if [ "${LIMIT:-0}" -gt 0 ] && [ "$USED" -gt "$LIMIT" ]; then
  echo "::error::OTA: the bucket would exceed the $BUDGET_GB GB budget with this build -- not uploading"
  exit 1
fi

# Upload both signed apps first. A second signing/upload failure never sends a
# misleading two-button email; this script reaches mail only after all uploads.
# Each IPA gets up to five tries, each in a fresh aws process: on a line that keeps dropping (build 671, 2026-11-18) one process
# used up its retry budget ("Connection was closed before we received a valid response") although the next try would get through.
for name in "${NAMES[@]}"; do
  for try in 1 2 3 4 5; do
    if [ -n "${OTA_IPA_UPLOADER:-}" ] && [ "${GITHUB_ACTIONS:-}" != true ]; then
      # Local builds only (sign-local.sh): an uploader that retries each part on its own instead of the whole file.
      "$OTA_IPA_UPLOADER" "$W/$name-$VERSION.ipa" "$EP" "$B2_SIGN_BUCKET" "ota/$name-$VERSION.ipa" && break
    else
      s3 cp "$W/$name-$VERSION.ipa" "$B/$name-$VERSION.ipa" --content-type application/octet-stream && break
    fi
    [ "$try" -eq 5 ] && { echo "::error::OTA: $name-$VERSION.ipa did not upload after 5 tries"; exit 1; }
    echo "OTA: $name-$VERSION.ipa upload try $try failed; trying again in 30 s"
    sleep 30
  done
done
for manifest in "$W/pages"/*.plist; do
  s3 cp "$manifest" "$B/$(basename "$manifest")" --content-type application/xml
done
for page in "$W/pages"/*.html; do
  s3 cp "$page" "s3://$B2_SIGN_BUCKET/$(basename "$page")" --content-type "text/html; charset=utf-8" --cache-control no-cache
done
prune 0
if [ "${LIMIT:-0}" -gt 0 ]; then
  echo "::notice::OTA: bucket holds $BUILDS builds, $(python3 -c "import sys; print(f'{int(sys.argv[1])/1e9:.2f}')" "$USED") GB of the $BUDGET_GB GB budget"
fi
echo "::notice::OTA: ${NAMES[*]} $VERSION signed and published to the private $STORE bucket"

# --- one email after the entire publication is ready --------------------------
if [ -n "${OTA_MAIL_USER:-}" ] && [ -n "${OTA_MAIL_APP_PASSWORD:-}" ] && [ -n "${OTA_MAIL_TO:-}" ]; then
  if python3 "$OTA_HELPER" mail "$W/publication.json"; then
    echo "::notice::OTA: install links for $VERSION e-mailed to the owner"
  else
    echo "::warning::OTA: e-mailing the install links failed (check OTA_MAIL_* secrets; the pages are still in the bucket)"
  fi
else
  echo "OTA: no OTA_MAIL_* secrets -- installation pages only in the bucket"
fi
