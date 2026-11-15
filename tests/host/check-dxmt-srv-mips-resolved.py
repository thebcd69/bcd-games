#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A shader resource view made with MipLevels = -1 reports the resolved count (dxmt 988c6335 by meshoklv, upstream merge 2026-11-14).

Direct3D 11 resolves "all mips from MostDetailedMip on" when the view is created and GetDesc returns the count; DXMT kept the
caller's 0xFFFFFFFF, Dark Souls III sized an array with it and wrote through NULL (DarkSoulsIII.exe+0x176d65b). The fix is in
ExtractEntireResourceViewDescription (dxmt/src/d3d11/d3d11_resource.hpp), with no switch: the old answer is wrong on Windows'
terms. BCD's own opt-in for the same crash (tools/patch-dxmt-srv-getdesc-mips.py, env.MADEIRA_SRV_GETDESC_RESOLVE) stays and still
applies. This cuts the resolving block out of the header and runs it for every texture view dimension: -1 becomes
total - MostDetailedMip, an explicit count is left alone, MostDetailedMip at or past the end leaves -1 (the view is refused later
as before), and a buffer view is not touched. SKIP (exit 0) without the dxmt sources."""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
D = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt"))
src = D / "src/d3d11/d3d11_resource.hpp"
if not src.exists():
    print(f"SKIP: no {src}")
    sys.exit(0)
s = src.read_text()
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-dxmt-srv-mips-resolved.py" in wf)
start = "      if constexpr (std::is_same_v<VIEW_DESC, D3D11_SHADER_RESOURCE_VIEW_DESC1>) {"
check("the resolving block is in ExtractEntireResourceViewDescription, once, after the format is settled",
      s.count(start) == 1 and 0 <= s.find("pViewDescOut->Format = pResourceDesc->Format;") < s.find(start))
a = s.find(start)
d = s.find("        default:\n          break;\n        }", a) if a >= 0 else -1
b = s.find("\n      }\n", d) if d >= 0 else -1
if s.count(start) == 1 and b < 0:
    check("the resolving block ends where this check expects it (default: break; and its closing brace)", False)
if s.count(start) == 1 and b >= 0:
    block = s[a:b + len("\n      }\n")]
    check("it has no switch (a fix of a wrong answer)", "getEnvVar" not in block and "getOption" not in block)
    script = (R / "tools/patch-dxmt-srv-getdesc-mips.py")
    check("BCD's opt-in for the same crash is still built into both d3d11.dll (workflow step and d3d11-src.dll's patch list)",
          "python3 tools/patch-dxmt-srv-getdesc-mips.py" in wf and '"patch-dxmt-srv-getdesc-mips.py"' in (R / "tools/build-d3d11-dll.sh").read_text()
          and script.exists())
    cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if not cxx:
        print("note: no C++ compiler; the block is not run")
    else:
        harness = r'''
#include <cassert>
#include <type_traits>
typedef unsigned int UINT;
enum { D3D11_SRV_DIMENSION_BUFFER = 1, D3D11_SRV_DIMENSION_TEXTURE1D, D3D11_SRV_DIMENSION_TEXTURE1DARRAY, D3D11_SRV_DIMENSION_TEXTURE2D,
       D3D11_SRV_DIMENSION_TEXTURE2DARRAY, D3D11_SRV_DIMENSION_TEXTURE2DMS, D3D11_SRV_DIMENSION_TEXTURE2DMSARRAY,
       D3D11_SRV_DIMENSION_TEXTURE3D, D3D11_SRV_DIMENSION_TEXTURECUBE, D3D11_SRV_DIMENSION_TEXTURECUBEARRAY };
struct Mips { UINT MostDetailedMip; UINT MipLevels; };
struct D3D11_SHADER_RESOURCE_VIEW_DESC1 { int Format; int ViewDimension;
  union { Mips Texture1D, Texture1DArray, Texture2D, Texture2DArray, Texture3D, TextureCube, TextureCubeArray; struct { UINT a, b; } Buffer; }; };
struct D3D11_RENDER_TARGET_VIEW_DESC1 { int Format; int ViewDimension; Mips Texture2D; };
struct RESOURCE_DESC { UINT MipLevels; };
template <typename VIEW_DESC> void resolve_view(const RESOURCE_DESC *pResourceDesc, VIEW_DESC *pViewDescOut) {
''' + block + r'''
}
int main() {
  const int dims[] = { D3D11_SRV_DIMENSION_TEXTURE1D, D3D11_SRV_DIMENSION_TEXTURE1DARRAY, D3D11_SRV_DIMENSION_TEXTURE2D,
                       D3D11_SRV_DIMENSION_TEXTURE2DARRAY, D3D11_SRV_DIMENSION_TEXTURE3D, D3D11_SRV_DIMENSION_TEXTURECUBE,
                       D3D11_SRV_DIMENSION_TEXTURECUBEARRAY };
  RESOURCE_DESC res{10};
  for (int dim : dims) {
    D3D11_SHADER_RESOURCE_VIEW_DESC1 v{0, dim, {{0, ~0u}}};
    resolve_view(&res, &v); assert(v.Texture2D.MipLevels == 10 && v.Texture2D.MostDetailedMip == 0);
    v = {0, dim, {{3, ~0u}}}; resolve_view(&res, &v); assert(v.Texture2D.MipLevels == 7);
    v = {0, dim, {{9, ~0u}}}; resolve_view(&res, &v); assert(v.Texture2D.MipLevels == 1);
    v = {0, dim, {{2, 4}}};   resolve_view(&res, &v); assert(v.Texture2D.MipLevels == 4);          /* an explicit count stays */
    v = {0, dim, {{10, ~0u}}}; resolve_view(&res, &v); assert(v.Texture2D.MipLevels == ~0u);       /* past the end: left as it came */
  }
  D3D11_SHADER_RESOURCE_VIEW_DESC1 b{0, D3D11_SRV_DIMENSION_BUFFER, {{5, ~0u}}};
  resolve_view(&res, &b); assert(b.Texture2D.MipLevels == ~0u && b.Texture2D.MostDetailedMip == 5); /* a buffer view is not a mip chain */
  D3D11_SHADER_RESOURCE_VIEW_DESC1 ms{0, D3D11_SRV_DIMENSION_TEXTURE2DMS, {{0, ~0u}}};
  resolve_view(&res, &ms); assert(ms.Texture2D.MipLevels == ~0u);
  D3D11_RENDER_TARGET_VIEW_DESC1 rt{0, D3D11_SRV_DIMENSION_TEXTURE2D, {0, ~0u}};
  resolve_view(&res, &rt); assert(rt.Texture2D.MipLevels == ~0u);                                   /* only shader resource views */
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            t = pathlib.Path(tmp)
            (t / "h.cpp").write_text(harness)
            c = subprocess.run([cxx, "-std=c++20", "-Wall", str(t / "h.cpp"), "-o", str(t / "h")], capture_output=True, text=True)
            check("the block compiles with stand-in types", c.returncode == 0, c.stderr)
            if c.returncode == 0:
                r = subprocess.run([str(t / "h")], capture_output=True, text=True)
                check("-1 resolves to total - MostDetailedMip for the seven texture dimensions; explicit counts, an out-of-range first mip, "
                      "buffer and multisample views and other view kinds are left alone", r.returncode == 0, r.stderr)
if not ok:
    print("FAILED: dxmt-srv-mips-resolved")
    sys.exit(1)
print("PASS: a shader resource view made with MipLevels = -1 reports the resolved count")
