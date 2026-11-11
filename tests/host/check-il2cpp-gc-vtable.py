#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""(Night round 2026-11-11, agent G1 S1: a fourth pass, for the race lost with only the OBJECT in a register; see the checks at the end of the pass list.)
The IL2CPP collector-race repair has a third pass for the race lost (round 630, agent U S8; Lonely Mountains: Snow Riders F6JNBQ).

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
check("four passes (the fourth: night round 2026-11-11, agent G1 S1)", "for (pass = 0; pass < 4 && !handled; pass++)" in blk)
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
      and 'fn, pass >= 2 ? "even" : "odd", pass, r,' in src)
# Night round 2026-11-11 (agent G1 S1; 1000xRESIST 63G9QV, build 634, and 0DA164, build 614): pass 3, the race lost and only the object in a register
p3 = blk[blk.index("else if (pass == 3)"):]
p3 = p3[:p3.index("clean_base = odd_ptr & ~7ULL;")]
check("pass 3 reads the object's klass word, takes only an 8-aligned user-range word, makes it look like a marked word, and runs after passes 0-2",
      blk.index("else if (pass == 2)") < blk.index("else if (pass == 3)")
      and "if ((rval & 7ULL) != 0 || rval < 0x10000ULL || rval >= 0x0000800000000000ULL)\n                                    continue;" in p3
      and "(mach_vm_address_t)rval, 8," in p3 and "if ((odd_ptr & 7ULL) != 0 || odd_ptr < 0x10000ULL || odd_ptr >= 0x0000800000000000ULL)" in p3
      and "odd_ptr |= 1ULL;" in p3)
check("pass 3 writes nothing back: the only write through the object's address is still pass 1's (one mach_vm_write of rval), passes 2 and 3 are marked",
      blk.count("mach_vm_write( mach_task_self(), (mach_vm_address_t)rval,") == 1 and "passes 2 and 3 write nothing" in blk)
# the equality that proves the table, on a constructed case of 63G9QV's shape (object in a register, klass clean, no register holds the table)
import struct
klass = 0x7d1250cf90                                    # [RCX+0], clean (63G9QV's [x86_dst] @RCX+0)
slots = [0x1000000a1, 0x7c0000b2c3, 0x7c0000d4e5, 0x7c0000f607]   # the table the object points to (slot k at klass + 8k)
idx = 1
bad_rip = (slots[idx] >> 8) | ((slots[idx + 1] << 56) & (2**64 - 1))       # what `mov rax,[klass|1 + 0x208]` read: one byte off
found = [i for i in range(len(slots) - 1) if (((slots[i] >> 8) | ((slots[i + 1] << 56) & (2**64 - 1))) == bad_rip) and 0x10000 <= slots[i] < 2**47]
check("the 64-bit equality (s0 >> 8 | s1 << 56 == bad_rip) finds the slot from the clean klass word alone, and nothing else in the table", found == [idx], found)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the lost race fixed in 630", '"id": "il2cpp-gc-klass-race-lost"' in rules)
check("auto-tune names the object-only race fixed in 652 (rule il2cpp-gc-klass-object-race-lost) and offers the real suspend first (rule unity-gc-real-suspend, line env.MADEIRA_REAL_SUSPEND = 1, priority 3 < 4)",
      '"id": "il2cpp-gc-klass-object-race-lost"' in rules and '"id": "unity-gc-real-suspend"' in rules
      and '"lines": ["env.MADEIRA_REAL_SUSPEND = 1"],' in rules and '"priority": 3,\n        "outcomes": ["crashed"],\n        "all": [s("repaired odd vtable", "[il2cpp-gc-vtable] #")' in rules
      # the real-suspend rule takes only this object-race signature (not the older, fixed class's NtRaiseException line, not the CTX_FRAME family's logs)
      and '"none": [s("[madeira-env] game MADEIRA_REAL_SUSPEND=1"), s("repaired even vtable"), s("NtRaiseException Unhandled exception code c0000005")]' in rules
      and '"none": [s("repaired even vtable"), s("NtRaiseException Unhandled exception code c0000005")]' in rules)
fx1 = json.loads((root / "tests/fixtures/autotune/1000xresist-il2cpp-object-race-lost-2026-11-07.expect").read_text())
fx2 = json.loads((root / "tests/fixtures/autotune/synthetic-1000xresist-object-race-lost-real-suspend-on.expect").read_text())
check("fixture 63G9QV: the real suspend is the next attempt, the fourth-pass rule is the second match; the synthetic one with the line on: the fourth-pass rule is known",
      fx1["matched"] == ["unity-gc-real-suspend", "il2cpp-gc-klass-object-race-lost"] and fx1["next"] == ["env.MADEIRA_REAL_SUSPEND = 1"] and fx1["status"] == "trying"
      and fx2["matched"] == ["il2cpp-gc-klass-object-race-lost"] and fx2["status"] == "known" and fx2["next"] == [])
recs = json.loads((root / "recommended/recommendations.json").read_text())
row = next((r for r in recs["rules"] if r["id"] == "lonely-mountains-snow-riders"), None)
check("Lonely Mountains' row gives the real suspend from build 630",
      row is not None and {"line": "env.MADEIRA_REAL_SUSPEND = 1", "minBuild": 630} in row["config"])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-il2cpp-gc-vtable.py" in wf)
print("PASS: the IL2CPP collector-race repair has its third pass" if ok else "FAILED: IL2CPP collector-race repair")
sys.exit(0 if ok else 1)
