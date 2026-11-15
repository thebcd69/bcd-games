#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The switches that came with the 2026-11-14 upstream merge of DXMT are off unless a game's file sets them.

BCD's dxmt fork took these commits of Madeira's fork (willfaust/dxmt, branches ios-port and codex/dxmt-consolidation). Each adds a
path behind a switch; this check reads dxmt/src (or $DXMT_SRC_DIR) and proves that with the switch unset the old lines run:

  96a6711f  d3d11.keepShaderIR      BCD's default is 1 (keep the shader object, no copy of the bytecode, no log); upstream's is 0
  a4d8ab22  DXMT_CMDLIST_KEEP_BLOCK a recycled command list frees every block unless the switch is set
  dfd523ef  DXMT_DYN_KEEP           0 keeps the 64 retired names of before (dynKeepFor compiled and run for 12 settings)
  0b28f9ff  DXMT_D9_LATE_READBACK   GetRenderTargetData waits for the GPU unless the switch is set (and, BCD: an entry another
            device left behind starts again instead of looking in flight for ever)
  7bb5e807 + 1aab69fd  DXMT_TYPELESS_COMPRESSION  every typeless texture keeps pixelFormatView unless the switch is 1 (the
            decision compiled and run for every setting and format class), and no view is examined without it (BCD)
  43d07629  MADEIRA_D3D9_X64=0      only the 64-bit PE build of the Direct3D 9 frontend reads it; BCD's values (dxmt, wined3d) are not "0"
(Not taken: 67079a57, ea19ddf4 and e0abd4d1 refer to functions of Madeira's app that BCD's app does not have; see
tests/host/check-winemetal-weak-refs.py.)
It also reads the list policy (app/Madeira/GameRecommendationsList.swift): a downloaded list may set each of these switches and
env.MADEIRA_PRECISE_MINMAX, so a game can get one from the online list without a build; the values are checked.
SKIP (exit 0) without the dxmt sources."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
S = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
if not (S / "d3d11/d3d11_pipeline_cache.cpp").exists():
    print(f"SKIP: no dxmt sources at {S}")
    sys.exit(0)
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


def cut(text, start, end, what, keep_end=0):
    """The text from `start` up to `end` (the first one after it); "" and a named FAIL when either is missing."""
    a = text.find(start)
    b = text.find(end, a + len(start)) if a >= 0 else -1
    if a < 0 or b < 0:
        check(f"{what}: the code is where this check expects it", False)
        return ""
    return text[a:b + keep_end]


def in_order(text, *needles):
    """True when every needle is in `text`, each after the one before."""
    pos = -1
    for n in needles:
        pos = text.find(n, pos + 1)
        if pos < 0:
            return False
    return True


def run_cxx(code, runs):
    """Compile `code` and run it once per (args, env additions); True when every run exits 0."""
    cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if not cxx:
        print("note: no C++ compiler; the compiled part of this check is not run")
        return True, ""
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / "h.cpp").write_text(code)
        c = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", str(t / "h.cpp"), "-o", str(t / "h")],
                           capture_output=True, text=True)
        if c.returncode:
            return False, c.stderr
        for args, extra in runs:
            e = {k: v for k, v in os.environ.items() if not k.startswith("DXMT_")}
            e.update(extra)
            r = subprocess.run([str(t / "h")] + args, env=e, capture_output=True, text=True)
            if r.returncode:
                return False, f"args {args} env {extra}: exit {r.returncode} {r.stderr}"
    return True, ""


wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-dxmt-upstream-optins.py" in wf)

# --- 96a6711f + BCD: the shader object is kept unless d3d11.keepShaderIR=0
pc = (S / "d3d11/d3d11_pipeline_cache.cpp").read_text()
check("d3d11.keepShaderIR defaults to 1 in BCD (the object is kept)",
      pc.count('Config::getInstance().getOption<int>("d3d11.keepShaderIR", 1) != 0;') == 1 and '"d3d11.keepShaderIR", 0)' not in pc)
ctor = cut(pc, "CachedSM50Shader(PipelineCache *cache, sm50_shader_t shader_transfered,", "~CachedSM50Shader()", "the shader object's constructor")
check("kept: the constructor copies no bytecode and destroys nothing (both only under !keep_ir)",
      ctor.count("bytecode_.assign(") == 1 and ctor.count("SM50Destroy(shader);") == 1
      and in_order(ctor, "if (!keep_ir) {", "bytecode_.assign(", "SM50Destroy(shader);"))
handle = cut(pc, "virtual sm50_shader_t handle() {", "virtual MTL_SHADER_REFLECTION &reflection()", "the shader object's handle()")
check("kept: handle() returns the object without parsing again (the parse needs an empty object and kept bytecode)",
      handle.count("SM50Initialize(") == 1
      and in_order(handle, "if (!shader && !bytecode_.empty()) {", "SM50Initialize(", "return shader;"))
note = cut(pc, "void NoteShaderCached(size_t bytecode_length) {", "void NoteShaderReparsed", "NoteShaderCached")
check("kept: nothing is logged ([shader-ir] only when objects are dropped)",
      in_order(note, "if (keep_shader_ir_)", "return;", "++ir_shaders_", 'WARN("[shader-ir] ml1222 "'))
check("the destructor frees the argument buffer whether or not the object is there",
      re.search(r"~CachedSM50Shader\(\) \{\s*if \(shader\) \{\s*SM50Destroy\(shader\);\s*shader = nullptr;\s*\}\s*if \(arguments_info_buffer\)\s*free\(arguments_info_buffer\);", pc) is not None)

# --- a4d8ab22: command lists
ci = (S / "d3d11/d3d11_context_impl.cpp").read_text()
check("DXMT_CMDLIST_KEEP_BLOCK: read once, on only for a non-empty value other than 0",
      'const std::string v = env::getEnvVar("DXMT_CMDLIST_KEEP_BLOCK");' in ci and 'const bool r = !v.empty() && v != "0";' in ci)
check("unset: Reset frees every block as before (free_blocks(~0uLL) is the else branch of the one call site)",
      re.search(r"if \(cmdlistKeepBlock\(\)\)\s*cpu_command_allocator\.free_blocks_keep_first\(\);\s*else\s*cpu_command_allocator\.free_blocks\(~0uLL\);", ci) is not None
      and ci.count("free_blocks_keep_first()") == 1)

# --- dfd523ef: dynamic buffers
dy = (S / "dxmt/dxmt_dynamic.cpp").read_text()
fn = cut(dy, "static uint32_t\ndynKeepFor(uint64_t length) {", "\n}\n", "dynKeepFor", keep_end=3)
check("DXMT_DYN_KEEP: the trim takes the new branch only for a non-zero keep count",
      "if (const uint32_t keep = dynKeepFor(buffer ? buffer->length() : 0)) {" in dy and dy.count("dynKeepFor(") == 2)
good, why = run_cxx(r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <string>
namespace env { static std::string getEnvVar(const char *n) { const char *v = getenv(n); return v ? v : ""; } }
template <typename... A> static void ERR(A...) {}
''' + fn + r'''
int main(int argc, char **argv) {
  uint64_t len = strtoull(argv[1], nullptr, 0); uint32_t want = (uint32_t)strtoul(argv[2], nullptr, 0);
  uint32_t got = dynKeepFor(len);
  if (got != want) { fprintf(stderr, "dynKeepFor(%llu) = %u, expected %u\n", (unsigned long long)len, got, want); return 1; }
  return 0;
}
''', [(["256", "0"], {}), (["65536", "0"], {}), (["256", "0"], {"DXMT_DYN_KEEP": "0"}), (["256", "0"], {"DXMT_DYN_KEEP": "-5"}),
      (["256", "0"], {"DXMT_DYN_KEEP": "x"}), (["256", "1000"], {"DXMT_DYN_KEEP": "1000"}),
      (["65536", "64"], {"DXMT_DYN_KEEP": "1000"}), (["8192", "512"], {"DXMT_DYN_KEEP": "1000"}),
      (["256", "64"], {"DXMT_DYN_KEEP": "10"}), (["256", "64"], {"DXMT_DYN_KEEP": "1000", "DXMT_DYN_KEEP_KB": "0"}),
      (["0", "1000"], {"DXMT_DYN_KEEP": "1000"}), (["256", "16384"], {"DXMT_DYN_KEEP": "99999999"})])
check("dynKeepFor: 0 when unset, 0, negative or not a number; max(64, min(N, KB*1024/size)) otherwise (12 runs)", good, why)

# --- 0b28f9ff: late readback
d9 = (S / "d3d9/d3d9_device.cpp").read_text()
check("DXMT_D9_LATE_READBACK: read once, on only for a non-empty value other than 0; both uses test it first",
      'static const bool late_enabled = [] { const auto v = env::getEnvVar("DXMT_D9_LATE_READBACK"); return !v.empty() && v != "0"; }();' in d9
      and d9.count("if (late_enabled") == 2 and d9.count("late_enabled") == 3)

check("BCD: a late readback entry whose signals are above this device's sequence (left by another device) starts again",
      in_order(d9, "if (st.bytes != total_bytes) {", "if (st.signal[0] > m_currentCmdSeq || st.signal[1] > m_currentCmdSeq) {",
               "st.signal[0] = st.signal[1] = 0;", "st.warmup = kLateWarmup;", "if (st.backing == nullptr) {")
      and d9.count("const uint64_t late_signal = m_currentCmdSeq;") == 1)

# --- 7bb5e807 + 1aab69fd + BCD: typeless textures
rh = (S / "d3d11/d3d11_resource_helper.cpp").read_text()
decision = cut(rh, '    static const bool compression = env::getEnvVar("DXMT_TYPELESS_COMPRESSION") == "1";',
               "      metal_usage |= WMTTextureUsagePixelFormatView;", "the typeless decision",
               keep_end=len("      metal_usage |= WMTTextureUsagePixelFormatView;"))
good, why = run_cxx(r'''
#include <cstdint>
#include <cstdlib>
#include <string>
namespace env { static std::string getEnvVar(const char *n) { const char *v = getenv(n); return v ? v : ""; } }
enum { MTL_DXGI_FORMAT_DEPTH_PLANER = 1, MTL_DXGI_FORMAT_STENCIL_PLANER = 2, MTL_DXGI_FORMAT_BC = 4 };
enum { WMTTextureUsagePixelFormatView = 0x10 };
struct { unsigned Flag; } metal_format;
int main(int argc, char **argv) {
  metal_format.Flag = (unsigned)atoi(argv[1]); unsigned metal_usage = 0;
''' + decision + r'''
  return ((metal_usage != 0) == (argv[2][0] == '1')) ? 0 : 1;
}
''', [([str(f), "1"], e) for f in (0, 1, 2, 4) for e in ({}, {"DXMT_TYPELESS_COMPRESSION": "0"}, {"DXMT_TYPELESS_PFV": "1"},
                                                          {"DXMT_TYPELESS_COMPRESSION": "yes"},
                                                          {"DXMT_TYPELESS_COMPRESSION": "1", "DXMT_TYPELESS_PFV": "1"})]
    + [(["0", "0"], {"DXMT_TYPELESS_COMPRESSION": "1"})]
    + [([str(f), "1"], {"DXMT_TYPELESS_COMPRESSION": "1"}) for f in (1, 2, 4, 3)])
check("typeless textures: pixelFormatView always, except a colour format with DXMT_TYPELESS_COMPRESSION=1 and no DXMT_TYPELESS_PFV=1 (25 runs)",
      good, why)
tx = (S / "dxmt/dxmt_texture.cpp").read_text()
check("BCD: views are examined for a missing pixelFormatView only with DXMT_TYPELESS_COMPRESSION=1",
      'static const bool report_pfv_views = env::getEnvVar("DXMT_TYPELESS_COMPRESSION") == "1";' in tx
      and "if (report_pfv_views && !(allocation->descriptor->usage() & WMTTextureUsagePixelFormatView)) {" in tx
      and tx.count("[pfv-view]") == 1)

# --- 43d07629: the 64-bit Direct3D 9 kill switch
d3 = (S / "d3d9/d3d9.cpp").read_text()
check("MADEIRA_D3D9_X64=0 is read by the 64-bit PE build only (the i386 and native builds answer false) and only \"0...\" disables",
      "#if defined(_WIN32) && defined(_WIN64) && !defined(DXMT_MADEIRA)\nstatic bool madeira_d3d9_x64_disabled() {" in d3
      and "return n > 0 && n < sizeof(v) && v[0] == '0';" in d3
      and "#else\nstatic bool madeira_d3d9_x64_disabled() { return false; }\n#endif" in d3)
bridge = (R / "app/Madeira/WineProcessBridge.m").read_text()
policy = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check("BCD links d3d9-dxmt.dll only for the value dxmt, and a list may only set dxmt or wined3d: the DLL never sees a value that starts with 0",
      'if (d3d9X64 && strcmp(d3d9X64, "dxmt") == 0) {' in bridge and "MADEIRA_D3D9_X64 may only be dxmt or wined3d" in policy)

# --- the list policy: a downloaded list may set each switch (and the min/max one), with its values checked
allowed = cut(policy, "static let allowedEnvNames: Set<String> = [", "\n    ]\n", "RecommendationsPolicy.allowedEnvNames")
for name in ("DXMT_CMDLIST_KEEP_BLOCK", "DXMT_DYN_KEEP", "DXMT_DYN_KEEP_KB", "DXMT_D9_LATE_READBACK", "MADEIRA_D3D11_LOCAL_SHARE",
             "DXMT_TYPELESS_COMPRESSION", "DXMT_TYPELESS_PFV", "MADEIRA_DXGI_VK_SWAPCHAIN", "MADEIRA_PRECISE_MINMAX"):
    check(f"a downloaded list may set env.{name} (allowedEnvNames) and its value is checked",
          f'"{name}"' in allowed and policy.count(f'"{name}"') >= 2)
check("their values: 0 or 1 for the six switches, 0 or 65..65536 names, 64..65536 KB, 1 for the min/max switch",
      'case "DXMT_CMDLIST_KEEP_BLOCK", "DXMT_D9_LATE_READBACK", "MADEIRA_D3D11_LOCAL_SHARE", "DXMT_TYPELESS_COMPRESSION", "DXMT_TYPELESS_PFV", "MADEIRA_DXGI_VK_SWAPCHAIN":' in policy
      and "\\(name) is 0 or 1" in policy and "DXMT_DYN_KEEP is 0 or 65 to 65536" in policy and "n == 0 || (65...65536).contains(n)" in policy
      and "DXMT_DYN_KEEP_KB is 64 to 65536" in policy and "MADEIRA_PRECISE_MINMAX is 1" in policy)
if not ok:
    print("FAILED: dxmt-upstream-optins")
    sys.exit(1)
print("PASS: the switches of the 2026-11-14 DXMT merge are off by default; the old lines run without them")
