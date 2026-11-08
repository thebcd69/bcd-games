#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A game's own switches end with its session (WineProcessBridge.m's unsetenv list).

The bridge setenv()s every env. line of the game's file at the start; a switch missing from the
unsetenv list at the end would carry into the next game of the same app run (an unrelated game
would get, e.g., the Runtimes\\arm64 registry marker). The switches added on 2026-10-20/22 are
checked here by name, next to the older ones.
"""
from pathlib import Path
import re, sys
root = Path(__file__).resolve().parents[2]
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
unset = set(re.findall(r'unsetenv\("(MADEIRA_[A-Z0-9_]+)"\);', bridge))
want = ["MADEIRA_WER", "MADEIRA_WINTYPES", "MADEIRA_OPTIN_DLLS", "MADEIRA_VCRT_ARM64_KEY",
        "MADEIRA_TAIL_CARVE_EXEC_CHECK", "MADEIRA_LUM_PROBE", "MADEIRA_FEX_BAND_KEEP_MB", "MADEIRA_X64_MACHINE",
        "MADEIRA_POOL_CLAMP_SECTIONS", "MADEIRA_TINY_WINDOW_FIX", "MADEIRA_WG_64BIT", "MADEIRA_NLS_EXTRA",
        "MADEIRA_REAL_SUSPEND", "MADEIRA_FIRST_PROC_OWN_EXIT_FLAG"]
missing = [n for n in want if n not in unset]
for n in want:
    print(("ok   " if n in unset else "FAIL ") + n + " is cleared at the end of a session")
print("PASS: session switches" if not missing else "FAILED: session switches")
sys.exit(1 if missing else 0)
