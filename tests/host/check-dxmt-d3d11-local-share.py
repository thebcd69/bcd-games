#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Direct3D 11 shared texture handles inside the process are opt-in (upstream merge 2026-11-14).

dxmt ebb9189f (willfaust/dxmt, adapted there from Playport) hands a texture made without a shared flag an in-process handle from
GetSharedHandle, which OpenSharedResource resolves in the process (Unity's video output; Windows answers NULL). BCD's fork keeps
the old answers unless env.MADEIRA_D3D11_LOCAL_SHARE = 1. This reads dxmt/src (or $DXMT_SRC_DIR):

1. Source: the three places upstream changed take their old lines while the switch is off (NULL from GetSharedHandle, E_FAIL for a
   mach port that cannot be registered, no handle looked up by OpenSharedResource), and the switch is read once and is on only for "1".
2. The table itself (LocalShareAdd / Remove / Open, IsLocalShare), cut out of the source and compiled with fakes: handles are
   0x40000000 | n, a D3DKMT handle (both top bits) is never taken for one, a second Add keeps the handle, an unknown or retired handle
   and a texture whose last reference is gone are refused without opening it, a live one is opened once on the asking device.
SKIP (exit 0) without the dxmt sources."""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
src = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src/d3d11/d3d11_texture_device.cpp"
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


wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-dxmt-d3d11-local-share.py" in wf)
check("the switch is read once and is on only for MADEIRA_D3D11_LOCAL_SHARE == \"1\"",
      s.count('static const bool enabled = env::getEnvVar("MADEIRA_D3D11_LOCAL_SHARE") == "1";') == 1
      and s.count('"MADEIRA_D3D11_LOCAL_SHARE"') == 1)
get = cut(s, "  GetSharedHandle(HANDLE *pSharedHandle) override {", "\n  }\n", "GetSharedHandle")
unflagged = get[max(get.find("if (!(this->desc.MiscFlags & (D3D11_RESOURCE_MISC_SHARED | D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX))) {"), 0):]
check("off: a texture without a shared flag answers GetSharedHandle with NULL and S_OK before anything is shared",
      in_order(unflagged, "if (!(this->desc.MiscFlags & (D3D11_RESOURCE_MISC_SHARED | D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX))) {",
               "if (!LocalShareEnabled()) {", "*pSharedHandle = NULL;", "return S_OK;", "ShareLocally();"))
check("ShareLocally is called in two places only, both behind the switch", s.count("ShareLocally();") == 2)
reg = cut(s, "if (!WMTBootstrapRegister(runtimeData.mach_port_name, mach_port)) {", "runtimeData.dimension = tag::dimension;", "the mach port registration")
check("off: a mach port that cannot be registered fails the creation with E_FAIL as before",
      in_order(reg, "if (!LocalShareEnabled()) {", 'ERR("DeviceTexture: Failed to register mach port for shared texture");',
               "return E_FAIL;", "tex->ShareLocally();"))
check("off: OpenSharedResource looks no in-process handle up",
      "if (LocalShareEnabled() && IsLocalShare(reinterpret_cast<uintptr_t>(hResource))) {" in s
      and s.count("IsLocalShare(reinterpret_cast<uintptr_t>(hResource))") == 1)

cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
a = s.find("struct LocalShare {")
o = s.find("\nLocalShareOpen(MTLD3D11Device *pDevice", a) if a >= 0 else -1
b = s.find("\n}\n", o) if o >= 0 else -1   # the workflow's SRV patch puts its helpers after LocalShareOpen
table = s[a:b + 3] if b >= 0 else ""
check("the table code is where it is expected (LocalShare .. LocalShareOpen)",
      all(n in table for n in ("LocalShareEnabled()", "LocalShareAdd(", "LocalShareRemove(", "IsLocalShare(", "LocalShareOpen(")))
check("the table and its lock are allocated once and never destroyed (a texture alive at process exit still retires its handle)",
      "static std::mutex &local_share_mutex = *new std::mutex;" in table
      and "static std::unordered_map<uint32_t, LocalShare> &local_shares = *new std::unordered_map<uint32_t, LocalShare>;" in table)
if not table:
    pass
elif not cxx:
    print("note: no C++ compiler; the table is not run")
else:
    harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#include <string>
#include <unordered_map>
typedef long HRESULT; typedef const void *REFIID;
#define E_INVALIDARG ((HRESULT)0x80070057L)
#define S_OK ((HRESULT)0)
struct MTLD3D11Device { int id; };
namespace env { static std::string getEnvVar(const char *n) { const char *v = getenv(n); return v ? v : ""; } }
''' + table + r'''
static int opens; static MTLD3D11Device *opened_on; static bool alive = true;
static bool acquire(void *) { return alive; }
static HRESULT open_fn(void *, MTLD3D11Device *d, REFIID, void **out) { opens++; opened_on = d; *out = (void *)0x1234; return S_OK; }
int main(int argc, char **argv) {
  const bool want = argc > 1 && argv[1][0] == '1';
  assert(LocalShareEnabled() == want);
  uint32_t h1 = 0, h2 = 0; int t1, t2; void *out = nullptr; MTLD3D11Device dev{7};
  LocalShareAdd({&t1, &acquire, &open_fn}, h1);
  LocalShareAdd({&t2, &acquire, &open_fn}, h2);
  assert(h1 == 0x40000001u && h2 == 0x40000002u);
  uint32_t again = h1; LocalShareAdd({&t2, &acquire, &open_fn}, again); assert(again == h1);   /* a second Add keeps the handle */
  assert(IsLocalShare(h1) && IsLocalShare(h2));
  assert(!IsLocalShare(0) && !IsLocalShare(0xc0000001u) && !IsLocalShare(0x80000001u) && !IsLocalShare(0x00000004u));
  assert(!IsLocalShare((uintptr_t)0x140000001ull));
  assert(LocalShareOpen(&dev, 0x40000099u, nullptr, &out) == E_INVALIDARG && opens == 0);       /* unknown */
  assert(LocalShareOpen(&dev, h1, nullptr, &out) == S_OK && opens == 1 && opened_on == &dev);    /* live */
  alive = false;
  assert(LocalShareOpen(&dev, h2, nullptr, &out) == E_INVALIDARG && opens == 1);                 /* last reference gone: not revived */
  alive = true;
  LocalShareRemove(h1);
  assert(LocalShareOpen(&dev, h1, nullptr, &out) == E_INVALIDARG && opens == 1);                 /* retired */
  assert(LocalShareOpen(&dev, h2, nullptr, &out) == S_OK && opens == 2);
  return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / "h.cpp").write_text(harness)
        c = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wno-unused-function", str(t / "h.cpp"), "-o", str(t / "h")],
                           capture_output=True, text=True)
        check("the table compiles with fakes", c.returncode == 0, c.stderr)
        if c.returncode == 0:
            for value, arg, what in ((None, "0", "unset: off"), ("0", "0", "0: off"), ("yes", "0", "another word: off"), ("1", "1", "1: on")):
                e = {k: v for k, v in os.environ.items() if k != "MADEIRA_D3D11_LOCAL_SHARE"}
                if value is not None:
                    e["MADEIRA_D3D11_LOCAL_SHARE"] = value
                r = subprocess.run([str(t / "h"), arg], env=e, capture_output=True, text=True)
                check(f"MADEIRA_D3D11_LOCAL_SHARE {what}; handles, refusals and one open per live handle", r.returncode == 0, r.stderr)
if not ok:
    print("FAILED: dxmt-d3d11-local-share")
    sys.exit(1)
print("PASS: Direct3D 11 in-process shared handles are opt-in (MADEIRA_D3D11_LOCAL_SHARE=1); the table refuses dead and unknown handles")
