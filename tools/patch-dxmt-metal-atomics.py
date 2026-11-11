#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 Paulo Eduardo Correa (pauloec); thebcd69/bcd-games#43
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Taken from thebcd69/bcd-games pull request #43 by Paulo Eduardo Correa (2026-11-04). Modified by BCD (thebcd69), 2026-11-11: --if-needed.
# Portions copyright 2026 Henrique Rocha (iqui27): the iPhone Duo tabletop pose, from thebcd69/bcd-games pull request #31; GPL-3.0-only, modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""Use Metal's public atomic operation rather than a compiler-private intrinsic.

Xcode 27's Metal compiler changed the private intrinsic's argument count. The public overload preserves the relaxed threadgroup
integer increment and also compiles in the project's existing Metal 3.1 language mode.

--if-needed (what CI runs): patch only when this machine's Metal compiler rejects the private intrinsic and accepts the public one;
a compiler that accepts the shader as it is (Xcode 26 and older, the one BCD builds with today) leaves the source byte for byte
alone, so the tessellation shader every DXMT build embeds is the one that has run on devices. --verify: fail unless the shader
carries the public overload (what a build with Xcode 27 needs).
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

OLD = "return __metal_atomic_fetch_add_explicit(out_count, 1, int(memory_order_relaxed), __METAL_MEMORY_SCOPE_THREADGROUP__);"
NEW = "return atomic_fetch_add_explicit(reinterpret_cast<threadgroup atomic_int *>(out_count), 1, memory_order_relaxed);"
METAL = ["xcrun", "-sdk", "macosx", "metal", "-std=metal3.1", "--target=air64-apple-macos14.0"]


def compiles(source: str) -> bool:
    """True when this machine's Metal compiler builds `source` (the whole tessellation shader), False when it rejects it. No compiler: None."""
    if not shutil.which("xcrun"):
        return None
    with tempfile.TemporaryDirectory(prefix="bcd-metal-atomic-") as folder:
        src = Path(folder) / "air_tessellation.metal"
        src.write_text(source)
        r = subprocess.run(METAL + ["-c", str(src), "-o", str(Path(folder) / "out.air")], capture_output=True)
        if r.returncode != 0 and b"metal" in r.stderr.lower() and b"unable to find utility" in r.stderr.lower():
            return None   # the Metal toolchain is not installed: not an answer about the source
        return r.returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "dxmt/src/airconv/shaders/air_tessellation.metal")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--if-needed", action="store_true")
    args = parser.parse_args(argv)
    text = args.source.read_text()
    if text.count(NEW) == 1 and OLD not in text:
        print("Metal threadgroup atomic: public overload is present")
        return 0
    if args.verify:
        raise SystemExit("Metal atomic anchor changed or the public overload is missing; review the shader before building")
    if text.count(OLD) != 1:
        raise SystemExit("Metal atomic anchor changed or the public overload is missing; review the shader before building")
    if args.if_needed:
        if compiles(text) is not False:
            print("Metal threadgroup atomic: this Metal compiler accepts the shader as it is; not patched")
            return 0
        patched = text.replace(OLD, NEW)
        if compiles(patched) is False:
            raise SystemExit("this Metal compiler rejects the shader with either form of the atomic; review the DXMT pin")
        args.source.write_text(patched)
        print("Metal threadgroup atomic: this Metal compiler rejects the private intrinsic; replaced it by the public overload")
        return 0
    args.source.write_text(text.replace(OLD, NEW))
    print("Metal threadgroup atomic: replaced the compiler-private intrinsic")
    return 0


if __name__ == "__main__":
    sys.exit(main())
