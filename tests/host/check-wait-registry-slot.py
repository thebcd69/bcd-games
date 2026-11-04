#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The stuck-wait registry gives every thread its own slot (round 630, agent O1 S17); log only, no Wine runs.

build/ntdll-unix/server_ios.c ios_wait_slot hashed the TEB pointer, (teb >> 16) % 512: every 32-bit program's main thread has its
TEB at its 4 GB window + 0x300000, so all of them shared slot 48, the ++ parity of two waiters flipped each other back to "idle",
and the wineserver's [srv-stuck] report never named a 32-bit main thread's wait (Gothic II AMDWS5, A6WWNQ). Checks: the slot comes
from the Wine thread id, enter makes the sequence odd and leave even whatever a colliding thread did, and the arithmetic.
"""
from pathlib import Path
import re, sys

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/server_ios.c").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


slot = src[src.index("static struct ios_wait_entry *ios_wait_slot(void)"):]
slot = slot[:slot.index("\n}\n")]
check("the slot is (wine tid >> 2) % 512, not the TEB", "return &ios_wait_reg[(tid >> 2) % IOS_WAITREG_SLOTS];" in slot
      and "NtCurrentTeb()->ClientId.UniqueThread" in slot and "(t >> 16)" not in slot)
check("enter makes the sequence odd, leave even", "e->seq = (e->seq + 1) | 1;" in src and "e->seq = (e->seq + 1) & ~1u;" in src
      and "e->seq++;" not in src)
tebs = [0x7900300000, 0x7200300000, 0x7100300000]
check("the old hash put the 32-bit main threads of three windows in one slot", len({(t >> 16) % 512 for t in tebs}) == 1)
tids = [0x00b8, 0x00bc, 0x0124, 0x0128]
check("the new one gives consecutive thread ids their own slots", len({(t >> 2) % 512 for t in tids}) == len(tids))
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-wait-registry-slot.py" in wf)
print("PASS: every thread's wait has its own slot in the stuck-wait registry" if ok else "FAILED: stuck-wait registry slot")
sys.exit(0 if ok else 1)
