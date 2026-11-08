#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""ID3D12GraphicsCommandList8 (upstream Madeira ml1300, 56260b9), opt-in d3d12-list8 = 1; no GPU, no Wine.

Final Fantasy Tactics - The Ivalice Chronicles creates its command lists as ID3D12GraphicsCommandList8 (List7 plus
OMSetFrontAndBackStencilRef) and stops with a "Success." message box when QueryInterface refuses it. Off (the default),
List8 is refused exactly as before: GTA V, RDR2, Expedition 33 and others log QueryInterface refusals (their IIDs are
redacted in Send Log, so a List8 request among them cannot be ruled out) and play on. On, the list answers it; the one
new method is allowed only with OPTIONS14.IndependentFrontAndBackStencilRefMaskSupported, which is reported off, and
winemetal carries one stencil reference: the front value is used.

1. Source: the key is read once, default 0; List8 is answered only behind it; the other IIDs are answered as before; the
   List8 slot is appended after the List7 table (the object's vtable pointer is the List7 part's address, as before) and
   filled in build_vtables; the method forwards to list_OMSetStencilRef with the front value.
2. list_QI is cut out and compiled on the host with fake IIDs: key off: List8 refused, every other IID of the old set
   answered with a reference added; key on: List8 answered too; an unknown IID goes to the generic path as before.
3. With the arm64ec toolchain: the appended slot sits right after List7's last method (Barrier) in a struct whose first
   member is the List7 table, and the IID is Microsoft's (ee936ef9-599d-4d28-938e-23c4ad05ce51).
4. CI runs this check.
SKIP (exit 0) of the behaviour parts without a C++ compiler / the toolchain.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


def cut(text, a, b):
    i = text.index(a)
    return text[i:text.index(b, i)]


qi = cut(src, "static HRESULT STDMETHODCALLTYPE list_QI(ID3D12GraphicsCommandList *This, REFIID riid, void **out) {", "\n}\n") + "\n}\n"
on_fn = cut(src, "static int mad_list8_on(void) {", "\n}\n") + "\n}\n"
check("the key is read once, default 0, and logged when on",
      'static int on = -1;' in on_fn and 'mad_cfg_int_pe("d3d12-list8", 0) ? 1 : 0' in on_fn and src.count('"d3d12-list8"') == 1
      and "ID3D12GraphicsCommandList8 is answered" in on_fn)
check("List8 is answered only behind the key, the old IIDs without it",
      "(IsEqualGUID(riid, &IID_ID3D12GraphicsCommandList8) && mad_list8_on())" in qi
      and qi.count("IsEqualGUID(riid, &IID_ID3D12GraphicsCommandList") == 8)
check("the List8 slot follows the List7 table in one struct; g_list_vtbl still names the List7 part",
      "ID3D12GraphicsCommandList7Vtbl v7;\n    void (STDMETHODCALLTYPE *OMSetFrontAndBackStencilRef)(ID3D12GraphicsCommandList *This, UINT front, UINT back);\n} g_list_vtbl8;" in src
      and "#define g_list_vtbl (g_list_vtbl8.v7)" in src and "static ID3D12GraphicsCommandList7Vtbl g_list_vtbl;" not in src)
check("the slot is filled in build_vtables, and the method forwards the front reference",
      "g_list_vtbl8.OMSetFrontAndBackStencilRef = list_OMSetFrontAndBackStencilRef;" in src
      and "    list_OMSetStencilRef(This, front);\n}" in src)
check("the IID is Microsoft's", "DEFINE_GUID(IID_ID3D12GraphicsCommandList8, 0xee936ef9, 0x599d, 0x4d28, 0x93, 0x8e, 0x23, 0xc4, 0xad, 0x05, 0xce, 0x51);" in src)
check("this check runs in the workflow", "tests/host/check-d3d12-list8.py" in wf)

cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cxx:
    print("SKIP: no C++ compiler for the behaviour part")
else:
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <string>
typedef int32_t LONG; typedef int32_t HRESULT; typedef const void *REFIID_;
struct GUID { uint32_t a; };
#define REFIID const GUID *
#define STDMETHODCALLTYPE
#define S_OK 0
#define E_NOINTERFACE ((HRESULT)0x80004002)
#define ID(n) static const GUID n = { __COUNTER__ + 1 };
ID(IID_ID3D12CommandList) ID(IID_ID3D12GraphicsCommandList1) ID(IID_ID3D12GraphicsCommandList2) ID(IID_ID3D12GraphicsCommandList3)
ID(IID_ID3D12GraphicsCommandList4) ID(IID_ID3D12GraphicsCommandList5) ID(IID_ID3D12GraphicsCommandList6) ID(IID_ID3D12GraphicsCommandList7)
ID(IID_ID3D12GraphicsCommandList8) ID(IID_Unknown_Else)
static bool IsEqualGUID(REFIID a, const GUID *b) { return a->a == b->a; }
struct ID3D12GraphicsCommandList;
struct mad_obj { void *vtbl; LONG refs; };
static LONG InterlockedIncrement(volatile LONG *p) { return ++*p; }
static int g_generic_calls;
static HRESULT mad_qi(struct mad_obj *o, REFIID riid, void **out, int x) { g_generic_calls++; return E_NOINTERFACE; }
static int g_cfg_list8;
static long long mad_cfg_int_pe(const char *k, long long d) { return !strcmp(k, "d3d12-list8") ? g_cfg_list8 : d; }
static std::string g_log;
static void d3d12_log(const char *f, ...) { g_log += f; }
''' + on_fn + "\n" + qi + r'''
static int fails = 0;
static void expect(bool c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
int main(int argc, char **argv) {
  g_cfg_list8 = argc > 1 && argv[1][0] == '1';
  mad_obj o = {nullptr, 1}; void *out = nullptr;
  ID3D12GraphicsCommandList *This = (ID3D12GraphicsCommandList *)&o;
  const GUID *old_set[] = {&IID_ID3D12CommandList, &IID_ID3D12GraphicsCommandList1, &IID_ID3D12GraphicsCommandList2, &IID_ID3D12GraphicsCommandList3,
                           &IID_ID3D12GraphicsCommandList4, &IID_ID3D12GraphicsCommandList5, &IID_ID3D12GraphicsCommandList6, &IID_ID3D12GraphicsCommandList7};
  bool all_old = true; LONG refs0 = o.refs;
  for (auto *g : old_set) { out = nullptr; all_old &= list_QI(This, g, &out) == S_OK && out == &o; }
  expect(all_old && o.refs == refs0 + 8 && g_generic_calls == 0, "CommandList and List1..7 are answered with a reference each, never through the generic path");
  out = nullptr; LONG r1 = o.refs;
  HRESULT hr = list_QI(This, &IID_ID3D12GraphicsCommandList8, &out);
  if (g_cfg_list8) {
    expect(hr == S_OK && out == &o && o.refs == r1 + 1 && g_log.find("ID3D12GraphicsCommandList8 is answered") != std::string::npos, "key on: List8 is answered with a reference, logged once");
  } else {
    expect(hr == E_NOINTERFACE && out == nullptr && o.refs == r1 && g_generic_calls == 1 && g_log.empty(), "key off: List8 goes to the generic path and is refused, as before; nothing logged");
  }
  int g0 = g_generic_calls;
  hr = list_QI(This, &IID_Unknown_Else, &out);
  expect(hr == E_NOINTERFACE && g_generic_calls == g0 + 1, "an unknown IID goes to the generic path");
  hr = list_QI(This, &IID_ID3D12GraphicsCommandList7, nullptr);
  expect(hr == E_NOINTERFACE && g_generic_calls == g0 + 2, "a null out pointer goes to the generic path too (unchanged)");
  return fails;
}
'''
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "h.cpp").write_text(harness)
        flags = ["-std=c++17", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-parameter", "-Wno-c++20-designator"]
        b = subprocess.run([cxx, *flags, "-fsanitize=address,undefined", "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
        if b.returncode:
            b = subprocess.run([cxx, *flags, "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
        check("list_QI compiles on the host with fake IIDs", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            for arg, what in (("0", "key off"), ("1", "key on")):
                r = subprocess.run([str(t / "h"), arg], capture_output=True, text=True)
                print(r.stdout.rstrip())
                check(f"list_QI behaves ({what})", r.returncode == 0)

# ---- 3. the ABI of the appended slot, with the arm64ec toolchain
mingw = None
for base in (root / "toolchains", Path.home() / "madeira-bcd/toolchains"):
    p = base / "llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
    if p.is_file():
        mingw = p
        break
if mingw is None:
    print("note: no llvm-mingw next to the sources; the slot's layout is checked where the toolchain is (CI)")
else:
    decl = cut(src, "static struct {\n    ID3D12GraphicsCommandList7Vtbl v7;", "} g_list_vtbl8;") + "} g_list_vtbl8;"
    prog = ("#define COBJMACROS\n#include <initguid.h>\n#include <d3d12.h>\n#include <stddef.h>\n" + decl.replace("static struct {", "typedef struct {").replace("} g_list_vtbl8;", "} mad_list8_vtbl;") +
            "\n_Static_assert(offsetof(mad_list8_vtbl, v7) == 0, \"the List7 table is first\");\n"
            "_Static_assert(offsetof(mad_list8_vtbl, OMSetFrontAndBackStencilRef) == sizeof(ID3D12GraphicsCommandList7Vtbl), \"List8's slot follows List7's table\");\n"
            "_Static_assert(offsetof(ID3D12GraphicsCommandList7Vtbl, Barrier) + sizeof(void *) == sizeof(ID3D12GraphicsCommandList7Vtbl), \"Barrier is List7's last method\");\n"
            "int main(void) { return 0; }\n")
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "abi.c").write_text(prog)
        r = subprocess.run([str(mingw), "-fsyntax-only", "-Wall", str(t / "abi.c")], capture_output=True, text=True)
        check("the appended List8 slot follows List7's last method (Barrier), the List7 table is first", r.returncode == 0)
        if r.returncode:
            print(r.stderr[-2000:])

print("FAILED: ID3D12GraphicsCommandList8" if failures else "PASS: ID3D12GraphicsCommandList8")
sys.exit(1 if failures else 0)
