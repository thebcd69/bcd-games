#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 BCD contributors
# Added for the native Duo / Xcode 27 build, 2026-11-04.
"""Verify patch safety and compile the real tessellation shader in Metal 3.1."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--require-metal", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
patcher = root / "tools/patch-dxmt-metal-atomics.py"
shader = root / "dxmt/src/airconv/shaders/air_tessellation.metal"
old = "return __metal_atomic_fetch_add_explicit(out_count, 1, int(memory_order_relaxed), __METAL_MEMORY_SCOPE_THREADGROUP__);"

with tempfile.TemporaryDirectory(prefix="bcd-metal-atomic-") as temp:
    folder = Path(temp)
    fixture = folder / "atomic.metal"
    fixture.write_text("int get_next_index(threadgroup int *out_count) {\n  " + old + "\n}\n")
    subprocess.run([sys.executable, str(patcher), "--source", str(fixture)], check=True)
    changed = fixture.read_bytes()
    subprocess.run([sys.executable, str(patcher), "--source", str(fixture)], check=True)
    assert fixture.read_bytes() == changed, "second application changed the file"
    fixture.write_text("unrecognised shader version\n")
    result = subprocess.run([sys.executable, str(patcher), "--source", str(fixture)], capture_output=True)
    assert result.returncode != 0 and fixture.read_text() == "unrecognised shader version\n"
    subprocess.run([sys.executable, str(patcher), "--verify"], check=True)
    if shutil.which("xcrun"):
        subprocess.run(["xcrun", "-sdk", "macosx", "metal", "-std=metal3.1",
                        "--target=air64-apple-macos14.0", "-c", str(shader),
                        "-o", str(folder / "tessellation.air")], check=True)
        assert (folder / "tessellation.air").stat().st_size > 0
        print("PASS: real tessellation shader compiles in Metal 3.1; patch is idempotent and rejects unknown input")
    elif args.require_metal:
        raise SystemExit("Metal compiler is required")
    else:
        print("PASS: patch safety; Metal compile skipped (xcrun unavailable)")
