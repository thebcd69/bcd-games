#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_CHILD_ARGS: the program name may hold a blank (round 707, agent G1 S7; Buckshot Roulette FEYMC1); no Wine runs.

child_extra_args (build/ntdll-unix/process_ios.c) took the program name up to the first blank, so "Buckshot Roulette.exe --rendering-driver
opengl3" could never match. The first ".exe" followed by a blank now ends the name when a blank comes before it; a spec whose first ".exe"
ends at the first blank (every spec that works today) reads exactly as before. The production function is compiled on the host with a model
of ios_image_name_is (the file name of the image, ASCII case ignored).
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/process_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


sig = "static const char *child_extra_args( const char *spec, const WCHAR *image, int image_len )"
fn = src[src.index(sig):src.index("\n}\n", src.index(sig)) + 3]
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-child-args-name.py" in wf)
program = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
typedef unsigned short WCHAR;
static int ios_image_name_is( const WCHAR *image, int len, const char *name )
{   /* the image's file name (after the last backslash), ASCII case ignored */
    int start = len, i, n = (int)strlen( name );
    while (start > 0 && image[start - 1] != '\\') start--;
    if (len - start != n) return 0;
    for (i = 0; i < n; i++)
    {
        WCHAR c = image[start + i];
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != (WCHAR)name[i]) return 0;
    }
    return 1;
}
@FN@
static void w( const char *a, WCHAR *out, int *len ) { int i; for (i = 0; a[i]; i++) out[i] = (WCHAR)a[i]; *len = i; }
int main( void )
{
    static const char *cases[][2] = {
        { "Buckshot Roulette.exe --rendering-driver opengl3", "C:\\Games\\Buckshot Roulette\\Buckshot Roulette.exe" },
        { "GTA5_Enhanced.exe -scDebugLogging", "C:\\Steam\\GTA5_Enhanced.exe" },
        { "Battle.net.exe --in-process-gpu --renderer-process-limit=2", "C:\\Program Files (x86)\\Battle.net\\Battle.net.exe" },
        { "FindTheNeedle.exe --rendering-driver d3d12", "C:\\g\\FindTheNeedle.exe" },
        { "Buckshot Roulette.exe --rendering-driver opengl3", "C:\\g\\Roulette.exe" },
        { "GTA5_Enhanced.exe -scDebugLogging", "C:\\g\\PlayGTAV.exe" },
        { "Buckshot Roulette.exe", "C:\\g\\Buckshot Roulette.exe" },
        { "  buckshot roulette.EXE   --x", "C:\\g\\BUCKSHOT ROULETTE.exe" },
        /* shorter than ".exe ": read only up to their end (AddressSanitizer, the build-709 check-sc-cef-gate finding) */
        { "", "C:\\g\\a.exe" },
        { "   ", "C:\\g\\a.exe" },
        { "a", "C:\\g\\a" },
        { "abc", "C:\\g\\abc" },
        { ".exe", "C:\\g\\.exe" },
        { "a.ex", "C:\\g\\a.ex" },
    };
    unsigned i;
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        WCHAR img[256]; int len; const char *r;
        char *spec = strdup( cases[i][0] );   /* exactly its own size on the heap, so a read past its end is caught */
        w( cases[i][1], img, &len );
        r = child_extra_args( spec, img, len );
        printf( "%u:%s\n", i, r ? r : "(none)" );
        free( spec );
    }
    return 0;
}
'''
tmp = Path(tempfile.mkdtemp(prefix="madeira-child-args-"))
(tmp / "m.c").write_text(program.replace("@FN@", fn))
b = subprocess.run(["cc", "-std=gnu11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-Wall", "-Werror",
                    "-o", str(tmp / "m"), str(tmp / "m.c")], capture_output=True, text=True)
check("the production function compiles on the host (AddressSanitizer)", b.returncode == 0, b.stderr)
if b.returncode == 0:
    run = subprocess.run([str(tmp / "m")], capture_output=True, text=True)
    check("no read past a spec's end, no undefined behaviour (AddressSanitizer, UBSan)", run.returncode == 0, run.stderr[-3000:])
    out = dict(l.split(":", 1) for l in run.stdout.splitlines())
    check("a name with a blank: Buckshot Roulette.exe gets its arguments", out["0"] == "--rendering-driver opengl3", out)
    check("the specs that work today read as before (GTA V Enhanced, Battle.net, Find The Needle)",
          out["1"] == "-scDebugLogging" and out["2"] == "--in-process-gpu --renderer-process-limit=2" and out["3"] == "--rendering-driver d3d12", out)
    check("another program gets nothing (the blank name's tail, PlayGTAV.exe)", out["4"] == "(none)" and out["5"] == "(none)", out)
    check("a spec with no arguments appends nothing", out["6"] == "(none)", out)
    check("leading blanks, case and extra blanks", out["7"] == "--x", out)
    check("a spec shorter than \".exe \" matches nothing", all(out.get(str(k)) == "(none)" for k in range(8, 14)), out)
print("PASS: MADEIRA_CHILD_ARGS names" if ok else "FAIL: MADEIRA_CHILD_ARGS names")
sys.exit(0 if ok else 1)
