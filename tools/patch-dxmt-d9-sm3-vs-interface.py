#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT Direct3D 9: a vs_3_0 vertex shader's NORMAL/TANGENT/BINORMAL/BLENDWEIGHT/BLENDINDICES/DEPTH outputs reach the pixel shader (round 630, agent O1 S3).

Alan Wake (A6232G, build 625; W5XXV1, build 614): 67 pipelines a run failed with AGX's "Fragment input(s) `user(TANGENT0),
user(BINORMAL0),user(NORMAL0)` mismatching vertex shader output type(s) or not written by vertex shader", and every draw of
those pipelines was skipped: the lit surfaces were missing ("very dark"). The SM3 vertex-output walk of
dxso_compile.cpp knew only Color, Texcoord and Fog; an output the shader declared as dcl_normal / dcl_tangent / dcl_binormal /
dcl_blendweight / dcl_blendindices / dcl_depth was dropped, while the pixel side (the same file) names those inputs
user(NORMAL0) ... user(DEPTH0). The walk now emits them under the same names.

Only outputs the vertex shader itself declares are added. A pixel shader that does not read one keeps it as the unused stub it
already declares for every shader ("pre-transformed passthrough stubs"), and an unread vertex output links (the file's own
comment), so a pair that links today links as before; the pairs that read one of these names stop failing. A D3D9 vertex shader
has at most twelve output registers, so the count stays within Metal's limit. (The two "Vertex shader writes point size but
inputPrimitiveTopology is MTLPrimitiveTopologyClassTriangle" pipelines of the same log need the point size kept per topology, a
pipeline-variant change that is not part of this patch.)

Idempotent; fails by name when the anchor moves. Run from the repository root (dxmt/src/airconv/dxso_compile.cpp).
"""
import pathlib
import sys

F = pathlib.Path("dxmt/src/airconv/dxso_compile.cpp")
MARKER = "madeira-bcd: d9-sm3-vs-interface"

ANCHOR = """        case DxsoUsage::Fog:
          base = "FOG";
          break;
        default:
          break; // Depth / etc; TODO
        }
"""
NEW = """        case DxsoUsage::Fog:
          base = "FOG";
          break;
        // """ + MARKER + """ (round 630, Alan Wake A6232G): the names the pixel side reads (and stubs) for these usages
        case DxsoUsage::BlendWeight:
          base = "BLENDWEIGHT";
          break;
        case DxsoUsage::BlendIndices:
          base = "BLENDINDICES";
          break;
        case DxsoUsage::Normal:
          base = "NORMAL";
          break;
        case DxsoUsage::Tangent:
          base = "TANGENT";
          break;
        case DxsoUsage::Binormal:
          base = "BINORMAL";
          break;
        case DxsoUsage::Depth:
          base = "DEPTH";
          break;
        default:
          break;
        }
"""


def patch(src):
    if MARKER in src:
        if src.count(NEW) != 1:
            raise ValueError("partial or changed d9-sm3-vs-interface")
        return src
    if src.count(ANCHOR) != 1:
        raise ValueError("the SM3 vertex-output usage switch was not found exactly once in dxso_compile.cpp (DXMT changed)")
    return src.replace(ANCHOR, NEW, 1)


def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else F
    src = path.read_text()
    try:
        out = patch(src)
    except ValueError as error:
        sys.exit("patch-dxmt-d9-sm3-vs-interface: " + str(error))
    if out == src:
        print("patch-dxmt-d9-sm3-vs-interface: already in")
        return
    path.write_text(out)
    print("patch-dxmt-d9-sm3-vs-interface: vs_3_0 NORMAL/TANGENT/BINORMAL/BLENDWEIGHT/BLENDINDICES/DEPTH outputs are emitted")


if __name__ == "__main__":
    main()
