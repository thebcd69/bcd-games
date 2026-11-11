#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-d3dx-hresult.py: Wine's D3DX10/D3DX11 run-time compile functions write the caller's *pHResult (night round 2026-11-11, G2 S1).

BioShock (Send Logs 01NGQE, FDVGYM): the Renderer thread compiles shaders at run time, then reads a NULL object. Wine's D3DX10CompileFromMemory,
D3DX11CompileFromMemory and D3DX10CreateEffectFromMemory / FromFileW / FromResourceA / FromResourceW returned the result but never wrote the
documented `HRESULT *pHResult` (the tree's texture loaders do). Checks:
  - CI applies the patch before the i386 farm is built and runs this check;
  - with a Wine tree (wine/ of this tree, WINE_SRC or ~/madeira-bcd/wine; skipped without one): the patch applies once to copies of the three
    files, a second run changes nothing, a moved anchor refuses it and leaves every file alone;
  - the real functions, extracted from the files and compiled on the host with stubs for what they call: unpatched they leave a caller's
    variable alone; patched they write the result of the compile or the effect creation (success and failure), write nothing for a NULL
    pointer, and, like the texture loaders, nothing when an argument or a resource load fails before the compile;
  - with the llvm-mingw toolchain and the full tree's i386 build (the Mac): the three patched files compile for the i386 farm with the build's
    own flags, with no error and no warning.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("d3dxhr", root / "tools/patch-wine-d3dx-hresult.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch wine d3dx10/d3dx11 compile functions to write pHResult\n")
check("CI runs this check and applies the patch before the i386 farm is built",
      "python3 tests/host/check-wine-d3dx-hresult.py\n          python3 tools/patch-wine-d3dx-hresult.py wine\n" in wf[step:step + 500]
      and step < wf.index("      - name: Build i386 farm (WoW64)\n"))

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and all((c / rel).is_file() for rel, _ in P.EDITS)), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine); the CI and the Mac run the source part")
    print("PASS: the d3dx compile functions write pHResult" if ok else "FAILED: d3dx hresult")
    sys.exit(0 if ok else 1)

orig = {rel: (wine / rel).read_text() for rel, _ in P.EDITS}
if any(P.MARKER in t for t in orig.values()):
    print("note: the Wine tree is already patched; only the patched files are checked")
    patched_src = orig
    orig = None
with tempfile.TemporaryDirectory(prefix="madeira-d3dx-hresult-") as tmp:
    tmp = Path(tmp)
    script = str(root / "tools/patch-wine-d3dx-hresult.py")
    if orig is not None:
        tree = tmp / "w"
        for rel, text in orig.items():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_text(text)
        r1 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        patched_src = {rel: (tree / rel).read_text() for rel in orig}
        r2 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        check("it applies once to the three files; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout
              and all((tree / rel).read_text() == patched_src[rel] and P.MARKER in patched_src[rel] for rel in orig),
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        check("six writes of the result (CompileFromMemory x2, CreateEffectFromMemory, FromFileW, FromResourceA, FromResourceW)",
              sum(t.count("*hresult = hr;") for t in patched_src.values()) - sum(t.count("*hresult = hr;") for t in orig.values()) == 6,
              [t.count("*hresult = hr;") for t in patched_src.values()])
        # a moved anchor in the last file must leave the first two alone too
        before = {rel: (tree / rel).read_text() for rel in orig}
        for rel in orig:
            (tree / rel).write_text(orig[rel])
        bad = tree / "dlls/d3dx10_43/compiler.c"
        bad.write_text(orig["dlls/d3dx10_43/compiler.c"].replace("create_effect(data, datasize,", "create_effect( data, datasize,", 1))
        r3 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and leaves all three files alone",
              r3.returncode != 0 and "d3dx10_43/compiler.c" in r3.stderr
              and all(P.MARKER not in (tree / rel).read_text() for rel in orig), r3.stderr)

    cc = shutil.which("cc") or shutil.which("clang")
    if not cc:
        print("skip: no C compiler here")
    else:
        def fn(text, name):
            i = text.index("HRESULT WINAPI %s(" % name)
            return text[i:text.index("\n}\n", i) + 3]

        prelude = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int32_t HRESULT; typedef uint32_t UINT, DWORD; typedef size_t SIZE_T; typedef uint16_t WCHAR; typedef void *HMODULE;
typedef void D3D10_SHADER_MACRO; typedef void ID3D10Include; typedef void ID3DInclude; typedef void ID3D10Blob; typedef void ID3D10Device;
typedef void ID3D10EffectPool; typedef void ID3D10Effect; typedef void ID3DX10ThreadPump; typedef void ID3DX11ThreadPump;
#define WINAPI
#define MAX_PATH 260
#define CP_ACP 0
#define S_OK 0
#define E_FAIL ((HRESULT)0x80004005)
#define E_OUTOFMEMORY ((HRESULT)0x8007000e)
#define SUCCEEDED(x) ((HRESULT)(x) >= 0)
#define FAILED(x) ((HRESULT)(x) < 0)
#define TRACE(...) do { } while (0)
#define FIXME(...) do { } while (0)
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define D3D_COMPILE_STANDARD_FILE_INCLUDE ((void *)1)
static HRESULT g_compile, g_load;
static HRESULT D3DCompile(const void *d, SIZE_T n, const char *f, const void *m, void *i, const char *e, const char *t, UINT a, UINT b, void **s, void **er)
{ return g_compile; }
static HRESULT create_effect(const void *d, SIZE_T n, const char *f, const void *m, void *i, const char *p, UINT a, UINT b, void *dev, void *pool,
                             void **eff, void **er) { return g_compile; }
static HRESULT load_file(const WCHAR *f, void **buf, DWORD *size) { if (FAILED(g_load)) return g_load; *buf = malloc(4); *size = 4; return S_OK; }
static HRESULT d3dx_load_resource_a(HMODULE m, const char *r, void **data, uint32_t *size) { static char x[4]; *data = x; *size = 4; return g_load; }
static HRESULT d3dx_load_resource_w(HMODULE m, const WCHAR *r, void **data, uint32_t *size) { static char x[4]; *data = x; *size = 4; return g_load; }
static int WideCharToMultiByte(int cp, DWORD fl, const WCHAR *w, int wl, char *mb, int ml, const char *d, int *u)
{ if (mb && ml > 0) mb[0] = 0; return 1; }
static int MultiByteToWideChar(int cp, DWORD fl, const char *mb, int ml, WCHAR *w, int wl) { if (w && wl > 0) w[0] = 0; return 1; }
'''
        main = r'''
#define SENT ((HRESULT)0x12345678)
static int run_all(void)
{
    char out[16384]; (void)out;
    const struct { HRESULT compile, load; } cases[] = { {S_OK, S_OK}, {E_FAIL, S_OK}, {S_OK, E_FAIL} };
    for (unsigned c = 0; c < 3; c++)
    {
        HRESULT hr[8], ret[8];
        for (int k = 0; k < 8; k++) hr[k] = SENT;
        g_compile = cases[c].compile; g_load = cases[c].load;
        ret[0] = %(f0)s(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, &hr[0]);
        ret[1] = %(f1)s(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, &hr[1]);
        ret[2] = %(f2)s(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, &hr[2]);
        ret[3] = %(f3)s((const WCHAR *)L"a", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, &hr[3]);
        ret[4] = %(f4)s(0, "res", "f", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, &hr[4]);
        ret[5] = %(f5)s(0, (const WCHAR *)"r\0", (const WCHAR *)"f\0", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, &hr[5]);
        printf("case %%u compile=%%08x load=%%08x:", c, (unsigned)cases[c].compile, (unsigned)cases[c].load);
        for (int k = 0; k < 6; k++) printf(" %%08x/%%08x", (unsigned)ret[k], (unsigned)hr[k]);
        printf("\n");
        /* NULL pHResult must not crash */
        %(f0)s(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0); %(f1)s(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        %(f2)s(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0); %(f3)s((const WCHAR *)L"a", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        %(f4)s(0, "res", "f", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0); %(f5)s(0, (const WCHAR *)"r\0", (const WCHAR *)"f\0", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    }
    return 0;
}
int main(void) { return run_all(); }
'''
        # the real texts (renamed apart: both CompileFromMemory functions keep the same shape)
        def build(srcs, sent_note):
            parts = []
            names = {}
            for key, (rel, name) in {"f0": ("dlls/d3dx10_43/async.c", "D3DX10CompileFromMemory"),
                                      "f1": ("dlls/d3dx11_43/async.c", "D3DX11CompileFromMemory"),
                                      "f2": ("dlls/d3dx10_43/compiler.c", "D3DX10CreateEffectFromMemory"),
                                      "f3": ("dlls/d3dx10_43/compiler.c", "D3DX10CreateEffectFromFileW"),
                                      "f4": ("dlls/d3dx10_43/compiler.c", "D3DX10CreateEffectFromResourceA"),
                                      "f5": ("dlls/d3dx10_43/compiler.c", "D3DX10CreateEffectFromResourceW")}.items():
                parts.append(fn(srcs[rel], name))
                names[key] = name
            return prelude + "\n".join(parts) + main % names

        outputs = {}
        for which, srcs in (("patched", patched_src), ("orig", orig)):
            if srcs is None:
                continue
            (tmp / (which + ".c")).write_text(build(srcs, which))
            r = subprocess.run([cc, "-O1", "-w", "-o", str(tmp / which), str(tmp / (which + ".c"))], capture_output=True, text=True)
            check("the %s functions compile on the host with the stubs" % which, r.returncode == 0, r.stderr)
            if r.returncode == 0:
                outputs[which] = subprocess.run([str(tmp / which)], capture_output=True, text=True)
        if "patched" in outputs:
            lines = outputs["patched"].stdout.splitlines()
            check("patched: no crash, also with a NULL pHResult", outputs["patched"].returncode == 0 and len(lines) == 3, outputs["patched"].stderr)
            S, F = "00000000", "80004005"

            def parse(line):
                return [tuple(x.split("/")) for x in line.split(":", 1)[1].split()]
            if len(lines) == 3:
                ok_case, fail_case, load_case = (parse(l) for l in lines)
                check("patched: a good compile or effect creation writes S_OK (all six functions)", all(r == (S, S) for r in ok_case), ok_case)
                check("patched: a failed compile or effect creation writes the failure (all six functions)", all(r == (F, F) for r in fail_case), fail_case)
                check("patched: a failed file or resource load returns its error and writes nothing (as the texture loaders do)",
                      all(r[1] == "12345678" for r in load_case[3:6]) and all(r[1] == S for r in load_case[0:3]), load_case)
        if "orig" in outputs:
            lines = outputs["orig"].stdout.splitlines()
            check("unpatched: the caller's variable is never written (this check fails without the patch)",
                  len(lines) == 3 and all(x.endswith("/12345678") for l in lines for x in l.split(":", 1)[1].split()), lines)

        # the real i386 PE compile with the build's own flags (the Mac: llvm-mingw + the full tree's build-i386 headers)
        mingw = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
        build_dir = Path.home() / "madeira-bcd/wine/build-i386"
        if not (mingw / "i686-w64-mingw32-gcc").is_file() or not (build_dir / "include").is_dir():
            print("skip: the PE compile needs llvm-mingw and the full tree's wine/build-i386 (the Mac)")
        else:
            # compile against the pinned tree's headers with the three patched files in front
            for rel, mod, extra in (("dlls/d3dx10_43/async.c", "dlls/d3dx10_43", []), ("dlls/d3dx10_43/compiler.c", "dlls/d3dx10_43", []),
                                    ("dlls/d3dx11_43/async.c", "dlls/d3dx11_43", ["-DD3DX11_SDK_VERSION=43"])):
                f = tmp / ("pe_" + rel.replace("/", "_"))
                f.write_text(patched_src[rel])
                env = dict(os.environ, PATH=str(mingw) + os.pathsep + os.environ["PATH"])
                cmd = ["i686-w64-mingw32-gcc", "-D__STDC__", "-c", "-o", str(tmp / (f.name + ".o")), str(f),
                       "-I" + str(build_dir / mod), "-I" + str(wine / mod), "-I" + str(wine / "dlls/d3dx9_36"), "-I" + str(wine / "dlls/d3dx10_43"),
                       "-I" + str(build_dir / "include"), "-I" + str(wine / "include"), "-I" + str(wine / "include/msvcrt"),
                       "-D_UCRT", "-D__WINESRC__", "-D__WINE_PE_BUILD", "-Wall", "-target", "i686-windows", "-fuse-ld=lld", "--no-default-config",
                       "-fno-strict-aliasing", "-Wdeclaration-after-statement", "-Wempty-body", "-Wignored-qualifiers", "-Winit-self",
                       "-Wno-microsoft-enum-forward-reference", "-Wstrict-prototypes", "-Wtype-limits", "-Wunused-but-set-parameter", "-Wvla",
                       "-Wwrite-strings", "-Wpointer-arith", "-Wabsolute-value", "-ffunction-sections", "-fasync-exceptions",
                       "-ffp-exception-behavior=maytrap", "-fms-hotpatch", "-fno-omit-frame-pointer", "-mlong-double-64", "-O2"] + extra
                r = subprocess.run(cmd, capture_output=True, text=True, env=env)
                check("%s compiles for the i386 farm with the build's flags (no error, no warning)" % rel,
                      r.returncode == 0 and not r.stderr.strip(), r.stderr[-800:])

print("PASS: the d3dx compile functions write pHResult" if ok else "FAILED: d3dx hresult")
sys.exit(0 if ok else 1)
