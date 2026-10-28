#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional permission: Madeira Converter Exception, version 1; see LICENSE-EXCEPTION.md.
"""Use the public Metal atomic API; Xcode 27 changed a private intrinsic.

Run before generating air_tessellation.h. The pointer, relaxed ordering and
threadgroup memory scope are unchanged. Safe to run repeatedly.
"""
from pathlib import Path
import sys

path = Path("dxmt/src/airconv/shaders/air_tessellation.metal")
old = "return __metal_atomic_fetch_add_explicit(out_count, 1, int(memory_order_relaxed), __METAL_MEMORY_SCOPE_THREADGROUP__);"
new = "return atomic_fetch_add_explicit(reinterpret_cast<threadgroup atomic_int *>(out_count), 1, memory_order_relaxed);"
source = path.read_text()
if new in source:
    print("air_tessellation.metal: public atomic API already patched")
elif source.count(old) == 1:
    path.write_text(source.replace(old, new))
    print("air_tessellation.metal: public atomic API patched")
else:
    print("::error::air_tessellation.metal: atomic anchor moved; review the DXMT pin")
    sys.exit(1)
