#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Check kernelbase-optin.dll before it ships (round 624, agent E2 S3; Glyphica: Typing Survival).

The shipped arm64ec-windows/kernelbase.dll is a prebuilt file that CI never rebuilds, so the Wine mirror's opt-in
GetProcessMitigationPolicy fix (dlls/kernelbase/process.c, env.MADEIRA_ZERO_MITIGATION_POLICY = 1) never ran on a phone:
the shipped file has no "MADEIRA_" string at all, and Glyphica failed the same way with the key set (7611AG, build 600;
J9JDBG, build 623). tools/build-wine-extra-dlls.sh (block ZMP) builds kernelbase.dll from the tree as kernelbase-optin.dll;
a game that lists kernelbase in env.MADEIRA_OPTIN_DLLS gets it for its session (WineProcessBridge.m's opt-in loop).

  verify-kernelbase-optin.py BUILT SHIPPED
      BUILT carries the switch's UTF-16 name (the tree's process.c is in it), is for the shipped file's machine, and exports
      exactly the shipped kernelbase.dll's names (a game sees the same entry points). Exit 1 with the reasons otherwise.
"""
import importlib.util
import sys
from pathlib import Path

MARKER = "MADEIRA_ZERO_MITIGATION_POLICY"

_spec = importlib.util.spec_from_file_location("kb_fibertrace", Path(__file__).resolve().parent / "patch-wine-kernelbase-fibertrace.py")
_kb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_kb)


def problems(built, shipped):
    d, machine, names = _kb.pe_exports(built)
    _, smachine, snames = _kb.pe_exports(shipped)
    bad = []
    if MARKER.encode("utf-16-le") not in d:
        bad.append("no UTF-16 '%s': not built from the tree that has the switch (dlls/kernelbase/process.c)" % MARKER)
    if machine != smachine:
        bad.append("machine %#x, the shipped kernelbase.dll's is %#x" % (machine, smachine))
    if names != snames:
        bad.append("exports differ from the shipped kernelbase.dll: only here %s, only there %s"
                   % (sorted(names - snames)[:8], sorted(snames - names)[:8]))
    return bad, len(names)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    bad, n = problems(sys.argv[1], sys.argv[2])
    if bad:
        sys.exit("verify %s:\n  %s" % (sys.argv[1], "\n  ".join(bad)))
    print("verify %s: %s present, the %d exports of %s" % (sys.argv[1], MARKER, n, sys.argv[2]))
