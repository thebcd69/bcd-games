#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 intraducine. This file was taken from https://github.com/willfaust/Madeira (branch playport/kosmickrisp, commit 07471e3)
# under GPL-3.0-or-later. See THIRD-PARTY-NOTICES.md.
# The experimental geometry/transform-feedback/cache stack, independent of BCD.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
: "${KK_WORK:=$HERE/.work-playport}"
export KK_WORK
export KK_PROFILE=playport
export KK_SHADER_CACHE="${KK_SHADER_CACHE:-enabled}"
export KK_MESA_SRC="$KK_WORK/mesa-b39d173ca9369c19fda2635be6655d308067e52d"
python3 "$HERE/fetch-playport.py" "$KK_WORK"
exec bash "$HERE/build-kosmickrisp.sh"
