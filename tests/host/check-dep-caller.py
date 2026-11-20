#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Portions copyright 2026 Russell Barton: the change it checks is from https://github.com/willfaust/Madeira (pull request #615,
# commit 6cfcc828) under LGPL-2.1-or-later, taken for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""NtSetInformationProcess(ProcessExecuteFlags): only a WoW64 caller has DEP options, as on Windows (build 669).

is_wow64() is a session-wide answer in BCD's Wine (any 32-bit pseudo-process booted), so a 64-bit thread got through and
virtual_set_force_exec re-protected every view of the session. The calling thread's TEB (WowTebOffset) decides now.
(Upstream's own check models its per-process DEP flags, which BCD does not have; this checks BCD's line.)"""
import re, sys
from pathlib import Path
R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/process_ios.c").read_text()
case = src[src.index("    case ProcessExecuteFlags:\n        /* dep-caller"):]
case = case[:case.index("break;")]
ok = ("if ((is_win64 && !NtCurrentTeb()->WowTebOffset) || size != sizeof(ULONG)) return STATUS_INVALID_PARAMETER;" in case
      and "is_wow64()" not in case.split("*/", 1)[1])
wf = (R / ".github/workflows/build-ipa.yml").read_text()
ok = ok and "run: python3 tests/host/check-dep-caller.py" in wf
print("PASS: DEP options only for a WoW64 caller (the thread's TEB decides)" if ok else "FAILED: dep-caller")
sys.exit(0 if ok else 1)
