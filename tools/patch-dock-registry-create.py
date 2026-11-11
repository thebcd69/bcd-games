#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: the Steam client entries step creates a key that is missing (host result 42, night round 2026-11-11).

Before it asks Valve's client to start the game, the Dock (madeira-dock/src/launch.c, upstream submodule) opens HKCU
Software\\Valve\\Steam\\ActiveProcess and HKLM Software\\Valve\\Steam (32-bit view) and publishes the client's pid, ActiveUser and SteamPID
there. tools/patch-dock-registry-events.py (round 630) reports which open failed and tries it once more after a second. A key that is
ABSENT stays absent: ZB8E97, RAC3KD, GNWWRT (the same iPad, two games in 22 minutes), GWX5H2 and DP9TVR (build 634, three players) all ended
with launch-registry-step=2 and launch-registry-error=2 (ERROR_FILE_NOT_FOUND) after the retry, for every game they started. The prefix
had lost HKLM Software\\Wow6432Node\\Valve\\Steam, which BCD seeds once when it downloads Valve's client (SteamRuntimeFiles.registry);
the app now puts the sections back before each Dock start (SteamRuntimeFiles.repairRegistry). This is the host's own last layer.

After the retry, a start that failed because a key does not exist (error 2 or 3) now CREATES the keys that are not open (RegCreateKeyExW with
the access and the view of the open) and goes on to publish. Numbers only: "launch-registry-created" names each key it created (1 the
ActiveProcess key, 2 the machine key); "launch-registry-create-error" the Win32 error of a create that failed (the old step and error events
follow as before). When the start still ends in 42, "launch-registry-probe" says which keys the host could see: bit 0 HKLM Software\\Valve\\Steam
without a view flag, bit 1 HKLM Software\\Wow6432Node\\Valve\\Steam in the 64-bit view, bit 2 HKLM Software, bit 3 HKLM Software\\Wow6432Node
(64-bit view), bit 4 HKCU Software\\Valve\\Steam\\ActiveProcess. A key that is there is opened as before; only a start that ends in 42 today
runs the new code. The values, their order, the cleanup at "done" and the result are unchanged (restore() puts values back; the created keys stay).
MADEIRA_DOCK_REGISTRY_CREATE=0: no create (the probe still runs).

Must run after tools/patch-dock-registry-events.py. Usage: patch-dock-registry-create.py madeira-dock/src/launch.c
(idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: registry-create-v1"
OLD_FAIL = ("    if (registry_step) {\n"
            "        o->event(\"launch-registry-step\", registry_step);\n"
            "        o->event(\"launch-registry-error\", (int32_t)registry_error);\n"
            "        goto done;\n"
            "    }\n")
NEW_FAIL = ("    /* " + MARKER + " (tools/patch-dock-registry-create.py, night round 2026-11-11; ZB8E97, RAC3KD, GNWWRT, GWX5H2, DP9TVR):\n"
            "     * a key that does not exist (2 or 3 after the retry) is created, so the launch goes on; the app also puts the sections back\n"
            "     * before the start. MADEIRA_DOCK_REGISTRY_CREATE=0 leaves the failure as it was. */\n"
            "    if (registry_step && (registry_error == ERROR_FILE_NOT_FOUND || registry_error == ERROR_PATH_NOT_FOUND) &&\n"
            "        !wide_flag(L\"MADEIRA_DOCK_REGISTRY_CREATE\", L'0')) {\n"
            "        LSTATUS created = ERROR_SUCCESS;\n"
            "        DWORD disposition = 0;   /* REG_CREATED_NEW_KEY (1) or REG_OPENED_EXISTING_KEY (2): only a key that was not there is reported */\n"
            "        if (!active) {\n"
            "            created = RegCreateKeyExW(HKEY_CURRENT_USER, L\"Software\\\\Valve\\\\Steam\\\\ActiveProcess\", 0, NULL, 0,\n"
            "                                      KEY_QUERY_VALUE | KEY_SET_VALUE, NULL, &active, &disposition);\n"
            "            if (created != ERROR_SUCCESS) active = NULL;\n"
            "            else if (disposition == REG_CREATED_NEW_KEY) o->event(\"launch-registry-created\", 1);\n"
            "        }\n"
            "        if (created == ERROR_SUCCESS && !machine) {\n"
            "            created = RegCreateKeyExW(HKEY_LOCAL_MACHINE, L\"Software\\\\Valve\\\\Steam\", 0, NULL, 0,\n"
            "                                      KEY_QUERY_VALUE | KEY_SET_VALUE | KEY_WOW64_32KEY, NULL, &machine, &disposition);\n"
            "            if (created != ERROR_SUCCESS) machine = NULL;\n"
            "            else if (disposition == REG_CREATED_NEW_KEY) o->event(\"launch-registry-created\", 2);\n"
            "        }\n"
            "        if (created == ERROR_SUCCESS) registry_step = 0;\n"
            "        else o->event(\"launch-registry-create-error\", (int32_t)created);\n"
            "    }\n"
            "    if (registry_step) {\n"
            "        {   /* which keys the host can see: bit 0 HKLM Software\\Valve\\Steam (no view flag), 1 HKLM Software\\Wow6432Node\\Valve\\Steam\n"
            "             * (64-bit view), 2 HKLM Software, 3 HKLM Software\\Wow6432Node (64-bit view), 4 HKCU Software\\Valve\\Steam\\ActiveProcess */\n"
            "            struct { HKEY root; const wchar_t *path; REGSAM view; } probes[5] = {\n"
            "                {HKEY_LOCAL_MACHINE, L\"Software\\\\Valve\\\\Steam\", 0},\n"
            "                {HKEY_LOCAL_MACHINE, L\"Software\\\\Wow6432Node\\\\Valve\\\\Steam\", KEY_WOW64_64KEY},\n"
            "                {HKEY_LOCAL_MACHINE, L\"Software\", 0},\n"
            "                {HKEY_LOCAL_MACHINE, L\"Software\\\\Wow6432Node\", KEY_WOW64_64KEY},\n"
            "                {HKEY_CURRENT_USER, L\"Software\\\\Valve\\\\Steam\\\\ActiveProcess\", 0}};\n"
            "            int32_t seen = 0;\n"
            "            for (int probe = 0; probe < 5; ++probe) {\n"
            "                HKEY key = NULL;\n"
            "                if (RegOpenKeyExW(probes[probe].root, probes[probe].path, 0, KEY_QUERY_VALUE | probes[probe].view, &key) == ERROR_SUCCESS) {\n"
            "                    seen |= 1 << probe;\n"
            "                    RegCloseKey(key);\n"
            "                }\n"
            "            }\n"
            "            o->event(\"launch-registry-probe\", seen);\n"
            "        }\n"
            "        o->event(\"launch-registry-step\", registry_step);\n"
            "        o->event(\"launch-registry-error\", (int32_t)registry_error);\n"
            "        goto done;\n"
            "    }\n")
PREREQ = "madeira-bcd: registry-events-v1"


def patch(src):
    if MARKER in src:
        if src.count(NEW_FAIL) != 1 or OLD_FAIL in src.replace(NEW_FAIL, ""):
            raise ValueError("partial or changed registry-create patch")
        return src
    if PREREQ not in src:
        raise ValueError("tools/patch-dock-registry-events.py has not run (its marker is missing)")
    if src.count(OLD_FAIL) != 1:
        raise ValueError("the registry step's failure exit of launch.c not found once (madeira-dock or patch-dock-registry-events.py changed)")
    if "static bool wide_flag(const wchar_t *name, wchar_t expected)" not in src[:src.index(OLD_FAIL)]:
        raise ValueError("wide_flag is not defined before the registry steps (madeira-dock changed)")
    if src.index("HKEY active = NULL, machine = NULL;") > src.index(OLD_FAIL):
        raise ValueError("the key handles are not declared before the registry steps (madeira-dock changed)")
    return src.replace(OLD_FAIL, NEW_FAIL, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-registry-create: " + str(error))
    if result == source:
        print("patch-dock-registry-create: already in")
    else:
        path.write_text(result)
        print("madeira-dock: a missing Steam client key (42) is created; a start that still fails says which keys the host sees")
