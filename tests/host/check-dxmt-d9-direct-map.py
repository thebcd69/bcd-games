#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The 64-bit Direct3D 9 frontend locks dynamic buffers in place, opt-in (dxmt 880241a + 4842a04 by Ixion;
BCD: off unless env.MADEIRA_D3D9_DIRECT_MAP = 1); the DXMT frontend's source, no device.

A D3D9 lock handed out a host mirror and every draw that read a changed buffer renamed it and copied the
whole mirror in again (about 43 % of Team Fortress 2's D3D9 thread; upstream's apbench timedemo 42.8 -> 83.7
fps on an iPhone 15 Pro Max). A 64-bit guest addresses Metal's own allocation, so a DEFAULT + DYNAMIC buffer
there can lock the allocation draws read: DISCARD takes a GPU-idle one, NOOVERWRITE keeps the current one,
a plain lock renames and carries the contents over; such a buffer has no mirror, never refreshes, and its
allocations use the default CPU cache mode instead of write-combined (the application reads what it locked).
Only the arm64ec frontend (d3d9-dxmt.dll, env.MADEIRA_D3D9_X64 = dxmt) defines __x86_64__ without
DXMT_MADEIRA; the 32-bit d3d9-emulated.dll and the native ARM64 frontend keep the mirror whatever is set.

1. Source: the path is on only for env.MADEIRA_D3D9_DIRECT_MAP == "1" (read once), only under
   __x86_64__ and not DXMT_MADEIRA, only for DEFAULT + DYNAMIC buffers on a device that is not
   software-vertex-processing-only; with the key unset a buffer is created with the write-combined class
   and locks and refreshes exactly as before (every new branch is behind m_direct).
2. directMapEnabled() is cut out and compiled on the host for each environment value and each compile
   target (x86_64 or not, DXMT_MADEIRA or not): unset, "", "0", "true", "2" -> off; "1" -> on only for the
   64-bit non-native build.
3. lockDirect() is cut out and compiled with fakes: NOOVERWRITE keeps the allocation and renames nothing;
   DISCARD renames and copies nothing; a plain lock renames and carries the old contents over; a failed
   rename returns its error and hands out nothing; a name without a CPU address is D3DERR_INVALIDCALL.
4. CI runs this check.
SKIP (exit 0) without the dxmt sources, the Direct3D headers or a C++ compiler.
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
if not (d9 / "d3d9_buffer.cpp").is_file() or not (inc_dx / "d3d9.h").is_file():
    print("SKIP: no dxmt checkout with the Direct3D headers (run in the full tree)")
    sys.exit(0)
cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
src = (d9 / "d3d9_buffer.cpp").read_text()
hdr = (d9 / "d3d9_buffer.hpp").read_text()
mapp = (d9 / "d3d9_buffer_map.hpp").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


if "directMapEnabled" not in src:
    check("d3d9_buffer.cpp has the direct map (dxmt 880241a): pin the dxmt fork at the commits that carry it", False)
    print("FAILED: D3D9 direct map")
    sys.exit(1)


def cut(text, a, b):
    i = text.index(a)
    return text[i:text.index(b, i) + len(b)]


enabled_fn = cut(src, "bool\ndirectMapEnabled() {", "\n}\n")
wants_fn = cut(src, "bool\nwantsDirectMap(", "\n}\n")
lock_fn = cut(src, "HRESULT\nlockDirect(", "\n}\n")

# ---- 1. source
check("on only for env.MADEIRA_D3D9_DIRECT_MAP == \"1\", read once",
      'static const bool enabled = env::getEnvVar("MADEIRA_D3D9_DIRECT_MAP") == "1";' in enabled_fn
      and src.count('"MADEIRA_D3D9_DIRECT_MAP"') == 1)
check("... only under __x86_64__ and not DXMT_MADEIRA, false otherwise",
      "#if defined(__x86_64__) && !defined(DXMT_MADEIRA)" in enabled_fn and "#else\n  return false;\n#endif" in enabled_fn)
check("... only for DEFAULT + DYNAMIC buffers on a device that is not software-VP-only",
      "pool == D3DPOOL_DEFAULT" in wants_fn and "(usage & D3DUSAGE_DYNAMIC)" in wants_fn and "!device->canOnlySWVP()" in wants_fn
      and "directMapEnabled() &&" in wants_fn)
check("both buffer kinds create the write-combined class unless the buffer is direct-mapped",
      src.count("Flags<BufferAllocationFlag>(BufferAllocationFlag::CpuWriteCombined)") == 2
      and src.count("direct ? useCachedAllocation(") == 2)
check("every use of the direct path is behind m_direct (refresh, lock, host pointer)",
      src.count("if (m_direct) {") == 2 and "if (!m_dirty || m_direct)" in src and src.count("if (!m_dirty || m_direct)") == 2
      and "return m_direct ? m_dynamic->immediateMappedMemory() : m_hostPtr;" in hdr and hdr.count("m_direct ?") == 2)
check("m_direct stays false unless the path is wanted and the allocation has a CPU address",
      src.count("m_direct = direct && m_dynamic->immediateMappedMemory() != nullptr;") == 2 and "bool m_direct = false;" in hdr)
check("the mirror is freed only for a direct-mapped buffer",
      src.count("if (m_direct && m_hostPtr) {\n    guest_free(m_hostPtr);\n    m_hostPtr = nullptr;\n  }") == 2)
check("the map header says MADEIRA_D3D9_DIRECT_MAP=1 turns it on", "MADEIRA_D3D9_DIRECT_MAP=1 turns it" in mapp
      and "MADEIRA_D3D9_DIRECT_MAP=0 keeps" not in mapp)

# ---- 2 + 3. the harness
if not cxx:
    print("SKIP: no C++ compiler for the behaviour part")
else:
    harness = r'''
#include "d3d9.h"
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <cstdlib>
namespace env { static std::string getEnvVar(const char *n) { const char *v = getenv(n); return v ? std::string(v) : std::string(); } }
namespace dxmt {
struct BufferAllocation {};
template <class T> struct Rc { T *p = nullptr; };
/* a fake dynamic buffer: two allocations, the current one is what the "draw" reads */
struct DynamicBuffer {
  std::vector<unsigned char> a, b; bool cur_a = true; bool no_mem = false; int renames = 0;
  void *immediateMappedMemory() { return no_mem ? nullptr : (cur_a ? a.data() : b.data()); }
  Rc<BufferAllocation> immediateName() { return {}; }
};
struct MTLD3D9Device {};
static HRESULT g_rename_result = D3D_OK;
static HRESULT renameDynamicBuffer(MTLD3D9Device *, DynamicBuffer *d, uint64_t, uint64_t) {
  if (g_rename_result != D3D_OK) return g_rename_result;
  d->cur_a = !d->cur_a; d->renames++;
  auto &n = d->cur_a ? d->a : d->b;
  std::fill(n.begin(), n.end(), 0xEE);   /* a recycled allocation holds stale bytes */
  return D3D_OK;
}
''' + "namespace {\n" + enabled_fn.replace("defined(__x86_64__)", "defined(HARNESS_X64)") + "\n" + lock_fn + "\n} // namespace\n" + r'''
} // namespace dxmt
using namespace dxmt;
static int fails = 0;
static void expect(bool c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
int main(int argc, char **argv) {
  if (argc > 1 && !strcmp(argv[1], "enabled")) { printf("%d\n", dxmt::directMapEnabled() ? 1 : 0); return 0; }
  MTLD3D9Device dev; DynamicBuffer d; d.a.assign(64, 0xAA); d.b.assign(64, 0xBB);
  void *out = nullptr; HRESULT hr;
  const void *before = d.immediateMappedMemory();
  hr = lockDirect(&dev, &d, 64, D3DLOCK_NOOVERWRITE, 5, 3, &out);
  expect(hr == D3D_OK && out == before && d.renames == 0, "NOOVERWRITE: the current allocation, nothing renamed");
  hr = lockDirect(&dev, &d, 64, D3DLOCK_DISCARD, 6, 4, &out);
  expect(hr == D3D_OK && d.renames == 1 && out == d.immediateMappedMemory() && out != before
         && ((unsigned char *)out)[0] == 0xEE, "DISCARD: renamed, nothing carried over (the new allocation is the recycled one)");
  const void *cur = d.immediateMappedMemory();
  std::memset(d.a.data(), 0, 0); /* no-op, keeps the compiler quiet about unused */
  for (size_t i = 0; i < 64; i++) ((unsigned char *)const_cast<void *>(cur))[i] = (unsigned char)i;
  hr = lockDirect(&dev, &d, 64, 0, 7, 5, &out);
  bool carried = hr == D3D_OK && d.renames == 2 && out == d.immediateMappedMemory() && out != cur;
  for (size_t i = 0; carried && i < 64; i++) carried &= ((unsigned char *)out)[i] == i;
  expect(carried, "a plain lock: renamed and the old contents carried over, byte for byte");
  hr = lockDirect(&dev, &d, 32, D3DLOCK_READONLY, 8, 6, &out);
  expect(hr == D3D_OK && d.renames == 3, "a read-only lock (no NOOVERWRITE, no DISCARD) renames too (it must not hand back what a queued draw reads)");
  g_rename_result = D3DERR_OUTOFVIDEOMEMORY; out = (void *)0x1; int r0 = d.renames;
  hr = lockDirect(&dev, &d, 64, D3DLOCK_DISCARD, 9, 7, &out);
  expect(hr == D3DERR_OUTOFVIDEOMEMORY && d.renames == r0 && out == (void *)0x1, "a failed rename returns its error and hands out nothing");
  g_rename_result = D3D_OK;
  hr = lockDirect(&dev, &d, 64, D3DLOCK_NOOVERWRITE, 10, 8, &out);
  expect(hr == D3D_OK && out == d.immediateMappedMemory(), "after a failure the next lock works");
  d.no_mem = true;
  hr = lockDirect(&dev, &d, 64, D3DLOCK_NOOVERWRITE, 11, 9, &out);
  expect(hr == D3DERR_INVALIDCALL && out == nullptr, "an allocation without a CPU address: D3DERR_INVALIDCALL, nothing handed out");
  return fails;
}
'''
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "h.cpp").write_text(harness)
        flags = ["-std=c++20", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unknown-pragmas",
                 "-Wno-extern-c-compat", "-Wno-microsoft-exception-spec", "-Wno-ignored-attributes", "-Wno-unused-parameter"]
        inc = [f"-I{inc_dx}", f"-I{inc_win}", f"-I{root / 'dxmt/include'}"]
        variants = {}
        for name, defs in (("x64", ["-DHARNESS_X64=1"]), ("x64-native", ["-DHARNESS_X64=1", "-DDXMT_MADEIRA=1"]), ("not-x64", [])):
            exe = t / f"h-{name}"
            b = subprocess.run([cxx, *flags, *defs, *inc, "-fsanitize=address,undefined", "-o", str(exe), str(t / "h.cpp")], capture_output=True, text=True)
            if b.returncode:
                b = subprocess.run([cxx, *flags, *defs, *inc, "-o", str(exe), str(t / "h.cpp")], capture_output=True, text=True)
            check(f"the cut-out direct-map functions compile on the host ({name})", b.returncode == 0)
            if b.returncode:
                print(b.stderr[-2500:])
            else:
                variants[name] = exe
        if "x64" in variants:
            r = subprocess.run([str(variants["x64"])], capture_output=True, text=True, timeout=60)
            print(r.stdout.rstrip())
            if r.returncode:
                print(r.stderr[-1500:])
            check("lockDirect behaves (fakes for the allocation)", r.returncode == 0)

            def enabled(variant, value):
                env = {k: v for k, v in os.environ.items() if k != "MADEIRA_D3D9_DIRECT_MAP"}
                if value is not None:
                    env["MADEIRA_D3D9_DIRECT_MAP"] = value
                return subprocess.run([str(variants[variant]), "enabled"], capture_output=True, text=True, env=env).stdout.strip()

            for value, want in ((None, "0"), ("", "0"), ("0", "0"), ("true", "0"), ("2", "0"), ("1", "1")):
                got = enabled("x64", value)
                check(f"64-bit frontend, MADEIRA_D3D9_DIRECT_MAP={'(unset)' if value is None else repr(value)}: {'on' if want == '1' else 'off'}", got == want)
            for variant in ("x64-native", "not-x64"):
                if variant in variants:
                    check(f"{variant} (the native ARM64 / 32-bit builds): off even with =1",
                          enabled(variant, "1") == "0" and enabled(variant, None) == "0")

# ---- 5. BCD's index-buffer range refresh (tools/patch-dxmt-d9-ib-range.py, on by default) meets the direct map
# A direct-mapped index buffer has no mirror (m_hostPtr is null); refreshRange would copy from it. The patched refreshRange leaves
# such a buffer alone before it looks at anything else.
with tempfile.TemporaryDirectory() as t5:
    t5 = Path(t5)
    shutil.copytree(d9, t5 / "dxmt/src/d3d9")
    r5 = subprocess.run([sys.executable, str(root / "tools/patch-dxmt-d9-ib-range.py")], cwd=t5, capture_output=True, text=True)
    check("tools/patch-dxmt-d9-ib-range.py applies on top of the direct map", r5.returncode == 0)
    if r5.returncode == 0:
        b5 = (t5 / "dxmt/src/d3d9/d3d9_buffer.cpp").read_text()
        rr = b5[b5.index("MTLD3D9IndexBuffer::refreshRange(uint32_t lo, uint32_t hi) {"):]
        rr = rr[:rr.index("\n}\n")]
        check("refreshRange returns first thing for a direct-mapped index buffer (before the state machine)",
              "if (m_direct)" in rr and "return;" in rr and rr.index("if (m_direct)") < rr.index("m_range.onRangeDraw") and rr.index("if (m_direct)") < rr.index("m_hostPtr"))
        check("the other patched places keep their direct-map branches (Lock returns before the mirror code)",
              b5.count("if (m_direct) {") == 2 and "if (!m_dirty || m_direct)" in b5)

check("this check runs in the workflow", "tests/host/check-dxmt-d9-direct-map.py" in wf)
print("FAILED: D3D9 direct map" if failures else "PASS: D3D9 direct map")
sys.exit(1 if failures else 0)
