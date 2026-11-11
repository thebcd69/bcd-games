#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-enumfonts-facename.py: win32u's NtGdiEnumFonts cuts a face name that does not fit its LOGFONTW (night round 2026-11-11, G4 S13).

FINAL FANTASY VII (2013), Steam 39140, Send Log 9J06NQ (build 634): the Qt 4 launcher passes a LOGFONTW with 32 characters and no terminator,
gdi32 hands NtGdiEnumFonts length 33 (host x25=0x21), and the memcpy of 66 bytes into the 64-byte lfFaceName aborts in the iOS SDK's fortified
memcpy ("[mach_exc] bt[1] ... NtGdiEnumFonts+0x74"; c0000026). Checks:
  - CI applies the patch before win32u-unix is built and runs this check;
  - the auto-tune rule names the host stack frame (fixed in 652) and its fixture is there;
  - with a Wine tree (wine/ of this tree, WINE_SRC or ~/madeira-bcd/wine; skipped without one): the patch applies once, a second run changes
    nothing, a moved function refuses it and leaves the file alone; the clamp is `> LF_FACESIZE` (a length of 32 is not touched) and sits
    before the memcpy inside NtGdiEnumFonts;
  - the real lines of the function, compiled with the host's fortified memcpy (-O2 -D_FORTIFY_SOURCE=2, the way the iOS SDK builds it): the
    unpatched lines abort for a length of 33, the patched lines survive it with a terminated 31-character name, and for 5 and 32 characters
    both copy the same bytes.
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


spec = importlib.util.spec_from_file_location("enumfonts", root / "tools/patch-wine-enumfonts-facename.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch Wine's win32u NtGdiEnumFonts (a face name that does not fit)\n")
check("CI runs this check and applies the patch before win32u-unix is built",
      "python3 tests/host/check-wine-enumfonts-facename.py\n          python3 tools/patch-wine-enumfonts-facename.py wine\n" in wf[step:step + 500]
      and step < wf.index("      - name: Build win32u-unix\n"))
rules = (root / "build/tools/autotune_rules.py").read_text()
rb = rules[rules.index('"id": "win32u-enumfonts-facename-overflow"'):]
rb = rb[:rb.index("\n    },\n")]
check("the auto-tune rule names the host frame (fixed in 652) and its fixture is there",
      '"fixedIn": 652' in rb and 's("[mach_exc] bt[1]", "NtGdiEnumFonts+0x")' in rb
      and (root / "tests/fixtures/autotune/ff7-2013-launcher-enumfonts-2026-11-07.expect").is_file())

cands = [root / "wine", Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, Path.home() / "madeira-bcd/wine"]
wine = next((c for c in cands if c and (c / P.FILE).is_file()), None)
if not wine:
    print("skip: no Wine tree here (wine/, WINE_SRC or ~/madeira-bcd/wine); the CI and the Mac run the source part")
    print("PASS: NtGdiEnumFonts keeps its face name inside the LOGFONTW" if ok else "FAILED: NtGdiEnumFonts face name")
    sys.exit(0 if ok else 1)

src = (wine / P.FILE).read_text()
if P.MARKER in src:
    print("note: the Wine tree is already patched; the original is rebuilt from it for the comparison")
    src = src.replace(P.NEW, P.OLD, 1)
check("the unpatched function copies face_name_len WCHARs into lf.lfFaceName without a bound", src.count(P.OLD) == 1)
with tempfile.TemporaryDirectory(prefix="madeira-enumfonts-") as tmp:
    tmp = Path(tmp)
    tree = tmp / "w"
    (tree / Path(P.FILE).parent).mkdir(parents=True)
    t = tree / P.FILE
    t.write_text(src)
    script = str(root / "tools/patch-wine-enumfonts-facename.py")
    r1 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    patched = t.read_text()
    r2 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    check("it applies once; a second run says so and changes nothing",
          r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == patched and P.MARKER in patched,
          r1.stdout + r1.stderr + r2.stdout + r2.stderr)
    fn = patched[patched.index(P.FUNCTION_ANCHOR):]
    fn = fn[:fn.index("\n}\n")]
    check("the clamp is `> LF_FACESIZE` (a length of 32 is not touched), cuts to 31, and sits before the memcpy inside NtGdiEnumFonts",
          "    if (face_name_len > LF_FACESIZE) face_name_len = LF_FACESIZE - 1;\n" in fn
          and fn.index("face_name_len > LF_FACESIZE") < fn.index("memcpy( lf.lfFaceName, face_name, face_name_len * sizeof(WCHAR) )")
          and fn.count("memcpy(") == 1)
    t.write_text(src.replace(P.FUNCTION_ANCHOR, P.FUNCTION_ANCHOR.replace("NtGdiEnumFonts", "NtGdiEnumFontsX"), 1))
    r3 = subprocess.run([sys.executable, script, str(tree)], capture_output=True, text=True)
    check("a moved function refuses the patch and leaves the file alone",
          r3.returncode != 0 and "NtGdiEnumFonts of win32u/font.c" in r3.stderr and P.MARKER not in t.read_text(), r3.stderr)

    cc = shutil.which("cc") or shutil.which("clang")
    if not cc:
        print("skip: no C compiler here")
    else:
        # the real lines between `memset( &lf` and the end of the copy, in a function of the same shape (WCHAR / LOGFONTW as Windows has them)
        def lines_of(text):
            body = text[text.index(P.FUNCTION_ANCHOR):]
            body = body[body.index("    memset( &lf, 0, sizeof(lf) );\n"):]
            return body[:body.index("    fe.hdc")]

        harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint16_t WCHAR; typedef uint32_t ULONG; typedef int BOOL;
#define LF_FACESIZE 32
typedef struct { int32_t lfHeight, lfWidth, lfEscapement, lfOrientation, lfWeight; uint8_t lfItalic, lfUnderline, lfStrikeOut, lfCharSet,
                 lfOutPrecision, lfClipPrecision, lfQuality, lfPitchAndFamily; WCHAR lfFaceName[LF_FACESIZE]; } LOGFONTW;
__attribute__((noinline)) int run(ULONG face_name_len, const WCHAR *face_name, ULONG charset, WCHAR *out)
{
    LOGFONTW lf;
%s    memcpy( out, lf.lfFaceName, sizeof(lf.lfFaceName) );
    return (int)face_name_len;
}
int main(int argc, char **argv)
{
    WCHAR in[80], out[LF_FACESIZE];
    unsigned n = (unsigned)atoi(argv[1]), i;
    for (i = 0; i < 80; i++) in[i] = (WCHAR)('A' + (i %% 26));
    memset(out, 0xee, sizeof(out));
    run(n, in, 0, out);
    for (i = 0; i < LF_FACESIZE; i++) printf("%%04x", out[i]);
    printf("\n");
    return 0;
}
'''
        exes = {}
        for which, text in (("orig", src), ("patched", patched)):
            (tmp / (which + ".c")).write_text(harness % lines_of(text))
            r = subprocess.run([cc, "-O2", "-D_FORTIFY_SOURCE=2", "-w", "-o", str(tmp / which), str(tmp / (which + ".c"))],
                               capture_output=True, text=True)
            exes[which] = tmp / which if r.returncode == 0 else None
            check("the %s lines compile on the host with the fortified memcpy" % which, r.returncode == 0, r.stderr)
        if all(exes.values()):
            def run(which, n):
                r = subprocess.run([str(exes[which]), str(n)], capture_output=True, text=True)
                return r.returncode, r.stdout.strip()
            o33, p33 = run("orig", 33), run("patched", 33)
            check("a length of 33 aborts the unpatched lines (the fortified memcpy, as on the phone) and survives the patched ones",
                  o33[0] != 0 and p33[0] == 0, (o33, p33))
            name33 = "".join("%04x" % (ord("A") + (i % 26)) for i in range(31)) + "0000"
            check("the patched lines keep 31 characters and the terminator of a 33-character name", p33[1] == name33, p33)
            same = all(run("orig", n) == run("patched", n) and run("orig", n)[0] == 0 for n in (0, 5, 31, 32))
            check("a length of 0, 5, 31 or 32 copies the same bytes before and after the patch", same,
                  [(n, run("orig", n), run("patched", n)) for n in (0, 5, 31, 32)])
            check("a length of 100 is cut too", run("patched", 100)[0] == 0 and run("patched", 100)[1] == name33)

print("PASS: NtGdiEnumFonts keeps its face name inside the LOGFONTW" if ok else "FAILED: NtGdiEnumFonts face name")
sys.exit(0 if ok else 1)
