#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A tessellation or geometry mesh pipeline that did not compile: drop the mesh draws that follow it instead of encoding them without
a pipeline (round 626, agent T S2; Batman: Arkham City GOTY, Send Logs 9AGR39 = 1WAGW3 and AE9TXK, build 623, iPad17,2).

The game ran Direct3D 11 at 60 FPS for about 65 s, then the first tessellated draw's mesh pipeline failed to compile
(AGXMetalG17G: "Total mesh size (33792) exceeds the maximum mesh size allowed (32768)", DXMT's "[mesh-fail] ml754 kind=TESSELLATION").
FinalizeTessellationRenderPipeline's encoder lambda then returned before binding a pipeline, but the draws that followed were still
encoded as mesh draws; the driver faulted in drawMeshThreadgroups (pc in the dyld cache, fault address 0x1012), BCD ended the encode
thread and Metal asserted "Command encoder released without endEncoding". DXMT's own ml869 comment names the same driver fault for a
mesh draw without its mesh pipeline.

What changes:
  - dxmt/src/dxmt/dxmt_context.hpp: ArgumentEncodingContext gets tess_pso_failed and gs_pso_failed (false by default).
  - dxmt/src/d3d11/d3d11_context_impl.cpp: the tessellation and geometry pipelines' encoder lambdas set their flag to "no pipeline
    state" and say so ([mesh-skip] ml870, once, then every 1024th time); the four tessellation draws and the four geometry draws
    return before they encode anything while their kind's flag is set. The next mesh pipeline of that kind that compiles clears it.
A draw whose pipeline exists is encoded exactly as before (the flag is false); only draws that today go out without their mesh
pipeline and take the game down are dropped. The geometry half has no log of its own yet: it is the same missing-pipeline path.

PE side: the i386 d3d11.dll the CI compiles (Batman: Arkham City is 32-bit) and d3d11-src.dll (a 64-bit game gets it with
env.MADEIRA_D3D11_SRC = 1; tools/build-d3d11-dll.sh). Idempotent (marker "tess-pso-failed-v1"); exits 1 naming the anchor when one
moved, the files unchanged. Run from the repository root, after the other DXMT patches.
"""
import pathlib
import sys

MARKER = "tess-pso-failed-v1"
HPP = pathlib.Path("dxmt/src/dxmt/dxmt_context.hpp")
CPP = pathlib.Path("dxmt/src/d3d11/d3d11_context_impl.cpp")

HPP_OLD = """  uint32_t tess_num_output_control_point_element;
  uint32_t tess_threads_per_patch;
"""
HPP_NEW = """  uint32_t tess_num_output_control_point_element;
  uint32_t tess_threads_per_patch;
  /* madeira-bcd tess-pso-failed-v1 (round 626; tools/patch-dxmt-tess-pso-failed.py): the last tessellation / geometry mesh pipeline
   * this context set did not compile; the mesh draws of that kind that follow are dropped (d3d11_context_impl.cpp). */
  bool tess_pso_failed = false;
  bool gs_pso_failed = false;
"""

HELPER_OLD = """    ERR("[mesh-skip] ml869 dropped ", n, " geometry/tessellation draw(s) (d3d11.noMeshShaders=1)");
  return true;
}
"""
HELPER_NEW = HELPER_OLD + """
/* madeira-bcd tess-pso-failed-v1 (round 626, Batman: Arkham City 9AGR39/AE9TXK; tools/patch-dxmt-tess-pso-failed.py): a tessellation
 * or geometry mesh pipeline that did not compile (Metal: "Total mesh size (33792) exceeds the maximum mesh size allowed (32768)")
 * left no pipeline bound, and the mesh draws that followed made the driver fault in drawMeshThreadgroups. Those draws are dropped
 * until a pipeline of that kind compiles again; said once, then every 1024th time. */
static inline void madeira_mesh_pso_missing(const char *kind) {
  static std::atomic<uint32_t> missing{0};
  uint32_t n = missing.fetch_add(1, std::memory_order_relaxed) + 1;
  if (n == 1 || (n & 0x3FF) == 0)
    ERR("[mesh-skip] ml870 ", kind, " mesh pipeline missing (#", n, "): its draws are dropped until one compiles");
}
"""

TESS_PSO_OLD = """      enc.tess_threads_per_patch = GraphicsPipeline.ThreadsPerPatch;
      if (!GraphicsPipeline.PipelineState)
        return;
"""
TESS_PSO_NEW = """      enc.tess_threads_per_patch = GraphicsPipeline.ThreadsPerPatch;
      enc.tess_pso_failed = !GraphicsPipeline.PipelineState;   /* madeira-bcd tess-pso-failed-v1 */
      if (!GraphicsPipeline.PipelineState) {
        madeira_mesh_pso_missing("tessellation");
        return;
      }
"""

GS_PSO_OLD = """      render_encoder->use_geometry = 1;
      MTL_COMPILED_GRAPHICS_PIPELINE GraphicsPipeline{};
      pso->GetPipeline(&GraphicsPipeline); // may block
      if (!GraphicsPipeline.PipelineState)
        return;
"""
GS_PSO_NEW = """      render_encoder->use_geometry = 1;
      MTL_COMPILED_GRAPHICS_PIPELINE GraphicsPipeline{};
      pso->GetPipeline(&GraphicsPipeline); // may block
      enc.gs_pso_failed = !GraphicsPipeline.PipelineState;   /* madeira-bcd tess-pso-failed-v1 */
      if (!GraphicsPipeline.PipelineState) {
        madeira_mesh_pso_missing("geometry");
        return;
      }
"""

GUARD_TS = "      if (enc.tess_pso_failed) return;   /* madeira-bcd tess-pso-failed-v1: no mesh pipeline for this draw */\n"
GUARD_TS_IND = "        if (enc.tess_pso_failed) return;   /* madeira-bcd tess-pso-failed-v1: no mesh pipeline for this draw */\n"
GUARD_GS = "      if (enc.gs_pso_failed) return;   /* madeira-bcd tess-pso-failed-v1: no mesh pipeline for this draw */\n"
GUARD_GS_IND = "        if (enc.gs_pso_failed) return;   /* madeira-bcd tess-pso-failed-v1: no mesh pipeline for this draw */\n"

DRAWS = [
    ("TessellationDraw", """  TessellationDraw(
      UINT NumControlPoint, UINT VertexCountPerInstance, UINT InstanceCount, UINT StartVertexLocation,
      UINT StartInstanceLocation
  ) {
    auto draw_arguments_offset = PreAllocateArgumentBuffer(sizeof(DXMT_DRAW_ARGUMENTS), 32);
    auto max_object_threadgroups = max_object_threadgroups_;
    EmitOP([=](ArgumentEncodingContext &enc) {
""", GUARD_TS),
    ("TessellationDrawIndexed", """  TessellationDrawIndexed(
      UINT NumControlPoint, UINT IndexCountPerInstance, UINT StartIndexLocation, INT BaseVertexLocation,
      UINT InstanceCount, UINT BaseInstance
  ) {
    auto IndexBufferOffset = state_.InputAssembler.IndexBufferOffset;
    auto draw_arguments_offset = PreAllocateArgumentBuffer(sizeof(DXMT_DRAW_INDEXED_ARGUMENTS), 32);
    auto max_object_threadgroups = max_object_threadgroups_;
    EmitOP([=](ArgumentEncodingContext &enc) {
""", GUARD_TS),
    ("TessellationDrawIndirect", """  TessellationDrawIndirect(
    UINT NumControlPoint, ID3D11Buffer *pBufferForArgs, UINT AlignedByteOffsetForArgs
  ) {
    auto max_object_threadgroups = max_object_threadgroups_;
    if (auto bindable = reinterpret_cast<D3D11ResourceCommon *>(pBufferForArgs)) {
      EmitOP([=, ArgBuffer = bindable->buffer()](ArgumentEncodingContext &enc) {
""", GUARD_TS_IND),
    ("TessellationDrawIndexedIndirect", """  TessellationDrawIndexedIndirect(
    UINT NumControlPoint, ID3D11Buffer *pBufferForArgs, UINT AlignedByteOffsetForArgs
  ) {
    auto IndexBufferOffset = state_.InputAssembler.IndexBufferOffset;
    auto max_object_threadgroups = max_object_threadgroups_;

    if (auto bindable = reinterpret_cast<D3D11ResourceCommon *>(pBufferForArgs)) {
      EmitOP([=, ArgBuffer = bindable->buffer()](ArgumentEncodingContext &enc) {
""", GUARD_TS_IND),
    ("GeometryDraw", """  GeometryDraw(
      UINT VertexCountPerInstance, UINT InstanceCount, UINT StartVertexLocation,
      UINT StartInstanceLocation
  ) {
    auto draw_arguments_offset = PreAllocateArgumentBuffer(sizeof(DXMT_DRAW_ARGUMENTS), 32);
    auto max_object_threadgroups = max_object_threadgroups_;
    EmitOP([=, topo = state_.InputAssembler.Topology](ArgumentEncodingContext &enc) {
""", GUARD_GS),
    ("GeometryDrawIndexed", """  GeometryDrawIndexed(
      UINT IndexCountPerInstance, UINT StartIndexLocation, INT BaseVertexLocation,
      UINT InstanceCount, UINT BaseInstance
  ) {
    auto IndexBufferOffset = state_.InputAssembler.IndexBufferOffset;
    auto draw_arguments_offset = PreAllocateArgumentBuffer(sizeof(DXMT_DRAW_INDEXED_ARGUMENTS), 32);
    auto max_object_threadgroups = max_object_threadgroups_;
    EmitOP([=, topo = state_.InputAssembler.Topology](ArgumentEncodingContext &enc) {
""", GUARD_GS),
    ("GeometryDrawIndirect", """  GeometryDrawIndirect(
    ID3D11Buffer *pBufferForArgs, UINT AlignedByteOffsetForArgs
  ) {
    auto max_object_threadgroups = max_object_threadgroups_;
    if (auto bindable = reinterpret_cast<D3D11ResourceCommon *>(pBufferForArgs)) {
      EmitOP([=, topo = state_.InputAssembler.Topology, ArgBuffer = bindable->buffer()](ArgumentEncodingContext &enc) {
""", GUARD_GS_IND),
    ("GeometryDrawIndexedIndirect", """  GeometryDrawIndexedIndirect(
    ID3D11Buffer *pBufferForArgs, UINT AlignedByteOffsetForArgs
  ) {
    auto IndexBufferOffset = state_.InputAssembler.IndexBufferOffset;
    auto max_object_threadgroups = max_object_threadgroups_;

    if (auto bindable = reinterpret_cast<D3D11ResourceCommon *>(pBufferForArgs)) {
      EmitOP([=, topo = state_.InputAssembler.Topology, ArgBuffer = bindable->buffer()](ArgumentEncodingContext &enc) {
""", GUARD_GS_IND),
]


def main():
    hpp, cpp = HPP.read_text(), CPP.read_text()
    marked = [MARKER in hpp, MARKER in cpp]
    if all(marked):
        print("tessellation/geometry mesh pipeline failures: already patched")
        return 0
    if any(marked):
        print("::error::patch-dxmt-tess-pso-failed: only one file carries the marker -- dxmt moved, review this patch")
        return 1
    checks = [(hpp, HPP_OLD, "dxmt_context.hpp: ArgumentEncodingContext's tessellation fields"),
              (cpp, HELPER_OLD, "d3d11_context_impl.cpp: dxmt_no_mesh_draws (ml869)"),
              (cpp, TESS_PSO_OLD, "d3d11_context_impl.cpp: FinalizeTessellationRenderPipeline's encoder lambda"),
              (cpp, GS_PSO_OLD, "d3d11_context_impl.cpp: FinalizeGeometryRenderPipeline's encoder lambda")]
    checks += [(cpp, old, "d3d11_context_impl.cpp: " + name) for name, old, _ in DRAWS]
    for text, old, name in checks:
        n = text.count(old)
        if n != 1:
            print(f"::error::patch-dxmt-tess-pso-failed: {name}: anchor found {n} times (want 1) -- dxmt moved, review this patch")
            return 1
    hpp = hpp.replace(HPP_OLD, HPP_NEW, 1)
    cpp = cpp.replace(HELPER_OLD, HELPER_NEW, 1).replace(TESS_PSO_OLD, TESS_PSO_NEW, 1).replace(GS_PSO_OLD, GS_PSO_NEW, 1)
    for _, old, guard in DRAWS:
        cpp = cpp.replace(old, old + guard, 1)
    HPP.write_text(hpp)
    CPP.write_text(cpp)
    print("tessellation/geometry mesh pipeline failures patched (their draws are dropped, [mesh-skip] ml870)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
