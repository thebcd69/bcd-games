#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 669 (agent A S4; Cooking Simulator 2 742A8P, Trackmania YQKE46): the [alloc-fail] line of a refused allocation names the stage that
refused it from a thread-local record (ios_af) that only map_view reset. A request refused before map_view (262,144 hinted RWX reservations
in one second) printed an earlier request's record: "STAGE=none err=1 at 0xffff70000+0x90000" for every hint. Both allocation entries now start
the record clean; map_view still resets it for its own attempt. Log text only."""
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
v = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


reset = "ios_af.stage = 0; ios_af.err = 0; ios_af.addr = 0; ios_af.size = 0;"
for name in ("ios_nt_impl_NtAllocateVirtualMemory(", "ios_nt_impl_NtAllocateVirtualMemoryEx("):
    i = v.index("static inline __attribute__((always_inline)) NTSTATUS " + name)
    head = v[i:i + 1200]
    check(f"{name[12:-1]}: the record starts clean at the top of the call", reset in head)
check("map_view still starts its own attempt clean", "ios_af.stage = 0;   /* ml814: this request's attribution starts clean */" in v)
check("the record is thread-local", "static __thread struct ios_af_rec ios_af;" in v)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-alloc-fail-record.py" in wf)
print("PASS: alloc-fail record" if ok else "FAILED: alloc-fail record")
sys.exit(0 if ok else 1)
