#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: the Steam client entries step (host result 42) says which part failed, and its two opens are tried once more after
a second (round 630, agent A1 S11).

Before it asks Valve's client to start the game, the Dock (madeira-dock/src/launch.c, upstream submodule) opens HKCU
Software\\Valve\\Steam\\ActiveProcess and HKLM Software\\Valve\\Steam and publishes the client's pid, ActiveUser and SteamPID there for
the game to find. Any failure ended the start with result 42 and no event: 8 of 976 archive logs with a host result (builds 565-625;
Firewatch, GTA V Legacy, PlateUp! BN3SXW, whose next start worked). Which open or which value failed was not in any log.

Now each failure reports "launch-registry-step" (1 the ActiveProcess key, 2 the machine key, 3 pid, 4 ActiveUser, 5 SteamPID) and, for
the opens, "launch-registry-error" (the Win32 error). An open that fails is tried once more after one second
("launch-registry-retry" names it): an open changes nothing, so a retry cannot leave anything behind, and a start whose open fails
fails today. MADEIRA_DOCK_REGISTRY_RETRY=0: no second try. The values, the order, the cleanup at "done" and the result are unchanged.

Usage: patch-dock-registry-events.py madeira-dock/src/launch.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: registry-events-v1"
OLD_OPEN = ("    if (RegOpenKeyExW(HKEY_CURRENT_USER, L\"Software\\\\Valve\\\\Steam\\\\ActiveProcess\", 0,\n"
            "                     KEY_QUERY_VALUE | KEY_SET_VALUE, &active) != ERROR_SUCCESS ||\n"
            "        RegOpenKeyExW(HKEY_LOCAL_MACHINE, L\"Software\\\\Valve\\\\Steam\", 0,\n"
            "                     KEY_QUERY_VALUE | KEY_SET_VALUE | KEY_WOW64_32KEY, &machine) != ERROR_SUCCESS) goto done;\n")
NEW_OPEN = ("    /* " + MARKER + " (tools/patch-dock-registry-events.py, round 630, BN3SXW): which open failed, and one more try of\n"
            "     * a failed open after 1 s (an open changes nothing). MADEIRA_DOCK_REGISTRY_RETRY=0: no second try. */\n"
            "    int registry_step = 0;\n"
            "    LSTATUS registry_error = ERROR_SUCCESS;\n"
            "    for (int registry_try = 0; registry_try < 2; ++registry_try) {\n"
            "        registry_step = 0;\n"
            "        if (!active && (registry_error = RegOpenKeyExW(HKEY_CURRENT_USER, L\"Software\\\\Valve\\\\Steam\\\\ActiveProcess\", 0,\n"
            "                                                       KEY_QUERY_VALUE | KEY_SET_VALUE, &active)) != ERROR_SUCCESS) {\n"
            "            active = NULL;\n"
            "            registry_step = 1;\n"
            "        } else if (!machine && (registry_error = RegOpenKeyExW(HKEY_LOCAL_MACHINE, L\"Software\\\\Valve\\\\Steam\", 0,\n"
            "                                                              KEY_QUERY_VALUE | KEY_SET_VALUE | KEY_WOW64_32KEY, &machine)) != ERROR_SUCCESS) {\n"
            "            machine = NULL;\n"
            "            registry_step = 2;\n"
            "        }\n"
            "        if (!registry_step || registry_try || wide_flag(L\"MADEIRA_DOCK_REGISTRY_RETRY\", L'0')) break;\n"
            "        o->event(\"launch-registry-retry\", registry_step);\n"
            "        o->sleep_ms(1000);\n"
            "    }\n"
            "    if (registry_step) {\n"
            "        o->event(\"launch-registry-step\", registry_step);\n"
            "        o->event(\"launch-registry-error\", (int32_t)registry_error);\n"
            "        goto done;\n"
            "    }\n")
OLD_PUBLISH = ("    if (!publish(&values[0], GetCurrentProcessId()) ||\n"
               "        !publish(&values[1], (DWORD)steamid) || !publish(&values[2], GetCurrentProcessId())) goto done;\n")
NEW_PUBLISH = ("    if (!publish(&values[0], GetCurrentProcessId())) { o->event(\"launch-registry-step\", 3); goto done; }   /* " + MARKER + " */\n"
               "    if (!publish(&values[1], (DWORD)steamid)) { o->event(\"launch-registry-step\", 4); goto done; }\n"
               "    if (!publish(&values[2], GetCurrentProcessId())) { o->event(\"launch-registry-step\", 5); goto done; }\n")
DECL = "    HKEY active = NULL, machine = NULL;\n"


def patch(src):
    if MARKER in src:
        if src.count(NEW_OPEN) != 1 or src.count(NEW_PUBLISH) != 1 or OLD_OPEN in src or OLD_PUBLISH in src:
            raise ValueError("partial or changed registry-events patch")
        return src
    for name, old in (("the two registry opens", OLD_OPEN), ("the three published values", OLD_PUBLISH), ("the key handles", DECL)):
        if src.count(old) != 1:
            raise ValueError(name + " of launch.c not found once (madeira-dock changed)")
    if not (src.index(DECL) < src.index(OLD_OPEN) < src.index(OLD_PUBLISH)):
        raise ValueError("the registry steps of launch.c are not in their order (madeira-dock changed)")
    if "static bool wide_flag(const wchar_t *name, wchar_t expected)" not in src[:src.index(OLD_OPEN)]:
        raise ValueError("wide_flag is not defined before the registry steps (madeira-dock changed)")
    return src.replace(OLD_OPEN, NEW_OPEN, 1).replace(OLD_PUBLISH, NEW_PUBLISH, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-registry-events: " + str(error))
    if result == source:
        print("patch-dock-registry-events: already in")
    else:
        path.write_text(result)
        print("madeira-dock: the Steam client entries step (42) says which part failed; a failed open is tried once more after 1 s")
