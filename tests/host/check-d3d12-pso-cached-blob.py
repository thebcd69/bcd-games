#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Opt-in ID3D12PipelineState::GetCachedBlob of the D3D12 runtime; no device.

madeira-d3d12/src/pe/madeira_d3d12.c, block "ID3D12PipelineState::GetCachedBlob": with bcd.cfg / game file
d3d12-pso-cached-blob = 1 the call answers a 16-byte blob (a magic and reserved zeros) instead of the generated
stub's E_NOTIMPL. Resident Evil Requiem's demo (Steam 4459100, re9demo.exe; Send Logs E952YC, SAG071, 0FEAMR,
PTSY5G, build 592, iPhone18,3) creates its first two pipelines, calls GetCachedBlob ("[madeira-d3d12] unimplemented:
ID3D12PipelineState::GetCachedBlob") and in the same instant calls an unimplemented comctl32 ordinal (345: its
fatal-error dialog), ending with 0x80000100. The runtime answers D3D12_FEATURE_SHADER_CACHE with
D3D12_SHADER_CACHE_SUPPORT_SINGLE_PSO, which promises the call. 37 other D3D12 sessions of the logs (GTA V Enhanced,
RDR2, Ghost of Tsushima, Horizon Zero Dawn, Spider-Man: Miles Morales...) never make it, so the default stays the stub.

  * checks statically that the key defaults to 0 and is logged once, only when on; that the call falls back to the
    generated stub (the same arguments) when the key is off, before anything else; that a NULL out pointer is
    E_POINTER; that the out pointer is cleared before the blob is made; that the vtable slot points at the new
    function; that creating a pipeline never reads CachedPSO (the blob handed back is harmless); that the runtime
    still reports the single-pipeline shader cache (the promise the call keeps); that the settings catalog lists the
    key off by default; that the Requiem lists and auto-tune's rule carry it and CI runs this check;
  * cuts the function and its switch (between "psoblob-test:begin" and "psoblob-test:end" for the blob, and the two
    functions by name), compiles them on the host against stand-ins and runs them: switch unset, 0, empty, 2, "yes":
    the stub is called with the same arguments, the log says nothing and the out pointer is untouched; switch 1: the
    blob is 16 bytes, "MADPSOC1" and eight zeros, the log says so once, a NULL out pointer is E_POINTER and the stub
    is not called;
  * compiles the whole runtime for syntax with the repo's llvm-mingw when its toolchain is present (the full tree),
    else says so.

The runtime part is skipped when no host C compiler is found; the static checks still run.
"""
import json, pathlib, re, shutil, subprocess, sys, tempfile

R = pathlib.Path(__file__).resolve().parents[2]
SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


def cut(sig):
    i = SRC.index(sig)
    j = SRC.index("{", i)
    depth = 0
    for k in range(j, len(SRC)):
        if SRC[k] == "{":
            depth += 1
        elif SRC[k] == "}":
            depth -= 1
            if depth == 0:
                return SRC[i:k + 1]
    raise SystemExit("unbalanced " + sig)


# --- static checks ---------------------------------------------------------
on = cut("static int mad_psoblob_on(void)")
check("d3d12-pso-cached-blob defaults to 0 and only a non-zero number turns it on",
      'v = mad_cfg_int_pe("d3d12-pso-cached-blob", 0) ? 1 : 0;' in on)
check("logged once, only when on", re.search(r'if \(v\) d3d12_log\("\[madeira-d3d12\] d3d12-pso-cached-blob = 1 \(opt-in\)', on) is not None
      and on.count("d3d12_log(") == 1)
fn = cut("static HRESULT STDMETHODCALLTYPE pso_GetCachedBlob(ID3D12PipelineState *This, ID3DBlob **blob)")
body = [l.strip() for l in fn[fn.index("{") + 1:].splitlines() if l.strip()]
decl_end = next(i for i, l in enumerate(body) if l.startswith("if (!mad_psoblob_on())"))
check("the generated stub, with the same arguments, when the key is off (first statement after the buffer)",
      body[decl_end] == "if (!mad_psoblob_on()) return stub_ID3D12PipelineState_GetCachedBlob(This, blob);" and decl_end <= 1)
check("the stub stays where it is (generated header)", SRC.count("stub_ID3D12PipelineState_GetCachedBlob(") == 1)
check("a NULL out pointer is E_POINTER, before anything is written", "if (!blob) return E_POINTER;" in fn
      and fn.index("if (!blob) return E_POINTER;") < fn.index("*blob = NULL;") < fn.index("mad_make_blob("))
check("the answer is the 16-byte blob", "mad_make_blob(data, MAD_PSOBLOB_SIZE, (ID3D10Blob **)blob)" in fn
      and "#define MAD_PSOBLOB_SIZE 16" in SRC and '#define MAD_PSOBLOB_MAGIC "MADPSOC1"' in SRC)
check("the vtable slot points at the new function (the stub when off)",
      "g_pso_vtbl.GetCachedBlob = pso_GetCachedBlob;" in SRC)
check("creating a pipeline never reads CachedPSO (a blob handed back is harmless): it is stored, nothing else",
      len(re.findall(r"[.>]CachedPSO\b", SRC)) == 2 and "g.CachedPSO = c.CachedPSO = *(const D3D12_CACHED_PIPELINE_STATE *)pl;" in SRC)
check("the runtime keeps reporting the single-pipeline shader cache, the promise this call keeps",
      "c->SupportFlags = D3D12_SHADER_CACHE_SUPPORT_SINGLE_PSO;" in SRC)
catalog = (R / "build/tools/gen-config-catalog.py").read_text()
check("the settings catalog lists d3d12-pso-cached-blob, off by default",
      re.search(r'"d3d12-pso-cached-blob": \{"category": "Direct3D 12"[^}]*"kind": "bool", "default": "0"', catalog) is not None)
swift = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the generated catalog carries the key (regenerate it in the full tree; the line is the generator's)",
      re.search(r'ConfigOption\(key: "d3d12-pso-cached-blob", [^\n]*kind: \.bool, defaultValue: "0"', swift) is not None)
lists = json.loads((R / "recommended/recommendations.json").read_text())
carriers = sorted(r["id"] for r in lists["rules"] if any((c["line"] if isinstance(c, dict) else c) == "d3d12-pso-cached-blob = 1" for c in r["config"]))
# List v42 (build 644): The Last of Us Part I v8 sets it from build 643 (proven by hand on 2026-11-08 12:17, build 643: its
# "HALT: Failed to GetCachedBlob" is gone and the game reaches the loading screen).
_re = ["resident-evil-requiem", "resident-evil-requiem-demo"]
check("only the two Resident Evil Requiem lists (minBuild 598) and The Last of Us Part I (minBuild 643) set it",
      carriers == _re + ["the-last-of-us-part-1"]
      and all({"line": "d3d12-pso-cached-blob = 1", "minBuild": 598} in r["config"] for r in lists["rules"] if r["id"] in _re)
      and all({"line": "d3d12-pso-cached-blob = 1", "minBuild": 643} in r["config"] for r in lists["rules"] if r["id"] == "the-last-of-us-part-1"))
rules = (R / "build/tools/autotune_rules.py").read_text()
check("auto-tune's rule adds the line after the GetCachedBlob stub line and the comctl32 stub line, never twice",
      '"id": "d3d12-pso-cached-blob-fatal"' in rules and '"lines": ["d3d12-pso-cached-blob = 1"]' in rules
      and 's("[madeira-d3d12] unimplemented: ID3D12PipelineState::GetCachedBlob")' in rules
      and 's("to unimplemented function COMCTL32.dll.345")' in rules
      and '"none": [s("[madeira-d3d12] d3d12-pso-cached-blob = 1")]' in rules)
check("the fixture is there and expects the line", (R / "tests/fixtures/autotune/resident-evil-requiem-demo-getcachedblob-2026-10-23.log").is_file()
      and json.loads((R / "tests/fixtures/autotune/resident-evil-requiem-demo-getcachedblob-2026-10-23.expect").read_text())["next"] == ["d3d12-pso-cached-blob = 1"])
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-d3d12-pso-cached-blob.py" in workflow)

# --- the function on the host ----------------------------------------------
cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
b = SRC.index("/* psoblob-test:begin */")
e = SRC.index("/* psoblob-test:end */")
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef long HRESULT; typedef size_t SIZE_T;
#define S_OK ((HRESULT)0)
#define E_NOTIMPL ((HRESULT)0x80004001L)
#define E_POINTER ((HRESULT)0x80004003L)
#define STDMETHODCALLTYPE
typedef struct ID3D10Blob { unsigned char *data; SIZE_T size; } ID3D10Blob;
typedef ID3D10Blob ID3DBlob;
typedef struct ID3D12PipelineState { int unused; } ID3D12PipelineState;
#define MAD_PSOBLOB_MAGIC "MADPSOC1"
#define MAD_PSOBLOB_SIZE 16

static int g_stub_calls, g_log_calls, g_cfg_reads;
static ID3D12PipelineState *g_stub_this; static ID3DBlob **g_stub_blob;
static HRESULT stub_ID3D12PipelineState_GetCachedBlob(ID3D12PipelineState *This, ID3DBlob **blob) {
    g_stub_calls++; g_stub_this = This; g_stub_blob = blob; return E_NOTIMPL;
}
static long long mad_cfg_int_pe(const char *key, long long dflt) {
    const char *v = getenv("PSOBLOB_KEY");
    g_cfg_reads++;
    if (strcmp(key, "d3d12-pso-cached-blob")) { puts("FAIL: another key was read"); exit(1); }
    return v ? atoll(v) : dflt;
}
static void d3d12_log(const char *fmt, ...) { (void)fmt; g_log_calls++; }
static HRESULT mad_make_blob(const void *bytes, SIZE_T n, ID3D10Blob **out) {
    ID3D10Blob *b = calloc(1, sizeof *b);
    b->data = malloc(n); memcpy(b->data, bytes, n); b->size = n; *out = b; return S_OK;
}
''' + SRC[b:e] + r'''''' + cut("static int mad_psoblob_on(void)") + "\n" + cut("static HRESULT STDMETHODCALLTYPE pso_GetCachedBlob(ID3D12PipelineState *This, ID3DBlob **blob)") + r'''

#define T(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void) {
    const char *k = getenv("PSOBLOB_KEY");
    int on = k && atoll(k) != 0;
    ID3D12PipelineState pso; ID3DBlob *blob = (ID3DBlob *)0x1234; HRESULT hr;
    /* the blob helper */
    { unsigned char buf[MAD_PSOBLOB_SIZE + 1]; memset(buf, 0xAA, sizeof buf); mad_psoblob_fill(buf);
      T(!memcmp(buf, "MADPSOC1", 8)); { int i; for (i = 8; i < 16; i++) T(buf[i] == 0); } T(buf[16] == 0xAA); }
    if (!on) {
        hr = pso_GetCachedBlob(&pso, &blob);
        T(hr == E_NOTIMPL); T(g_stub_calls == 1); T(g_stub_this == &pso); T(g_stub_blob == &blob);
        T(blob == (ID3DBlob *)0x1234);                 /* untouched, as before */
        T(g_log_calls == 0);
        hr = pso_GetCachedBlob(&pso, NULL);            /* the stub's own answer, with the same arguments */
        T(hr == E_NOTIMPL); T(g_stub_calls == 2); T(g_stub_blob == NULL);
        T(g_cfg_reads == 1);                            /* read once */
        puts("off: the stub, same arguments, no log, out pointer untouched");
    } else {
        hr = pso_GetCachedBlob(&pso, NULL);
        T(hr == E_POINTER); T(g_stub_calls == 0);
        hr = pso_GetCachedBlob(&pso, &blob);
        T(hr == S_OK); T(g_stub_calls == 0); T(blob && blob != (ID3DBlob *)0x1234);
        T(blob->size == 16); T(!memcmp(blob->data, "MADPSOC1", 8)); { int i; for (i = 8; i < 16; i++) T(blob->data[i] == 0); }
        hr = pso_GetCachedBlob(&pso, &blob);
        T(hr == S_OK);
        T(g_log_calls == 1);                            /* once */
        T(g_cfg_reads == 1);
        puts("on: a 16-byte MADPSOC1 blob, E_POINTER for NULL, the stub never called, logged once");
    }
    return 0;
}
'''
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = pathlib.Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = pathlib.Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the function and its switch compile on the host", r.returncode == 0)
        if r.returncode:
            print(r.stderr[-3000:])
        else:
            for value, label in ((None, "unset"), ("0", "0"), ("", "empty"), ("yes", "yes"), ("1", "1"), ("2", "2")):
                import os
                env = {k: v for k, v in os.environ.items() if k != "PSOBLOB_KEY"}
                if value is not None:
                    env["PSOBLOB_KEY"] = value
                run = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
                print("     [key %s] %s" % (label, run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr.strip()))
                check("switch %s: %s" % (label, "answers the blob" if value in ("1", "2") else "the stub, as before"), run.returncode == 0)
else:
    print("note: no host C compiler; the function was not run")

# --- the whole runtime, for syntax, with the repo's toolchain -----------------
mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if mingw.exists() and winemetal.exists():
    r = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="0000000000000000"',
                        "-I" + str(R / "madeira-d3d12/src/pe"), "-I" + str(R / "madeira-d3d12/src"), "-I" + str(winemetal),
                        str(R / "madeira-d3d12/src/pe/madeira_d3d12.c")], capture_output=True, text=True)
    check("madeira_d3d12.c compiles (arm64ec, -fsyntax-only)", r.returncode == 0)
    if r.returncode:
        print(r.stderr[-3000:])
else:
    print("note: llvm-mingw / dxmt not in this tree; the syntax compile runs in the full tree")

sys.exit(0 if ok else 1)
