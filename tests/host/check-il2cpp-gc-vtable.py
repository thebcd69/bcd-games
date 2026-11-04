#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The IL2CPP collector-race repair has a third pass for the race lost (round 630, agent U S8; Lonely Mountains: Snow Riders F6JNBQ).

signal_arm64_ios.c case 3.4 repairs a virtual call that read its vtable slot one byte off while Boehm GC had bit 0 of the klass word set.
Pass 0 looks for an odd register, pass 1 for an object whose klass word is odd; in F6JNBQ the collector had cleared the bit again before
the fault, both passes found nothing and the game died. Pass 2 accepts a register holding the clean (8-aligned) table, proved by the same
64-bit equality, and writes nothing back.

Checks, source-level (the code runs on the Mach exception thread; the iOS syntax build is tests/host/check-rwx-fault-diag.py's): three
passes; pass 2 takes only an 8-aligned value in the user range and runs after passes 0 and 1; pass 0 fixes the register, pass 1 the
object's word, pass 2 neither; the fix-ups of the call target, x11/x12 and [x28+0x18] are the same for every pass; the line says
"even" for pass 2; the auto-tune rule and the list row; CI.
"""
from pathlib import Path
import json, sys

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1200]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


blk = src[src.index("/* 3.4. Unity IL2CPP GC-marked vtable pointer (klass | 1) recovery at DispatcherLoopTop."):]
blk = blk[:blk.index("handled = 1;\n                                    break;")]
check("three passes", "for (pass = 0; pass < 3 && !handled; pass++)" in blk)
p2 = blk[blk.index("else if (pass == 2)"):]
p2 = p2[:p2.index("clean_base = odd_ptr & ~7ULL;")]
check("pass 2 takes only an 8-aligned value in the user range and makes it look like a marked word",
      "if ((rval & 7ULL) != 0 || rval < 0x10000ULL || rval >= 0x0000800000000000ULL)\n                                    continue;" in p2
      and "odd_ptr = rval | 1ULL;" in p2)
check("pass 0 fixes the register, pass 1 the object's word, pass 2 neither",
      "if (pass == 0) state.__x[r] = clean_base;\n                                    else if (pass == 1) mach_vm_write(" in blk
      and blk.count("mach_vm_write( mach_task_self(), (mach_vm_address_t)rval,") == 1)
check("the call target, x11/x12 and [x28+0x18] are fixed the same way for every pass",
      "state.__x[12] = s0;" in blk and "state.__x[11] = s0 >> 18;" in blk and "(mach_vm_address_t)(state.__x[28] + 0x18)" in blk)
check("the line says which kind of repair it was", '"[il2cpp-gc-vtable] #%d repaired %s vtable (pass=%u x%d=0x%llx+0x%x) "' in src
      and 'fn, pass == 2 ? "even" : "odd", pass, r,' in src)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the lost race fixed in 630", '"id": "il2cpp-gc-klass-race-lost"' in rules)
recs = json.loads((root / "recommended/recommendations.json").read_text())
row = next((r for r in recs["rules"] if r["id"] == "lonely-mountains-snow-riders"), None)
check("Lonely Mountains' row gives the real suspend from build 630",
      row is not None and {"line": "env.MADEIRA_REAL_SUSPEND = 1", "minBuild": 630} in row["config"])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-il2cpp-gc-vtable.py" in wf)
print("PASS: the IL2CPP collector-race repair has its third pass" if ok else "FAILED: IL2CPP collector-race repair")
sys.exit(0 if ok else 1)
