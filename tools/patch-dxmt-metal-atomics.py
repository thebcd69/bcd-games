#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 BCD contributors
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Added for Xcode 27 / native iPhone Duo builds, 2026-11-04.
"""Use Metal's public atomic operation rather than a compiler-private intrinsic.

Xcode 27's Metal compiler changed the private intrinsic's argument count.
The public overload preserves the relaxed threadgroup integer increment and
also compiles in the project's existing Metal 3.1 language mode.
"""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] /
                    "dxmt/src/airconv/shaders/air_tessellation.metal")
parser.add_argument("--verify", action="store_true")
args = parser.parse_args()
old = "return __metal_atomic_fetch_add_explicit(out_count, 1, int(memory_order_relaxed), __METAL_MEMORY_SCOPE_THREADGROUP__);"
new = "return atomic_fetch_add_explicit(reinterpret_cast<threadgroup atomic_int *>(out_count), 1, memory_order_relaxed);"
text = args.source.read_text()
if text.count(new) == 1 and old not in text:
    print("Metal threadgroup atomic: public overload is present")
elif text.count(old) == 1 and not args.verify:
    args.source.write_text(text.replace(old, new))
    print("Metal threadgroup atomic: replaced the compiler-private intrinsic")
else:
    raise SystemExit("Metal atomic anchor changed or the public overload is missing; review the shader before building")
