#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""D3D9 state blocks keep the sampler and texture-stage states they did not record
(dxmt aa7f5cc = willfaust/dxmt#52, b24e7d0); the DXMT frontend's source, no device.

Before: a recorded block tracked sampler states and texture-stage states with ONE boolean for the
whole array, so recording one element (ALPHAOP of stage 0) marked every stage and element, and
Apply restored the unrecorded siblings (COLORARG2, stage 1, other samplers) to their value at
BeginStateBlock. Windows (and wined3d, DXVK) record and restore exactly the elements the application
set. Now one bit per (stage, element) (uint16_t sampler_states[20], uint64_t texture_stage_states[8],
D3DTSS_CONSTANT = bit 32), Capture and Apply touch only recorded elements, and the predefined
PIXELSTATE / VERTEXSTATE subsets keep their wined3d membership. Both frontends (the 32-bit
d3d9-emulated.dll and the 64-bit d3d9-dxmt.dll) share the code.

1. d3d9_state_block_changes.hpp is compiled on the host and driven: a fresh block records nothing;
   markAll() sets exactly the valid (stage, element) bits; markPixelStateSubset() and
   markVertexStateSubset() set exactly the elements of the wined3d tables listed in the header
   (parsed from the header itself), the same in every stage, and nothing outside them.
2. The Capture and Apply loops are cut out of d3d9_state_block.cpp and compiled with a fake device
   (host C++): one recorded ALPHAOP of stage 0 restores that one value and leaves COLORARG2, stage 1
   and every sampler at the live value; a recorded sampler element restores that element only; a
   recorded MIPMAPLODBIAS re-derives the fetch4 latch of its slot only; D3DTSS_CONSTANT (bit 32)
   and the last sampler element (DMAPOFFSET) round trip; markAll() restores every element of every
   stage and slot as the old whole-array copy did; the two predefined subsets restore only their
   elements; Capture refreshes only recorded elements.
3. Source checks: the two setters mark the one bit while recording, no whole-array boolean is left
   in the frontend.
SKIP (exit 0) without the dxmt sources or the Direct3D headers (dxmt/include/native/directx).
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
d9 = root / "dxmt/src/d3d9"
inc_dx = root / "dxmt/include/native/directx"
inc_win = root / "dxmt/include/native/windows"
if not (d9 / "d3d9_state_block.cpp").is_file() or not (inc_dx / "d3d9.h").is_file():
    print("SKIP: no dxmt checkout with the Direct3D headers (run in the full tree)")
    sys.exit(0)
cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cxx:
    print("SKIP: no C++ compiler")
    sys.exit(0)

hdr = (d9 / "d3d9_state_block_changes.hpp").read_text()
blk = (d9 / "d3d9_state_block.cpp").read_text()
dev = (d9 / "d3d9_device.cpp").read_text()
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


# ---- 3. source checks
check("the old whole-array booleans are gone",
      "bool sampler_states" not in hdr and "bool texture_stage_states" not in hdr
      and "samp_element_mask" not in hdr + blk and "tss_element_mask" not in hdr + blk)
check("the changes struct has one bit per (stage, element)",
      "uint16_t sampler_states[kSbcMaxTextureUnits]" in hdr and "uint64_t texture_stage_states[8]" in hdr)
check("SetSamplerState marks the one (slot, element) bit while recording",
      "m_recordingBlock->m_changes.sampler_states[slot] |= 1u << Type;" in dev)
check("SetTextureStageState marks the one (stage, element) bit while recording",
      "m_recordingBlock->m_changes.texture_stage_states[Stage] |= 1ull << Type;" in dev)
check("nothing else in the frontend sets a whole category flag",
      not re.search(r"m_changes\.(sampler_states|texture_stage_states)\s*=\s*true", dev + blk))

# ---- 1 + 2: the host harness
def cut(text, a, b):
    i = text.index(a)
    return text[i:text.index(b, i)]


try:
    cap_chunk = cut(blk, "  for (uint32_t s = 0; s < D3D9_MAX_TEXTURE_UNITS; ++s)\n    for (uint32_t e = 0; e <= D3DSAMP_DMAPOFFSET; ++e)\n      if (m_changes.sampler_states[s] & (1u << e))",
                    "  if (m_changes.transforms)\n    std::memcpy(m_snapTransforms")
    app_chunk = cut(blk, "  for (uint32_t s = 0; s < D3D9_MAX_TEXTURE_UNITS; ++s) {\n    if (!m_changes.sampler_states[s])",
                    "  if (m_changes.transforms) {\n    // Only the indices the block recorded")
except ValueError:
    check("Capture and Apply have the per-element loops (dxmt aa7f5cc is in the dxmt pin)", False)
    print("FAILED: D3D9 state block elements")
    sys.exit(1)


def names(table):
    m = re.search(table + r"\[\] = \{(.*?)\};", hdr, re.S)
    return re.findall(r"D3D(?:TSS|SAMP)_\w+", m.group(1))


pix_tss, pix_samp = names("pixel_states_texture"), names("pixel_states_sampler")
vtx_tss, vtx_samp = names("vertex_states_texture"), [m for m in ["D3DSAMP_DMAPOFFSET"]]
check("the header lists the wined3d tables the harness compares against",
      len(pix_tss) > 10 and len(pix_samp) >= 8 and vtx_tss == ["D3DTSS_TEXCOORDINDEX", "D3DTSS_TEXTURETRANSFORMFLAGS"])


def arr(xs):
    return "{" + ", ".join(xs) + "}"


harness = r'''
#include "d3d9_state_block_changes.hpp"
#include <cstdio>
#include <cstring>
#include <vector>
namespace dxmt {
enum { D9ES_DIRTY_SAMPLER_STATES = 2, D9ES_DIRTY_TEXTURE_STAGE_STATES = 4, D9ES_DIRTY_RENDER_STATES = 1 };
}
#ifndef MAKEFOURCC
#define MAKEFOURCC(a, b, c, d) ((DWORD)(unsigned char)(a) | ((DWORD)(unsigned char)(b) << 8) | ((DWORD)(unsigned char)(c) << 16) | ((DWORD)(unsigned char)(d) << 24))
#endif
static constexpr unsigned D3D9_MAX_TEXTURE_UNITS = 20;
struct Dev {
  DWORD m_samplerStates[D3D9_MAX_TEXTURE_UNITS][D3DSAMP_DMAPOFFSET + 1] = {};
  DWORD m_textureStageStates[8][D3DTSS_CONSTANT + 1] = {};
  uint16_t m_fetch4Latch = 0;
};
struct Blk {
  Dev *m_device;
  dxmt::D3D9StateBlockChanges m_changes;
  DWORD m_snapSamplerStates[D3D9_MAX_TEXTURE_UNITS][D3DSAMP_DMAPOFFSET + 1] = {};
  DWORD m_snapTextureStageStates[8][D3DTSS_CONSTANT + 1] = {};
  uint32_t pod_dirty = 0;
  void Capture() {
''' + cap_chunk + r'''  }
  void Apply() {
    uint32_t pod_dirty_local = 0;
    auto &pod_dirty = pod_dirty_local;
''' + app_chunk + r'''    this->pod_dirty = pod_dirty_local;
  }
};
static int fails = 0;
static void expect(bool c, const char *m) { std::printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
static void fill_live(Dev &d, DWORD base) {
  for (unsigned s = 0; s < D3D9_MAX_TEXTURE_UNITS; ++s)
    for (unsigned e = 0; e <= D3DSAMP_DMAPOFFSET; ++e) d.m_samplerStates[s][e] = base + s * 100 + e;
  for (unsigned s = 0; s < 8; ++s)
    for (unsigned e = 0; e <= D3DTSS_CONSTANT; ++e) d.m_textureStageStates[s][e] = base + 5000 + s * 100 + e;
}
static void fill_snap(Blk &b, DWORD base) {
  for (unsigned s = 0; s < D3D9_MAX_TEXTURE_UNITS; ++s)
    for (unsigned e = 0; e <= D3DSAMP_DMAPOFFSET; ++e) b.m_snapSamplerStates[s][e] = base + s * 100 + e;
  for (unsigned s = 0; s < 8; ++s)
    for (unsigned e = 0; e <= D3DTSS_CONSTANT; ++e) b.m_snapTextureStageStates[s][e] = base + 5000 + s * 100 + e;
}
/* Which (stage, element) pairs differ from the live values after Apply with snapshot base 100000 and live base 1000. */
static void diff_after_apply(Blk &b, Dev &d, std::vector<std::pair<int, int>> &samp, std::vector<std::pair<int, int>> &tss) {
  samp.clear(); tss.clear();
  for (unsigned s = 0; s < D3D9_MAX_TEXTURE_UNITS; ++s)
    for (unsigned e = 0; e <= D3DSAMP_DMAPOFFSET; ++e) {
      if (d.m_samplerStates[s][e] == 100000 + s * 100 + e) samp.push_back({(int)s, (int)e});
      else if (d.m_samplerStates[s][e] != 1000 + s * 100 + e) expect(false, "a sampler value is neither live nor snapshot");
    }
  for (unsigned s = 0; s < 8; ++s)
    for (unsigned e = 0; e <= D3DTSS_CONSTANT; ++e) {
      if (d.m_textureStageStates[s][e] == 100000 + 5000 + s * 100 + e) tss.push_back({(int)s, (int)e});
      else if (d.m_textureStageStates[s][e] != 1000 + 5000 + s * 100 + e) expect(false, "a stage value is neither live nor snapshot");
    }
}
static const int PIX_TSS[] = ''' + arr(pix_tss) + r''';
static const int PIX_SAMP[] = ''' + arr(pix_samp) + r''';
static const int VTX_TSS[] = ''' + arr(vtx_tss) + r''';
int main() {
  {
    dxmt::D3D9StateBlockChanges c;
    bool zero = true;
    for (auto m : c.sampler_states) zero &= m == 0;
    for (auto m : c.texture_stage_states) zero &= m == 0;
    expect(zero, "a fresh block records no sampler or texture-stage element");
    expect(sizeof(c.texture_stage_states[0]) == 8 && sizeof(c.sampler_states[0]) == 2, "64 bits per stage (bit 32 = CONSTANT), 16 per slot (DMAPOFFSET = bit 13)");
  }
  {
    dxmt::D3D9StateBlockChanges c;
    c.markAll();
    bool ok = true;
    for (unsigned s = 0; s < D3D9_MAX_TEXTURE_UNITS; ++s)
      for (unsigned e = 0; e < 16; ++e) ok &= ((c.sampler_states[s] >> e) & 1) == (e <= D3DSAMP_DMAPOFFSET);
    for (unsigned s = 0; s < 8; ++s)
      for (unsigned e = 0; e < 64; ++e) ok &= ((c.texture_stage_states[s] >> e) & 1) == (e >= 1 && e <= D3DTSS_CONSTANT);
    expect(ok, "markAll: every valid element of every slot and stage, nothing else (no bit 0, none above CONSTANT)");
  }
  {
    dxmt::D3D9StateBlockChanges c;
    c.markPixelStateSubset();
    bool ok = true;
    for (unsigned s = 0; s < 8; ++s) {
      uint64_t want = 0;
      for (int e : PIX_TSS) want |= 1ull << e;
      ok &= c.texture_stage_states[s] == want;
    }
    expect(ok, "PIXELSTATE: texture-stage elements are exactly wined3d's pixel list, the same in every stage");
    ok = true;
    for (unsigned s = 0; s < D3D9_MAX_TEXTURE_UNITS; ++s) {
      uint16_t want = 0;
      for (int e : PIX_SAMP) want |= 1u << e;
      ok &= c.sampler_states[s] == want;
    }
    expect(ok, "PIXELSTATE: sampler elements are exactly wined3d's pixel list, the same in every slot");
  }
  {
    dxmt::D3D9StateBlockChanges c;
    c.markVertexStateSubset();
    bool ok = true;
    for (unsigned s = 0; s < 8; ++s) {
      uint64_t want = 0;
      for (int e : VTX_TSS) want |= 1ull << e;
      ok &= c.texture_stage_states[s] == want;
    }
    for (unsigned s = 0; s < D3D9_MAX_TEXTURE_UNITS; ++s) ok &= c.sampler_states[s] == (1u << D3DSAMP_DMAPOFFSET);
    expect(ok, "VERTEXSTATE: TEXCOORDINDEX and TEXTURETRANSFORMFLAGS per stage, DMAPOFFSET per slot, nothing else");
  }
  std::vector<std::pair<int, int>> samp, tss;
  {
    /* the case of the upstream reproducer: record ALPHAOP of stage 0 only */
    Dev d; fill_live(d, 1000); Blk b; b.m_device = &d; fill_snap(b, 100000);
    b.m_changes.texture_stage_states[0] |= 1ull << D3DTSS_ALPHAOP;
    b.Apply();
    diff_after_apply(b, d, samp, tss);
    expect(samp.empty() && tss.size() == 1 && tss[0] == std::make_pair(0, (int)D3DTSS_ALPHAOP),
           "ALPHAOP of stage 0 recorded: Apply restores that one value; COLORARG2, stage 1 and every sampler keep the live value");
    expect(b.pod_dirty == dxmt::D9ES_DIRTY_TEXTURE_STAGE_STATES, "only the texture-stage axis is dirtied");
  }
  {
    Dev d; fill_live(d, 1000); Blk b; b.m_device = &d; fill_snap(b, 100000);
    b.m_changes.texture_stage_states[7] |= 1ull << D3DTSS_CONSTANT;
    b.m_changes.sampler_states[19] |= 1u << D3DSAMP_DMAPOFFSET;
    b.m_changes.sampler_states[3] |= 1u << D3DSAMP_ADDRESSU;
    b.Apply();
    diff_after_apply(b, d, samp, tss);
    expect(tss.size() == 1 && tss[0] == std::make_pair(7, (int)D3DTSS_CONSTANT), "CONSTANT (bit 32) of the last stage round trips");
    expect(samp.size() == 2 && samp[0] == std::make_pair(3, (int)D3DSAMP_ADDRESSU) && samp[1] == std::make_pair(19, (int)D3DSAMP_DMAPOFFSET),
           "ADDRESSU of slot 3 and DMAPOFFSET of slot 19 (the last element of the last slot) restore, nothing else");
    expect(b.pod_dirty == (dxmt::D9ES_DIRTY_SAMPLER_STATES | dxmt::D9ES_DIRTY_TEXTURE_STAGE_STATES), "both axes dirtied");
  }
  {
    /* fetch4 latch: only a recorded MIPMAPLODBIAS of a slot below 16 re-derives that slot's latch */
    Dev d; fill_live(d, 1000); Blk b; b.m_device = &d; fill_snap(b, 100000);
    d.m_fetch4Latch = 0x0004;   /* slot 2 armed live, bias live is plain */
    b.m_snapSamplerStates[5][D3DSAMP_MIPMAPLODBIAS] = MAKEFOURCC('G', 'E', 'T', '4');
    b.m_snapSamplerStates[2][D3DSAMP_MIPMAPLODBIAS] = MAKEFOURCC('G', 'E', 'T', '1');
    b.m_changes.sampler_states[5] |= 1u << D3DSAMP_MIPMAPLODBIAS;
    b.m_changes.sampler_states[9] |= 1u << D3DSAMP_ADDRESSV;   /* no bias recorded: latch of 9 untouched */
    b.Apply();
    expect((d.m_fetch4Latch & (1u << 5)) != 0, "a recorded GET4 bias arms the latch of its slot");
    expect((d.m_fetch4Latch & (1u << 2)) != 0, "a slot whose bias was not recorded keeps its latch (the old loop visited every slot)");
    b.m_changes.sampler_states[2] |= 1u << D3DSAMP_MIPMAPLODBIAS;
    b.Apply();
    expect((d.m_fetch4Latch & (1u << 2)) == 0, "a recorded GET1 bias disarms the latch of its slot");
  }
  {
    /* D3DSBT_ALL: the old whole-array copy */
    Dev d; fill_live(d, 1000); Blk b; b.m_device = &d; fill_snap(b, 100000);
    b.m_changes.markAll();
    b.Apply();
    diff_after_apply(b, d, samp, tss);
    expect(samp.size() == 20u * 14u && tss.size() == 8u * 32u, "markAll: every element of every slot and stage is restored (D3DSBT_ALL as before)");
    bool bit0 = true;
    for (unsigned s = 0; s < 8; ++s) bit0 &= d.m_textureStageStates[s][0] == 1000 + 5000 + s * 100;
    expect(bit0, "stage element 0 (not a state) is never written");
  }
  {
    Dev d; fill_live(d, 1000); Blk b; b.m_device = &d; fill_snap(b, 100000);
    b.m_changes.markVertexStateSubset();
    b.Apply();
    diff_after_apply(b, d, samp, tss);
    expect(tss.size() == 8u * 2u && samp.size() == 20u, "VERTEXSTATE restores 2 elements per stage and DMAPOFFSET per slot only");
  }
  {
    Dev d; fill_live(d, 1000); Blk b; b.m_device = &d; fill_snap(b, 100000);
    b.m_changes.markPixelStateSubset();
    b.Apply();
    diff_after_apply(b, d, samp, tss);
    expect(tss.size() == 8u * (sizeof(PIX_TSS) / sizeof(int)) && samp.size() == 20u * (sizeof(PIX_SAMP) / sizeof(int)),
           "PIXELSTATE restores wined3d's pixel elements only");
  }
  {
    /* Capture: only recorded elements are refreshed from the live device */
    Dev d; fill_live(d, 7000); Blk b; b.m_device = &d; fill_snap(b, 100000);
    b.m_changes.sampler_states[1] |= 1u << D3DSAMP_MAGFILTER;
    b.m_changes.texture_stage_states[2] |= 1ull << D3DTSS_COLORARG1;
    b.Capture();
    int changed = 0;
    for (unsigned s = 0; s < D3D9_MAX_TEXTURE_UNITS; ++s)
      for (unsigned e = 0; e <= D3DSAMP_DMAPOFFSET; ++e)
        if (b.m_snapSamplerStates[s][e] != 100000 + s * 100 + e) {
          changed++;
          expect(s == 1 && e == D3DSAMP_MAGFILTER && b.m_snapSamplerStates[s][e] == 7000 + s * 100 + e, "Capture refreshed the recorded sampler element from the live value");
        }
    for (unsigned s = 0; s < 8; ++s)
      for (unsigned e = 0; e <= D3DTSS_CONSTANT; ++e)
        if (b.m_snapTextureStageStates[s][e] != 100000 + 5000 + s * 100 + e) {
          changed++;
          expect(s == 2 && e == D3DTSS_COLORARG1 && b.m_snapTextureStageStates[s][e] == 7000 + 5000 + s * 100 + e, "Capture refreshed the recorded stage element from the live value");
        }
    expect(changed == 2, "Capture touched exactly the two recorded elements");
  }
  return fails ? 1 : 0;
}
'''
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    (t / "h.cpp").write_text(harness)
    flags = ["-std=c++20", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-but-set-variable",
             "-Wno-unknown-pragmas", "-Wno-extern-c-compat", "-Wno-microsoft-exception-spec", "-Wno-ignored-attributes"]
    includes = [f"-I{d9}", f"-I{inc_dx}", f"-I{inc_win}", f"-I{root / 'dxmt/include'}"]
    san = ["-fsanitize=address,undefined"]
    b = subprocess.run([cxx, *flags, *san, *includes, "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
    if b.returncode:
        b = subprocess.run([cxx, *flags, *includes, "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
    check("the state block's change masks and its Capture / Apply loops compile on the host", b.returncode == 0)
    if b.returncode:
        print(b.stderr[-3000:])
    else:
        r = subprocess.run([str(t / "h")], capture_output=True, text=True, timeout=120)
        print(r.stdout.rstrip())
        if r.returncode:
            print(r.stderr[-1500:])
        check("the host harness passes", r.returncode == 0)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("this check runs in the workflow", "tests/host/check-dxmt-d9-stateblock-elements.py" in wf)
print("FAILED: D3D9 state block elements" if failures else "PASS: D3D9 state block elements")
sys.exit(1 if failures else 0)
