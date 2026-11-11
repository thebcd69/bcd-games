#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-ntdll-cxx-diag.py: the PE ntdll's ml811 block names the type of an MSVC C++ throw (night round 2026-11-11, G8 S9; log only).

Rise of the Tomb Raider (2Q4BH0): `[exc] ml811 code=e06d7363` and nothing about the thrown type. Checks:
  - the patch applies to a copy of dlls/ntdll/signal_arm64ec.c once (the Wine directory or the file as argument), a second run changes nothing, a moved
    anchor refuses it by name and leaves the file alone; it adds only the helper and the branch, inside the ml811 block's recursion guard, after the
    0x20474343 branch;
  - the real branch, cut from the patched file and compiled on the host over a simulated 64-bit process image (ThrowInfo, catchable-type array, type_info
    names, a std::exception object, ATL and COM objects) with fault-safe reads that fail outside the image: a std:: throw names the thrown type, its
    catchable bases and what(); an ATL and a COM exception name their HRESULT; an unreadable ThrowInfo prints the `?` line and nothing else; another code,
    or three parameters, prints nothing;
  - with llvm-mingw and the full tree's build-arm64ec (the Mac) the patched file compiles for arm64ec with the ntdll build's own flags: no error, and
    the same warnings as the unpatched file (it has 17 of its own).
WHERE THIS REACHES: no CI step applies the patch (the shipped 64-bit ntdll.dll is a committed binary); see the night-round note W-WINE.md (G8-S9).
"""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("cxxdiag", root / "tools/patch-wine-ntdll-cxx-diag.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wine = None
for cand in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine"):
    if (cand / P.FILE).is_file():
        wine = cand
        break
if not wine:
    print("SKIP: no wine source (check out the submodule or set WINE_SRC)")
    print("PASS: MSVC throw diagnostic (static part)" if ok else "FAIL: MSVC throw diagnostic")
    sys.exit(0 if ok else 1)
pristine = (wine / P.FILE).read_text()
if P.MARKER in pristine:
    print("SKIP: the wine tree is already patched; the checks need the unpatched file")
    print("PASS: MSVC throw diagnostic (static part)" if ok else "FAIL: MSVC throw diagnostic")
    sys.exit(0 if ok else 1)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    f = t / "w" / P.FILE
    f.parent.mkdir(parents=True)
    f.write_text(pristine)
    script = str(root / "tools/patch-wine-ntdll-cxx-diag.py")
    p1 = subprocess.run([sys.executable, script, str(t / "w")], capture_output=True, text=True)
    patched = f.read_text()
    p2 = subprocess.run([sys.executable, script, str(f)], capture_output=True, text=True)
    check("applies once (the Wine directory or the file as argument), then says already patched",
          p1.returncode == 0 and "patched" in p1.stdout and p2.returncode == 0 and "already patched" in p2.stdout and f.read_text() == patched,
          p1.stdout + p1.stderr + p2.stdout + p2.stderr)
    check("nothing but the helper and the branch changed", patched.replace(P.HELPER, "").replace(P.BRANCH_NEW, P.BRANCH_ANCHOR) == pristine)
    blk = patched[patched.index("if (rec->ExceptionCode != 0x406d1388)"):]
    blk = blk[:blk.index("context.AMD64_Context = *orig_context;")]
    check("the branch sits inside the ml811 block's recursion guard, after the 0x20474343 branch",
          blk.index("InterlockedCompareExchange( &in_probe, 1, 0 )") < blk.index("rec->ExceptionCode == 0x20474343") < blk.index("rec->ExceptionCode == 0xe06d7363")
          < blk.index("InterlockedExchange( &in_probe, 0 );"))
    f.write_text(pristine.replace("static int ios_exc_dump_str( const char *what, ULONG_PTR addr, SIZE_T len, int wide )\n{\n",
                                  "static int ios_exc_dump_str( const char *what, ULONG_PTR addr, SIZE_T len, int  wide )\n{\n", 1))
    moved = f.read_text()
    p3 = subprocess.run([sys.executable, script, str(t / "w")], capture_output=True, text=True)
    check("a moved anchor fails by name and leaves the file alone", p3.returncode != 0 and "ios_exc_dump_str of signal_arm64ec.c" in p3.stderr
          and f.read_text() == moved, p3.stdout + p3.stderr)

    cc = shutil.which("cc")
    helper = P.HELPER
    start = patched.index("                if (rec->ExceptionCode == 0xe06d7363 && rec->NumberParameters >= 4)")
    branch = patched[start:patched.index("            }\n            InterlockedExchange( &in_probe, 0 );", start)]
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
typedef uint64_t ULONG_PTR; typedef uint32_t ULONG; typedef uint64_t SIZE_T; typedef int32_t LONG; typedef int NTSTATUS; typedef void *HANDLE;
typedef struct { ULONG ExceptionCode; ULONG NumberParameters; ULONG_PTR ExceptionInformation[15]; } EXCEPTION_RECORD;
/* the simulated process: one image at BASE, 0x2000 bytes */
#define BASE 0x140000000ull
static unsigned char mem[0x2000];
#define MEMLEN 0x1800     /* the last 0x800 bytes are "unmapped" */
static HANDLE GetCurrentProcess(void) { return (HANDLE)-1; }
static NTSTATUS NtReadVirtualMemory(HANDLE p, void *addr, void *buf, SIZE_T size, SIZE_T *got)
{
    ULONG_PTR a = (ULONG_PTR)addr;
    *got = 0;
    if (a < BASE || a >= BASE + MEMLEN) return 0xc0000005;
    if (a + size > BASE + MEMLEN) { *got = BASE + MEMLEN - a; memcpy(buf, mem + (a - BASE), *got); return 0x8000000d; }
    memcpy(buf, mem + (a - BASE), size);
    *got = size;
    return 0;
}
static void err_out(const char *fmt, ...)
{
    char f[512]; char *o = f; const char *p = fmt; va_list ap;
    while (*p && o < f + sizeof(f) - 8)
    {
        if (!strncmp(p, "%p", 2)) { memcpy(o, "0x%llx", 6); o += 6; p += 2; }   /* args are cast below */
        else *o++ = *p++;
    }
    *o = 0;
    va_start(ap, fmt); vprintf(f, ap); va_end(ap);
}
#define ERR(...) err_out(__VA_ARGS__)
@HELPER@
static void put32(unsigned off, uint32_t v) { memcpy(mem + off, &v, 4); }
static void put64(unsigned off, uint64_t v) { memcpy(mem + off, &v, 8); }
static void puts_at(unsigned off, const char *s) { strcpy((char *)mem + off, s); }
/* layout: ThrowInfo at 0x100 (RVA 0x100); CTA at 0x140: count, refs; CatchableType at 0x180 + 0x20*k; type_info at 0x300 + 0x80*k (name at +16);
 * objects at 0x600 (std::exception: vtable, what ptr), what text at 0x700; ATL object 0x640 {hr}; COM object 0x660 {vtable, hr} */
static void build(const char **names, int n)
{
    int k;
    memset(mem, 0, sizeof(mem));
    put32(0x100, 0); put32(0x104, 0); put32(0x108, 0); put32(0x10c, 0x140);
    put32(0x140, n);
    for (k = 0; k < n; k++)
    {
        put32(0x144 + 4 * k, 0x180 + 0x20 * k);
        put32(0x180 + 0x20 * k, 0); put32(0x184 + 0x20 * k, 0x300 + 0x80 * k);
        puts_at(0x300 + 0x80 * k + 16, names[k]);
    }
}
static void run(unsigned code, unsigned nparams, ULONG_PTR obj, ULONG_PTR tinfo, ULONG_PTR base)
{
    EXCEPTION_RECORD r, *rec = &r;
    memset(&r, 0, sizeof(r));
    r.ExceptionCode = code; r.NumberParameters = nparams;
    r.ExceptionInformation[0] = 0x19930520; r.ExceptionInformation[1] = obj; r.ExceptionInformation[2] = tinfo; r.ExceptionInformation[3] = base;
    {
@BRANCH@
    }
}
int main(int argc, char **argv)
{
    int mode = atoi(argv[1]);
    static const char *std_names[] = { ".?AVruntime_error@std@@", ".?AVexception@std@@" };
    static const char *atl_names[] = { ".?AVCAtlException@ATL@@" };
    static const char *com_names[] = { ".?AV_com_error@@" };
    switch (mode)
    {
    case 0:
        build(std_names, 2); put64(0x600, 0); put64(0x608, BASE + 0x700); puts_at(0x700, "out of memory");
        run(0xe06d7363, 4, BASE + 0x600, BASE + 0x100, BASE); break;
    case 1:
        build(atl_names, 1); put32(0x640, 0x8007000e);
        run(0xe06d7363, 4, BASE + 0x640, BASE + 0x100, BASE); break;
    case 2:
        build(com_names, 1); put64(0x660, 0); put32(0x668, 0x80004005);
        run(0xe06d7363, 4, BASE + 0x660, BASE + 0x100, BASE); break;
    case 3: /* ThrowInfo outside the image */
        build(std_names, 2);
        run(0xe06d7363, 4, BASE + 0x600, BASE + 0x5000, BASE); break;
    case 4: build(std_names, 2); run(0xc0000005, 4, BASE + 0x600, BASE + 0x100, BASE); break;
    case 5: build(std_names, 2); run(0xe06d7363, 3, BASE + 0x600, BASE + 0x100, BASE); break;
    case 6: /* what() text starts 16 bytes before the end of the readable memory: the 80-byte read is partial, the 16-byte read works */
        build(std_names, 2); put64(0x600, 0); put64(0x608, BASE + MEMLEN - 16); memcpy(mem + MEMLEN - 16, "short what text!", 16);
        run(0xe06d7363, 4, BASE + 0x600, BASE + 0x100, BASE); break;
    case 7: /* more than 8 catchable types: only 8 are read, the list is cut */
        { static const char *many[10] = { ".?AVa@@", ".?AVb@@", ".?AVc@@", ".?AVd@@", ".?AVe@@", ".?AVf@@", ".?AVg@@", ".?AVh@@", ".?AVi@@", ".?AVj@@" };
          build(many, 10); run(0xe06d7363, 4, BASE + 0x600, BASE + 0x100, BASE); }
        break;
    }
    return 0;
}
'''.replace("@HELPER@", helper).replace("@BRANCH@", branch)
    # ERR's %p arguments are pointers: print them as 64-bit values
    harness = harness.replace('(void *)tinfo, (unsigned)attrs, (void *)obj', '(unsigned long long)tinfo, (unsigned)attrs, (unsigned long long)obj')
    (t / "h.c").write_text(harness)
    b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-format", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the helper and the branch compile on the host over a simulated process image", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        def run(mode):
            return subprocess.run([str(t / "h"), str(mode)], capture_output=True, text=True).stdout.splitlines()
        o = run(0)
        check("a std:: throw: the thrown type, the catchable bases and what()",
              o == ["[exc] ml811   MSVC C++ throw: thrown .?AVruntime_error@std@@; also catchable as: .?AVexception@std@@ (2 catchable type(s), ThrowInfo 0x140000100, attributes 0, object 0x140000600)",
                    '[exc] ml811   MSVC C++ throw what="out of memory"'], o)
        o = run(1)
        check("an ATL exception names its HRESULT", len(o) == 2 and "thrown .?AVCAtlException@ATL@@;" in o[0] and o[1] == "[exc] ml811   MSVC C++ throw ATL::CAtlException hr=0x8007000e", o)
        o = run(2)
        check("a COM error names its HRESULT", len(o) == 2 and "thrown .?AV_com_error@@;" in o[0] and o[1] == "[exc] ml811   MSVC C++ throw _com_error hr=0x80004005", o)
        o = run(3)
        check("an unreadable ThrowInfo prints the `?` line and nothing else",
              len(o) == 1 and o[0].startswith("[exc] ml811   MSVC C++ throw: thrown ? (ThrowInfo or its type list not readable); also catchable as: nothing (0 catchable type(s)"), o)
        check("another code prints nothing", run(4) == [])
        check("three parameters (a 32-bit throw) print nothing", run(5) == [])
        o = run(6)
        check("a what() text at the end of the readable memory: the 16-byte read is used", len(o) == 2 and o[1] == '[exc] ml811   MSVC C++ throw what="short what text!"', o)
        o = run(7)
        check("ten catchable types: the thrown type and at most the first eight, the list cut before it overflows",
              len(o) == 1 and ".?AVa@@;" in o[0] and ".?AVb@@" in o[0] and ".?AVh@@" in o[0] and ".?AVi@@" not in o[0] and "(10 catchable type(s)" in o[0], o)

    mingw = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
    bd = Path.home() / "madeira-bcd/wine/build-arm64ec"
    if not (mingw / "arm64ec-w64-mingw32-clang").is_file() or not (bd / "include").is_dir():
        print("SKIP: the arm64ec compile needs llvm-mingw and the full tree's wine/build-arm64ec (the Mac)")
    else:
        env = dict(os.environ, PATH=str(mingw) + os.pathsep + os.environ["PATH"])

        def pe_compile(text, name):
            src = t / (name + ".c")
            src.write_text(text)
            cmd = ["arm64ec-w64-mingw32-clang", "-D__STDC__", "-c", "-o", str(t / (name + ".o")), str(src), "-I" + str(bd / "dlls/ntdll"), "-I" + str(wine / "dlls/ntdll"),
                   "-I" + str(bd / "include"), "-I" + str(wine / "include"), "-I" + str(wine / "include/msvcrt"), "-I" + str(wine / "libs/tomcrypt/src/headers"),
                   "-D_MSVCR_VER=0", "-D__WINESRC__", "-DLTC_NO_PROTOTYPES", "-DLTC_SOURCE", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", "-D__WINE_PE_BUILD",
                   "-Wall", "-target", "arm64ec-windows", "-fuse-ld=lld", "--no-default-config", "-fno-strict-aliasing", "-Wdeclaration-after-statement",
                   "-Wempty-body", "-Wignored-qualifiers", "-Winit-self", "-Wno-microsoft-enum-forward-reference", "-Wstrict-prototypes", "-Wtype-limits",
                   "-Wunused-but-set-parameter", "-Wvla", "-Wwrite-strings", "-Wpointer-arith", "-Wabsolute-value", "-ffunction-sections", "-fasync-exceptions",
                   "-ffp-exception-behavior=maytrap", "-O2"]
            return subprocess.run(cmd, capture_output=True, text=True, env=env)

        strip = lambda r: sorted(re.sub(r".*signal_arm64ec(?:_patched)?\.c:\d+:\d+: ", "", l) for l in r.stderr.splitlines() if "warning:" in l)
        r_old, r_new = pe_compile(pristine, "signal_arm64ec"), pe_compile(patched, "signal_arm64ec_patched")
        check("the patched file compiles for arm64ec with the ntdll build's flags: no error and the unpatched file's warnings only",
              r_new.returncode == 0 and "error:" not in r_new.stderr and strip(r_new) == strip(r_old), (len(strip(r_new)), len(strip(r_old)), r_new.stderr[-600:]))

print("PASS: MSVC throw diagnostic" if ok else "FAIL: MSVC throw diagnostic")
sys.exit(0 if ok else 1)
