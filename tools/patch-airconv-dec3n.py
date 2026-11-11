#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Direct3D 9 vertex formats DEC3N and UDEC3 in DXMT's shader converter.

The Direct3D 9 path fetches vertices in the shader (dxso_compile.cpp calls
air::pull_vec4_from_addr). d3d9_device.cpp maps D3DDECLTYPE_DEC3N to Metal's
Int1010102Normalized (40) and D3DDECLTYPE_UDEC3 to dxmt's private value
DXSO_ATTR_FORMAT_UDEC3 (0x1000), but airconv never unpacked either:
  - DEC3N reaches unpack_fvec4_from_addr's `assert(0 && "unused")`;
  - UDEC3 has no case in pull_vec4_from_addr_checked, so `value` stays null
    and `assert(value)` fires.
Either abort ends the whole game process (Wine reports 0x80000101).
Battlefield 2 (BNVQ0G, FJ9NHA, 9YZ1F2, A5VVD3, VAF4MG, 365WWC, 7GT5WY, 841QE8,
C36DKQ, CND1VH, HF6J2J, VB2EGT: every run dies about 40 s in) and Cold Fear
(0KQ7V8, at the first 3D scene) died this way (night round 2026-11-11, agents
G1 S14, G2 S5, G3 S2).

Now, as the D3D9 specification expands them:
  - DEC3N: three signed 10-bit lanes (bits 0-9, 10-19, 20-29) divided by 511
    and clamped at -1, w = 1;
  - UDEC3: three unsigned 10-bit lanes, not normalized, w = 1, returned as int4
    (dxso_compile.cpp turns int4 into floats with SIToFP, as for UBYTE4).

Only shaders that aborted the process before change: a shader that converted
before never reached these places, so nothing cached can differ. Idempotent;
fails by name if an anchor moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/airconv/air_operations.cpp")
MARKER = "madeira-bcd: DEC3N and UDEC3 vertex formats"

DEC3N_OLD = """    co_return builder.CreateShuffleVector(_xy, _zw, {0, 1, 2, 3});
  }

  std::string op;
"""

DEC3N_NEW = """    co_return builder.CreateShuffleVector(_xy, _zw, {0, 1, 2, 3});
  }
  /* madeira-bcd: DEC3N and UDEC3 vertex formats -- D3DDECLTYPE_DEC3N
   * (d3d9_device.cpp maps it to Int1010102Normalized): three signed 10-bit
   * lanes, x bits 0-9, y 10-19, z 20-29, each divided by 511 and clamped at -1
   * as SNORM is, and w = 1 (Direct3D 9 has no w for this type). This format
   * used to assert(0) "unused" below, which aborted Battlefield 2 and Cold
   * Fear. */
  if (format == MTLAttributeFormat::Int1010102Normalized) {
    pvalue raw = co_yield load_from_device_buffer(types._int, base_addr, aligned_offest, 0, 4);
    pvalue lane[3];
    for (int i = 0; i < 3; i++) {
      pvalue shifted = builder.CreateShl(raw, builder.getInt32(22 - 10 * i));
      pvalue signext = builder.CreateAShr(shifted, builder.getInt32(22));
      pvalue scaled = builder.CreateFDiv(builder.CreateSIToFP(signext, types._float), co_yield get_float(511.0f));
      pvalue low = co_yield get_float(-1.0f);
      lane[i] = builder.CreateSelect(builder.CreateFCmpOLT(scaled, low), low, scaled);
    }
    pvalue v = builder.CreateInsertElement(llvm::PoisonValue::get(types._float4), lane[0], (int)0);
    v = builder.CreateInsertElement(v, lane[1], (int)1);
    v = builder.CreateInsertElement(v, lane[2], (int)2);
    co_return builder.CreateInsertElement(v, co_yield get_float(1), (int)3);
  }

  std::string op;
"""

UDEC3_OLD = """    value = co_yield unpack_fvec4_from_addr(format, base_addr, byte_offset);
    break;
  case MTLAttributeFormat::Invalid:
    break;
  }
  assert(value);
"""

UDEC3_NEW = """    value = co_yield unpack_fvec4_from_addr(format, base_addr, byte_offset);
    break;
  /* madeira-bcd: DEC3N and UDEC3 vertex formats -- dxmt's private value for
   * D3DDECLTYPE_UDEC3 (DXSO_ATTR_FORMAT_UDEC3 in airconv_public.h): three
   * unsigned 10-bit lanes, NOT normalized, and w = 1, as int4 (the DXSO
   * codegen makes floats of an int4). It had no case: value stayed null and
   * assert(value) aborted the game. */
  case (MTLAttributeFormat)0x1000: {
    pvalue raw = co_yield load_from_device_buffer(types._int, base_addr, byte_offset, 0, 4);
    pvalue mask = builder.getInt32(0x3ff);
    value = builder.CreateInsertElement(llvm::PoisonValue::get(types._int4), builder.CreateAnd(raw, mask), (int)0);
    value = builder.CreateInsertElement(value, builder.CreateAnd(builder.CreateLShr(raw, builder.getInt32(10)), mask), (int)1);
    value = builder.CreateInsertElement(value, builder.CreateAnd(builder.CreateLShr(raw, builder.getInt32(20)), mask), (int)2);
    value = builder.CreateInsertElement(value, co_yield get_int(1), (int)3);
    break;
  }
  case MTLAttributeFormat::Invalid:
    break;
  }
  assert(value);
"""


def main():
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(".")
    path = root / PATH
    s = path.read_text()
    if MARKER in s:
        print("air_operations.cpp: DEC3N and UDEC3 vertex formats already patched")
        return 0
    for old, what in ((DEC3N_OLD, "unpack_fvec4_from_addr's Short4Normalized block"),
                      (UDEC3_OLD, "pull_vec4_from_addr_checked's format switch")):
        if s.count(old) != 1:
            sys.stderr.write("patch-airconv-dec3n: the %s moved (%d matches); not patched\n" % (what, s.count(old)))
            return 1
    if 'assert(0 && "unused");' not in s:
        sys.stderr.write("patch-airconv-dec3n: the Int1010102Normalized assert moved; not patched\n")
        return 1
    s = s.replace(DEC3N_OLD, DEC3N_NEW).replace(UDEC3_OLD, UDEC3_NEW)
    path.write_text(s)
    print("air_operations.cpp: DEC3N and UDEC3 vertex formats unpacked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
