#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-d3dcompile-diag.py: Wine's D3DCompile2 names a failed run-time HLSL compile in the log (night round 2026-11-11, G2 S1 (d)).

BioShock (Send Logs 01NGQE, FDVGYM) compiles shaders at run time and the compiler's verdict never reached the log. Checks:
  - CI applies the patch before the i386 farm is built and runs this check;
  - with a Wine tree (wine/ of this tree, WINE_SRC or ~/madeira-bcd/wine; skipped without one): the patch applies once to a copy of
    dlls/d3dcompiler_43/compiler.c, a second run changes nothing, a moved call refuses it and leaves the file alone;
  - the real helper and the real D3DCompile2, extracted from the patched file and compiled on the host with stubs: a compile without a text
    and without failure prints nothing; a failed compile prints its header, the source head and the compiler's text (24 a process at most); one
    that only warned prints the header (3 a process at most); the caller's message blob, shader blob and return value are exactly what the
    compiler gave, a caller that passed no message blob gets nothing and nothing leaks (the local blob is released);
  - with the llvm-mingw toolchain and the full tree's i386 build (the Mac): the patched file compiles for the i386 farm (d3dcompiler_33, _43
    and _47) with the build's own flags, with no error and no warning.
"""
import importlib.util
import os
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


spec = importlib.util.spec_from_file_location("d3dcdiag", root / "tools/patch-wine-d3dcompile-diag.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch wine d3dcompile with a failed-compile diagnostic\n")
check("CI runs this check and applies the patch before the i386 farm is built",
      "python3 tests/host/check-wine-d3dcompile-diag.py\n          python3 tools/patch-wine-d3dcompile-diag.py wine\n" in wf[step:step + 500]
      and step < wf.index("      - name: Build i386 farm (WoW64)\n"))

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / P.FILE).is_file()), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine); the CI and the Mac run the source part")
    print("PASS: D3DCompile2 names a failed compile" if ok else "FAILED: d3dcompile diag")
    sys.exit(0 if ok else 1)

src = (wine / P.FILE).read_text()
if P.MARKER in src:
    print("note: the Wine tree is already patched; only the patched file is checked")
    patched = src
else:
    patched = None
with tempfile.TemporaryDirectory(prefix="madeira-d3dcompile-diag-") as tmp:
    tmp = Path(tmp)
    if patched is None:
        tree = tmp / "w"
        (tree / Path(P.FILE).parent).mkdir(parents=True)
        t = tree / P.FILE
        t.write_text(src)
        script = str(root / "tools/patch-wine-d3dcompile-diag.py")
        r1 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        patched = t.read_text()
        r2 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == patched and P.MARKER in patched,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        t.write_text(src.replace(P.CALL_OLD, P.CALL_OLD.replace("    if (SUCCEEDED(hr) && shader_blob == &dummy_blob)",
                                                               "    if (SUCCEEDED(hr) && shader_blob == &dummy_blob )"), 1))
        r3 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
        check("a moved call refuses the patch and leaves the file alone",
              r3.returncode != 0 and "vkd3d_D3DCompile2VKD3D call" in r3.stderr and P.MARKER not in t.read_text(), r3.stderr)

    cc = shutil.which("cc") or shutil.which("clang")
    if not cc:
        print("skip: no C compiler here")
    else:
        helper = patched[patched.index("static void madeira_compile_diag("):]
        helper = helper[:helper.index("\n}\n") + 3]
        d2 = patched[patched.index("HRESULT WINAPI D3DCompile2("):]
        d2 = d2[:d2.index("\n}\n") + 3]
        harness = r'''
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int32_t HRESULT; typedef int32_t LONG; typedef uint32_t UINT; typedef size_t SIZE_T;
typedef struct blob { char *data; SIZE_T size; } ID3DBlob;
typedef void D3D_SHADER_MACRO; typedef void ID3DInclude;
#define WINAPI
#define D3D_COMPILER_VERSION 43
#define FAILED(x) ((HRESULT)(x) < 0)
#define SUCCEEDED(x) ((HRESULT)(x) >= 0)
#define TRACE(...) do { } while (0)
#define D3D_COMPILE_STANDARD_FILE_INCLUDE ((void *)1)
static void err_out(const char *fmt, ...)
{
    /* Wine's printf: %Iu is a SIZE_T, %#lx an HRESULT, %ld a LONG (32 bits there); the host's printf wants other spellings */
    char f[512]; char *o = f; const char *p = fmt; va_list ap;
    while (*p && o < f + sizeof(f) - 8)
    {
        if (!strncmp(p, "%Iu", 3)) { memcpy(o, "%zu", 3); o += 3; p += 3; }
        else if (!strncmp(p, "%#lx", 4)) { memcpy(o, "%#x", 3); o += 3; p += 4; }
        else if (!strncmp(p, "%ld", 3)) { memcpy(o, "%d", 2); o += 2; p += 3; }
        else *o++ = *p++;
    }
    *o = 0;
    va_start(ap, fmt); vprintf(f, ap); va_end(ap);
}
#define ERR(...) err_out(__VA_ARGS__)
#define InterlockedIncrement(p) (__sync_add_and_fetch((p), 1))
#define ID3D10Blob_GetBufferSize(b) ((b)->size)
#define ID3D10Blob_GetBufferPointer(b) ((void *)(b)->data)
static int live;
static ID3DBlob *blob_new(const char *text) { ID3DBlob *b = calloc(1, sizeof(*b)); b->data = strdup(text); b->size = strlen(text); live++; return b; }
static void blob_free(ID3DBlob *b) { free(b->data); free(b); live--; }
#define ID3D10Blob_Release(b) blob_free(b)
static const char *debugstr_a(const char *s) { static char buf[16][256]; static int n; char *o = buf[n++ & 15]; snprintf(o, 256, "\"%s\"", s ? s : "(null)"); return o; }
static const char *debugstr_an(const char *s, int n) { static char buf[16][1024]; static int k; char *o = buf[k++ & 15]; snprintf(o, 1024, "\"%.*s\"", n, s); return o; }
struct d3dcompiler_include_from_file { struct { void *lpVtbl; } ID3DInclude_iface; const char *initial_filename; };
static int d3dcompiler_include_from_file_vtbl;
static HRESULT g_hr; static const char *g_text; static ID3DBlob *g_last_shader;
static HRESULT vkd3d_D3DCompile2VKD3D(const void *data, SIZE_T n, const char *f, const D3D_SHADER_MACRO *m, ID3DInclude *i, const char *e, const char *p,
        UINT fl, UINT efl, UINT sfl, const void *sd, SIZE_T sn, ID3DBlob **shader, ID3DBlob **msgs, unsigned int ver)
{
    if (msgs) { *msgs = NULL; if (g_text) *msgs = blob_new(g_text); }
    if (!FAILED(g_hr)) *shader = g_last_shader = blob_new("DXBC");
    return g_hr;
}
@HELPER@
@D2@
static int lines(void) { return 0; }
int main(int argc, char **argv)
{
    int mode = atoi(argv[1]), i;
    ID3DBlob *shader, *msgs;
    char src[400]; memset(src, 'x', sizeof(src) - 1); src[sizeof(src) - 1] = 0;
    char longtext[2000]; memset(longtext, 'm', sizeof(longtext) - 1); longtext[sizeof(longtext) - 1] = 0;
    HRESULT r;
    switch (mode)
    {
    case 0: /* success without a text: silent */
        g_hr = 0; g_text = NULL; r = D3DCompile2(src, 20, "f", NULL, NULL, "main", "ps_3_0", 0, 0, 0, NULL, 0, &shader, &msgs);
        printf("RESULT %08x shader=%d msgs=%d live=%d\n", (unsigned)r, shader != NULL, msgs != NULL, live); break;
    case 1: /* failure with a text and a caller's blob */
        g_hr = (HRESULT)0x80004005; g_text = "<anonymous>:496:37: E5017: Aborting due to not yet implemented feature.";
        shader = (ID3DBlob *)1;
        r = D3DCompile2(src, sizeof(src) - 1, "f", NULL, NULL, "main", "ps_3_0", 0x1000, 0, 0, NULL, 0, &shader, &msgs);
        printf("RESULT %08x shader=%d msgs=%d live=%d\n", (unsigned)r, shader != NULL, msgs != NULL, live); break;
    case 2: /* failure, a caller without a message blob and without a shader blob: nothing leaks */
        g_hr = (HRESULT)0x80004005; g_text = "oops";
        r = D3DCompile2(src, 10, "f", NULL, NULL, "main", "vs_3_0", 0, 0, 0, NULL, 0, NULL, NULL);
        printf("RESULT %08x live=%d\n", (unsigned)r, live); break;
    case 3: /* success with a text, caller without a message blob: no leak, shader released when no blob was asked */
        g_hr = 0; g_text = "warning X3206: implicit truncation";
        r = D3DCompile2(src, 10, "f", NULL, NULL, "main", "vs_3_0", 0, 0, 0, NULL, 0, NULL, NULL);
        printf("RESULT %08x live=%d\n", (unsigned)r, live); break;
    case 4: /* 30 failures: only the first 24 are named */
        g_hr = (HRESULT)0x80004005; g_text = longtext;
        for (i = 0; i < 30; i++) { D3DCompile2(src, 10, "f", NULL, NULL, "main", "ps_3_0", 0, 0, 0, NULL, 0, NULL, &msgs); blob_free(msgs); }
        printf("RESULT done live=%d\n", live); break;
    case 5: /* 6 compiles that only warned: only the first 3 are named, header only */
        g_hr = 0; g_text = "warning";
        for (i = 0; i < 6; i++) { D3DCompile2(src, 10, "f", NULL, NULL, "main", "ps_3_0", 0, 0, 0, NULL, 0, &shader, &msgs); blob_free(msgs); blob_free(shader); }
        printf("RESULT done live=%d\n", live); break;
    }
    return 0;
}
'''.replace("@HELPER@", helper).replace("@D2@", d2)
        (tmp / "h.c").write_text(harness)
        r = subprocess.run([cc, "-O1", "-w", "-o", str(tmp / "h"), str(tmp / "h.c")], capture_output=True, text=True)
        check("the patched helper and D3DCompile2 compile on the host with the stubs", r.returncode == 0, r.stderr)
        if r.returncode == 0:
            def run(mode):
                p = subprocess.run([str(tmp / "h"), str(mode)], capture_output=True, text=True)
                return p.returncode, p.stdout.splitlines()
            rc, out = run(0)
            check("a compile without failure and without a text prints nothing and returns the compiler's result and blobs",
                  rc == 0 and out == ["RESULT 00000000 shader=1 msgs=0 live=1"], out)
            rc, out = run(1)
            check("a failed compile prints the header, the source head and the text; the caller's blob and the result are the compiler's",
                  rc == 0 and len(out) == 4 and out[0].startswith("d3dcompile: v43 FAILED target=\"ps_3_0\" entry=\"main\" flags=0x1000 eflags=0 src=399 bytes hr=0x80004005 msgs=71 bytes #1")
                  and out[1].startswith("d3dcompile:   src=\"" + "x" * 160 + "\"") and "E5017" in out[2]
                  and out[3] == "RESULT 80004005 shader=0 msgs=1 live=1", out)
            rc, out = run(2)
            check("a caller without blobs still gets its failure named, text included (the local blob), and nothing leaks (the local blob is released)",
                  rc == 0 and len(out) == 4 and "hr=0x80004005 msgs=4 bytes #1" in out[0] and out[1].startswith("d3dcompile:   src=")
                  and out[2] == 'd3dcompile:   "oops"' and out[3] == "RESULT 80004005 live=0", out)
            rc, out = run(3)
            check("a compile that only warned, for a caller without blobs: the header only, the compiler's blob and the local blob are released",
                  rc == 0 and len(out) == 2 and " warnings " in out[0] and out[1] == "RESULT 00000000 live=0", out)
            rc, out = run(4)
            heads = [l for l in out if l.startswith("d3dcompile: v43")]
            check("30 failed compiles: 24 are named, with the text cut to 1600 bytes in 320-byte lines (5 text lines), none leaks",
                  rc == 0 and len(heads) == 24 and len([l for l in out if l.startswith('d3dcompile:   "m')]) == 24 * 5
                  and out[-1] == "RESULT done live=0", (len(heads), out[-1:]))
            rc, out = run(5)
            check("6 compiles that only warned: 3 headers, nothing else", rc == 0 and len([l for l in out if l.startswith("d3dcompile: v43")]) == 3
                  and len(out) == 4 and out[-1] == "RESULT done live=0", out)

        mingw = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
        build_dir = Path.home() / "madeira-bcd/wine/build-i386"
        if not (mingw / "i686-w64-mingw32-gcc").is_file() or not (build_dir / "include").is_dir():
            print("skip: the PE compile needs llvm-mingw and the full tree's wine/build-i386 (the Mac)")
        else:
            f = tmp / "compiler.c"
            f.write_text(patched)
            env = dict(os.environ, PATH=str(mingw) + os.pathsep + os.environ["PATH"])
            for v in (33, 43, 47):
                cmd = ["i686-w64-mingw32-gcc", "-D__STDC__", "-c", "-o", str(tmp / ("c%d.o" % v)), str(f),
                       "-I" + str(build_dir / ("dlls/d3dcompiler_%d" % v)), "-I" + str(wine / ("dlls/d3dcompiler_%d" % v)),
                       "-I" + str(wine / "dlls/d3dcompiler_43"), "-I" + str(build_dir / "include"), "-I" + str(wine / "include"),
                       "-I" + str(wine / "include/msvcrt"), "-I" + str(wine / "libs/vkd3d/include"), "-D_UCRT", "-D__WINESRC__",
                       "-DD3D_COMPILER_VERSION=%d" % v, "-D__WINE_PE_BUILD", "-Wall", "-target", "i686-windows", "-fuse-ld=lld",
                       "--no-default-config", "-fno-strict-aliasing", "-Wdeclaration-after-statement", "-Wempty-body", "-Wignored-qualifiers",
                       "-Winit-self", "-Wno-microsoft-enum-forward-reference", "-Wstrict-prototypes", "-Wtype-limits", "-Wunused-but-set-parameter",
                       "-Wvla", "-Wwrite-strings", "-Wpointer-arith", "-Wabsolute-value", "-ffunction-sections", "-fasync-exceptions",
                       "-ffp-exception-behavior=maytrap", "-fms-hotpatch", "-fno-omit-frame-pointer", "-mlong-double-64", "-O2"]
                r = subprocess.run(cmd, capture_output=True, text=True, env=env)
                check("d3dcompiler_%d: the patched file compiles for the i386 farm with the build's flags (no error, no warning)" % v,
                      r.returncode == 0 and not r.stderr.strip(), r.stderr[-800:])

print("PASS: D3DCompile2 names a failed compile" if ok else "FAILED: d3dcompile diag")
sys.exit(0 if ok else 1)
