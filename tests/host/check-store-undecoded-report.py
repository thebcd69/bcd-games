#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Night round 2026-11-11 (agent G9 S11 and S3; tModLoader 40CM32 and 7GA4A9, 173 archive logs; SpartanPort M0G5VG and 17 more logs).

S11: the Mach exception thread's "[store-undecoded] ... ADD THIS ENCODING to the case-4 emulator" line was printed for faults that are not
stores: an instruction FETCH from a page that is not executable (pc equals the address: `brk` 0xd4200000, `sub sp,sp,#0x40` 0xd10103ff, `mrs`
0xd53bd079, `str x30,[sp,#-16]!` 0xf81f0ffe fetched as code) and any word outside the A64 load/store group. The line is capped at 8 prints,
so those prints also took the place of the genuine gaps. Now such a fault gets its own, shorter line with its own cap (3) and the same
"[store-undecoded]" tag (auto-tune's rules read the tag: fex-avx-on excludes a log that has one); a genuine gap keeps the old line and cap.

S3 (NOT changed, pinned here): the four immediate-offset branches (STR X/W, STRB, STRH) take bits 11:10 = 00 and 01 only; the PRE-index form
(11, 0x78038d8a = `strh w10,[x12,#56]!` in 17 logs, 0x78038d1f in one) is declined to the BSD handler on purpose. All 18 logs show the BSD
handler's "[wr-strip] restored prot=... resuming (healed=1)" for the same pc and address within a few hundred lines, and Grind Survivors played
92 and 133 s that way; emulating the store would write through the alias slot, the wrong memory when the alias is stale (M0G5VG). The comment
above the branches says so; this check keeps the masks as they are so that a change of them has to read it.

Held here, with no Wine running:
  1. Source shape of the S11 split, and the S3 masks and comment.
  2. The real `nostore` expression, extracted from signal_arm64_ios.c and compiled on the host, over the words of the logs (and their pc / address).
  3. CI runs this check.
Needs python3 and a C compiler."""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    if not cond and detail:
        print(detail)
    ok = ok and bool(cond)


src = (R / "build/ntdll-unix/signal_arm64_ios.c").read_text()

# 1. source shape ---------------------------------------------------------------------------------------------------------------------------
u0 = src.find("static int undecoded_n;\n                        static int undecoded_nostore_n;")
u1 = src.find("if (emulated)", u0)
rep = src[u0:u1]
check("the report block is found and the `static int undecoded_n;` line the older checks pin is unchanged", u0 > 0 and u1 > u0)
m = re.search(r"const int nostore = (.*?);\n\s*if \(nostore\)", rep, re.S)
expr = re.sub(r"\s+\)", ")", re.sub(r"\s+", " ", re.sub(r"/\*.*?\*/", "", m.group(1)))).strip() if m else ""
check("[store-undecoded]: `nostore` = a fetch fault (pc == addr) or a word outside the load/store group",
      expr == "(fault_pc == (uint64_t)fault_addr) || ((insn & 0x0a000000) != 0x08000000 && (insn & 0xffffffe0) != 0xd50b7420)")
nostore_print = rep.split("if (nostore)")[1].split("else if (undecoded_n < 8)")[0] if "if (nostore)" in rep else ""
check("the nostore line has its own cap (3), keeps the tag and says why; it does not say ADD THIS ENCODING",
      "undecoded_nostore_n < 3" in nostore_print and "[store-undecoded] #%d rev=ml350" in nostore_print and "not a data store" in nostore_print
      and "ADD THIS ENCODING" not in nostore_print)
genuine = rep.split("else if (undecoded_n < 8)")[1] if "else if (undecoded_n < 8)" in rep else ""
check("a genuine gap keeps the old line and its cap of 8",
      "[store-undecoded] #%d rev=ml350 insn=0x%08x pc=0x%llx addr=0x%llx " in genuine and "alias OK, ADD THIS ENCODING to the case-4 emulator" in genuine)
a = src.find("/* STR (immediate, post/pre-index, 64-bit)")
b = src.find("/* SIMD/FP STR (immediate, post/pre-index, D-reg = 64-bit)")
block = src[a - 2200:b]
for base in ("0xf8000000", "0xb8000000", "0x38000000", "0x78000000"):
    check("S3: branch %s still takes bits 11:10 = 00 and 01 only" % base, ("else if ((insn & 0xffe00000) == %s && (insn & 0x800) == 0)" % base) in block)
check("S3: the comment above the branches records why the pre-index form is left to the BSD handler",
      "left to the next handler ON PURPOSE" in block and "[wr-strip] restored prot=" in block and "M0G5VG" in block)

# 2. the real expression, compiled ----------------------------------------------------------------------------------------------------------
cc = shutil.which("clang") or shutil.which("cc")
if not cc or not expr:
    print("note: no C compiler; the compiled part is skipped")
else:
    prog = ("#include <stdint.h>\n#include <stdio.h>\nstatic int f( uint64_t fault_pc, uint64_t fault_addr, uint32_t insn )\n{\n    const int nostore = %s;\n"
            "    return nostore;\n}\nint main( void )\n{\n" % expr)
    cases = [
        ("brk #0x1000 at a fetch", 0x1000, 0x1000, 0xd4200000, 1),
        ("sub sp,sp,#0x40 at a fetch", 0x2000, 0x2000, 0xd10103ff, 1),
        ("mrs at a fetch", 0x3000, 0x3000, 0xd53bd079, 1),
        ("str x30,[sp,#-16]! fetched as code (7GA4A9)", 0x4000, 0x4000, 0xf81f0ffe, 1),
        ("str x19,[sp,#-16]! fetched as code (7GA4A9)", 0x5000, 0x5000, 0xf81f0ff3, 1),
        ("a data-processing word with pc != addr", 0x6000, 0x7000, 0xd10103ff, 1),
        ("strh w10,[x12,#56]! (pre-index) data store", 0x151b0beb4, 0x7257890038, 0x78038d8a, 0),
        ("casal x8,x0,[x19] (Jetboard Joust, N4F8JR)", 0x8000, 0x9000, 0xc8e8ff60, 0),
        ("stlxrb w12,w11,[x8] (Graveyard Keeper 2)", 0x8000, 0x9000, 0x880cfd0b, 0),
        ("ldadd w6,w1,[x8] (the 0xb8f60106 line)", 0x8000, 0x9000, 0xb8f60106, 0),
        ("stp q16,q17,[x11],#32 (ULTRAKILL)", 0x8000, 0x9000, 0xac814570, 0),
        ("str x1,[x2] (unsigned offset)", 0x8000, 0x9000, 0xf9000041, 0),
        ("dc zva, x5 (a system instruction that writes)", 0x8000, 0x9000, 0xd50b7425, 0),
        ("dc civac, x5 (a cache op, not a write)", 0x8000, 0x9000, 0xd50b7e25, 1),
    ]
    for i, c in enumerate(cases):
        prog += '    printf( "%%d\\n", f( 0x%xull, 0x%xull, 0x%08xu ) );\n' % (c[1], c[2], c[3])
    prog += "    return 0;\n}\n"
    with tempfile.TemporaryDirectory() as d:
        cf = Path(d) / "t.c"
        cf.write_text(prog)
        exe = Path(d) / "t"
        r = subprocess.run([cc, "-O1", "-o", str(exe), str(cf)], capture_output=True, text=True)
        check("the extracted expression compiles", r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
            for i, c in enumerate(cases):
                check("%s -> %s" % (c[0], "nostore (short line)" if c[4] else "genuine (ADD THIS ENCODING)"), out[i] == str(c[4]))

# 3. CI -------------------------------------------------------------------------------------------------------------------------------------
check("CI runs this check", "tests/host/check-store-undecoded-report.py" in (R / ".github/workflows/build-ipa.yml").read_text())
print("PASS: the [store-undecoded] report names only real store gaps" if ok else "FAILED")
sys.exit(0 if ok else 1)
