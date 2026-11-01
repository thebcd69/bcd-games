#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 626 (agent A1 S12; XMTZMJ, 563J50): a BCD Dock start whose one-time installs run the EA app's installer links msiexec.exe
into system32 for that session (its bootstrapper starts the Windows Installer service first; Wine's MSIServer runs
system32\\msiexec.exe). Source pins and the matcher's behaviour (ported, like check-dock-installers)."""
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


di = (R / "app/Madeira/DockInstallers.swift").read_text()
cv = (R / "app/Madeira/ContentView.swift").read_text()
cat = (R / "build/tools/gen-config-catalog.py").read_text()

m = re.search(r'static func isEAAppInstaller\(_ executable: String\) -> Bool \{\n\s*\(executable\.split\(separator: "\\\\"\)\.last\.map\(String\.init\) \?\? executable\)\.lowercased\(\) == "eaappinstaller\.exe"\n\s*\}', di)
check("DockInstallScripts.isEAAppInstaller compares the last path part, lower-cased, with eaappinstaller.exe", m)


def is_ea(executable):   # the Swift matcher, ported
    return (executable.split("\\")[-1] if executable else executable).lower() == "eaappinstaller.exe"


cases = {r"C:\Program Files\EA Games\Mass Effect Legendary Edition\__Installer\Origin\redist\internal\EAappInstaller.exe": True,
         r"EAAPPINSTALLER.EXE": True, r"C:\x\EAappInstaller.exe.bak": False, r"C:\x\EAappInstallerX.exe": False,
         r"C:\x\DXSETUP.exe": False, "": False}
check("the matcher takes the EA app's installer and nothing else", all(is_ea(k) == v for k, v in cases.items()))
check("runsEAAppInstaller is per start: reset with the other state and set from the batch's pending programs",
      "private(set) static var runsEAAppInstaller = false" in di
      and "failedDirectX = false; sessionAppID = 0; runsEAAppInstaller = false" in di
      and "runsEAAppInstaller = pending.contains { DockInstallScripts.isEAAppInstaller($0.executable) }" in di)
i_ep, i_desk, i_ea, i_else = (cv.find("if epicSession {"), cv.find("} else if windowsDesktopEntry {"), cv.find("} else if eaInstallerSession {"),
                               cv.find('unsetenv("MADEIRA_MSIEXEC_SYSTEM32")'))
check("the session branch: Epic, the Windows desktop, then the EA installer's Dock session, then every other start unchanged",
      0 < i_ep < i_desk < i_ea < i_else)
check("the branch is read on the main thread with its switch (default on)",
      'let eaInstallerSession = DockInstallers.runsEAAppInstaller && DockInstallers.flag("MADEIRA_DOCK_EA_MSISERVICE")' in cv
      and 'nonisolated static func flag(_ name: String) -> Bool { SteamSignIn.flag(name, default: true) }' in di)
check("the log line names it", '[msiexec64] Dock install of the EA app: msiexec in system32 for this session' in cv)
check("the catalog lists the switch, default 1", '"env.MADEIRA_DOCK_EA_MSISERVICE": {"category": "Steam & Dock"' in cat
      and '"default": "1"' in cat.split('"env.MADEIRA_DOCK_EA_MSISERVICE"')[1][:300])
print("PASS: EA app installer in a Dock install session" if ok else "FAILED")
sys.exit(0 if ok else 1)
