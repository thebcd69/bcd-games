#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""D3D9 fixed-function light array cleared before the enabled lights are packed
(dxmt 0ec3585 by alantangok); the DXMT frontend's source, no device.

The generated fixed-function vertex shader evaluates all eight light slots and masks the inactive
ones with a zero attenuation. PackDrawConstants uploaded only the enabled slots into ring memory
that is recycled, so a stale NaN in an unused slot survives multiplication by zero and corrupts a
colour channel (upstream: RAN character select, magenta barrel frames 6 of 19 -> 0 of 19 on an
iPad Air 5). The change clears the whole light array (8 x 112 bytes at ffp_base + 256) first; the
enabled lights are packed the same way after it and the neighbouring uniforms are not touched.

1. Source: exactly one memset of the 8 x 112 byte array, directly after the ambient copy and before
   the loop over the enabled lights (so every enabled light is written after the clear).
2. dxmt/tests/d3d9/check_ffp_light_upload.py (upstream's host check: the production packer cut out
   of d3d9_device.cpp, run under ASan/UBSan on NaN-poisoned storage for 0..8 active lights) passes
   on the current source, and FAILS on the same source with the clear removed (the check bites).
SKIP (exit 0) without the dxmt sources or clang++.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src_path = root / "dxmt/src/d3d9/d3d9_device.cpp"
test_path = root / "dxmt/tests/d3d9/check_ffp_light_upload.py"
if not src_path.is_file():
    print("SKIP: no dxmt checkout (run in the full tree)")
    sys.exit(0)
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


src = src_path.read_text()
clear = "    std::memset(ffp_base + 256, 0, 8 * 112);\n"
amb = "    std::memcpy(ffp_base + 240, amb, sizeof(amb));\n"
loop = "    for (uint32_t li = 0; li < pod.ffp_light_count; ++li) {\n"
check("exactly one clear of the 8 x 112 byte light array", src.count("std::memset(ffp_base + 256, 0, 8 * 112)") == 1)
if src.count("std::memset(ffp_base + 256, 0, 8 * 112)") == 1:
    i = src.index(clear)
    check("the clear sits after the ambient copy and before the loop over the enabled lights",
          src.index(amb) < i < src.index(loop, i) and src.index(loop, i) - i < 400)
check("the light layout is unchanged: 28 floats (112 bytes) per slot at ffp_base + 256 + 112 * li",
      "ffp_base + 256 + li * 112" in src or "ffp_base + 256 + 112 * li" in src or "ffp_base + 256 + li * sizeof(blk)" in src)

if not test_path.is_file() or not shutil.which("clang++"):
    print("SKIP: the ASan/UBSan harness needs dxmt/tests/d3d9/check_ffp_light_upload.py and clang++")
else:
    r = subprocess.run([sys.executable, str(test_path), str(src_path)], capture_output=True, text=True)
    print(r.stdout.rstrip())
    check("upstream's host check passes on the current source", r.returncode == 0)
    with tempfile.TemporaryDirectory() as t:
        base = Path(t) / "d3d9_device.cpp"
        base.write_text(src.replace(clear, "", 1))
        r2 = subprocess.run([sys.executable, str(test_path), str(base)], capture_output=True, text=True)
        check("the same check FAILS on the source with the clear removed (it bites)", r2.returncode != 0)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("this check runs in the workflow", "tests/host/check-dxmt-d9-ffp-lights.py" in wf)
print("FAILED: D3D9 fixed-function light upload" if failures else "PASS: D3D9 fixed-function light upload")
sys.exit(1 if failures else 0)
