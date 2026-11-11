#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT Direct3D 9: a vertex shader that writes the point size gets its [[point_size]] output only in the point-draw variant
(night round 2026-11-11, G1 S6; the second half of round 630's Alan Wake fix).

Alan Wake (778A58, build 634; A6232G, build 625: 67 pipelines a run): "d3d9: failed to create pipeline state ... Error
Domain=AGXMetalG18P Code=3 'Vertex shader writes point size but inputPrimitiveTopology is MTLPrimitiveTopologyClassTriangle'".
airconv's DXSO converter (dxso_compile.cpp) defines the AIR `[[point_size]]` output whenever the shader's bytecode declares
dcl_psize (SM3) or writes oPts (SM1/2), whatever the DRAW's topology is. The frontend already compiles two variants of such a
shader (d3d9_shader.cpp getVariantTask: the key carries one bit, "inject point size", true only for a D3DPT_POINTLIST draw), so a
triangle or line draw of a billboard/particle/HUD shader that also writes psize got a point-size output in a triangle-class
pipeline, which AGX refuses: the pipeline is a null state and every draw that uses it is skipped.

The output is now defined only when the variant is the point-draw one (vs_inject_point_size, which d3d9_point_size.hpp's
inject_point_size() makes true for every point-list draw of a vertex shader that writes psize, so those keep the old function
exactly). The slot that the shader's own oPts / dcl_psize stores write to is still allocated when the output is not defined, so
the stores keep a destination (they are dead: the epilogue only reads the slot when the output exists). Only pipelines that cannot
be built today change; the fixed-function vertex path already has the same topology gate (ffp_point_size, a9730f3f).

Shader cache: D3D9's disk cache digest carries DXMT_VERSION (git describe of the dxmt pin), which differs between the 634 pin and
this one, so the old metallibs are not reused; a later build on an unchanged pin would have to bump kD9ShaderCacheEpoch by hand
(d3d9_shader.cpp).

Idempotent; fails by name when an anchor moves. Run from the repository root (dxmt/src/airconv/dxso_compile.cpp), or give the
file's path as the one argument (tests/host/check-airconv-d9-psize-topology.py does).
"""
import pathlib
import sys

F = pathlib.Path("dxmt/src/airconv/dxso_compile.cpp")
MARKER = "madeira-bcd: d9-psize-topology"

# 1. the SM1/2 legacy output walk (the walk that ends in `} else if (sm3_vs_outputs) {`)
OLD1 = """      if (oPts_used)
        oPts_arg_idx = (int)sig.DefineOutput(air::OutputPointSize{});
    } else if (sm3_vs_outputs) {
"""
NEW1 = """      // """ + MARKER + """ (night round 2026-11-11, Alan Wake 778A58): AGX refuses [[point_size]] in a triangle or line pipeline
      // ("Vertex shader writes point size but inputPrimitiveTopology is MTLPrimitiveTopologyClassTriangle"); the output belongs to the
      // point-draw variant only. The shader's own oPts writes stay (the slot below), only the output goes.
      if (oPts_used && vs_inject_point_size)
        oPts_arg_idx = (int)sig.DefineOutput(air::OutputPointSize{});
    } else if (sm3_vs_outputs) {
"""
# 2. the SM3 walk (the one that ends the vertex branch before the pixel-shader pre-scan)
OLD2 = """      if (oPts_used)
        oPts_arg_idx = (int)sig.DefineOutput(air::OutputPointSize{});
    }
  } else {
    // Pre-scan PS body for ColorOut writes"""
NEW2 = """      // """ + MARKER + """: the same gate for dcl_psize (see the SM1/2 walk above)
      if (oPts_used && vs_inject_point_size)
        oPts_arg_idx = (int)sig.DefineOutput(air::OutputPointSize{});
    }
  } else {
    // Pre-scan PS body for ColorOut writes"""
# 3. the slot: a shader that writes oPts / dcl_psize keeps its alloca when this variant defines no output
OLD3 = """  Value *oPts_slot = nullptr;
  if (is_vertex && oPts_arg_idx >= 0) {
"""
NEW3 = """  Value *oPts_slot = nullptr;
  // """ + MARKER + """: also when the shader writes psize but this (non-point) variant defines no output; its stores need a destination
  if (is_vertex && (oPts_arg_idx >= 0 || oPts_used)) {
"""
PAIRS = ((OLD1, NEW1), (OLD2, NEW2), (OLD3, NEW3))


def patch(src):
    if MARKER in src:
        if any(new not in src for _, new in PAIRS):
            raise ValueError("partial or changed d9-psize-topology")
        return src
    for old, _ in PAIRS:
        if src.count(old) != 1:
            raise ValueError("an anchor was not found exactly once in dxso_compile.cpp (DXMT changed): " + old.strip().splitlines()[0])
    for old, new in PAIRS:
        src = src.replace(old, new, 1)
    return src


def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else F
    src = path.read_text()
    try:
        out = patch(src)
    except ValueError as error:
        sys.exit("patch-airconv-d9-psize-topology: " + str(error))
    if out == src:
        print("patch-airconv-d9-psize-topology: already in")
        return
    path.write_text(out)
    print("patch-airconv-d9-psize-topology: [[point_size]] is defined for point draws only")


if __name__ == "__main__":
    main()
