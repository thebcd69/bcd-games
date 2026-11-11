#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Capcom's InstallerMessage.exe is provided by BCD (build 652; app/Madeira/DockInstallers.swift; night round 2026-11-11, agent A2 S5).

Resident Evil 4 D8RPHE, Monster Hunter Rise Q6WDVC, Mega Man Zero/ZX Legacy Collection Z3F8AX (42 logs since 2026-10-17): Capcom's
codec notice (.NET WinForms) cannot run under Wine Mono and installs nothing, so every Capcom game's first Dock start was installs-only
("This start runs only the installs. Restart BCD after them"). Checks:
  - isCapcomCodecNotice matches only InstallerMessage.exe (any case, any folder);
  - the plan marks it provided while MADEIRA_DOCK_INSTALLERMESSAGE_PROVIDED is on (default on, read through flag(); 0 = as before);
  - its log line names it before the DirectX branch; provider() calls it BCD's;
  - no auto-tune rule is possible (an installs-only session is not evaluated); CI runs this check.
"""
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1200])


s = (root / "app/Madeira/DockInstallers.swift").read_text()
fn = s[s.index("    static func isCapcomCodecNotice(_ executable: String) -> Bool {"):]
fn = fn[:fn.index("\n    }\n")]
check("isCapcomCodecNotice names only InstallerMessage.exe, by the file part, lower-cased",
      re.findall(r'"([^"]+\.exe)"', fn) == ["installermessage.exe"] and '.split(separator: "\\\\").last' in fn and ".lowercased()" in fn, fn)
check("the plan marks it provided while the switch is on (default on through flag(); 0 runs it as before)",
      'let capcomNotice = flag("MADEIRA_DOCK_INSTALLERMESSAGE_PROVIDED")' in s
      and "|| (capcomNotice && DockInstallScripts.isCapcomCodecNotice($0.executable))" in s
      and "nonisolated static func flag(_ name: String) -> Bool { SteamSignIn.flag(name, default: true) }" in s)
log = s[s.index("} else if DockInstallScripts.isCapcomCodecNotice(item.process.executable) {"):]
check("its log line says what it is, before the DirectX branch",
      "is Capcom's codec notice, a .NET program that cannot run under Wine Mono and installs nothing" in log[:600]
      and s.index("} else if DockInstallScripts.isCapcomCodecNotice(item.process.executable) {")
      < s.index('} else if DockInstallScripts.provider(item.process) == "BCD" {'))
check("provider() calls it BCD's", 'isCapcomCodecNotice(process.executable) ? "BCD (Capcom\'s codec notice)"' in s)
# No auto-tune rule: the engine does not evaluate an installs-only session (AutoTune.swift), and the first Capcom start is exactly
# one; the fix is visible in the [dock-installers] line ("is Capcom's codec notice") instead.
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-dock-capcom-notice.py" in ci)
print("PASS: Capcom codec notice provided" if ok else "FAILED: Capcom codec notice")
sys.exit(0 if ok else 1)
