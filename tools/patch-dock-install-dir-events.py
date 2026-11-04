#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: the two silent install-folder failures (host result 41) say which check failed (round 630, agent A1 S16).

Before it asks Valve's client to start a game, the Dock (madeira-dock/src/launch.c, upstream submodule) compares the install folder
Valve's client names for the app with the one BCD recorded (MADEIRA_STEAM_HOST_EXPECTED_INSTALL). A mismatch reports
"launch-install-directory-mismatch"; the other two ways to fail, an empty or too long recorded folder and an answer of the client that
is empty, too long or not UTF-8, ended with result 41 and no event. BCD Dock start N14Q6D (GP Bikes, build 626): no byte came from
Steam's servers, the documented offline fallback ran (session-offline-fallback=1) and the start ended with a silent 41.

Log only: before each silent "return 41" the host now reports one number: "launch-install-directory-expected-length" (the recorded
folder's length, 0 when unset) or "launch-install-directory-got" (the client's return value, which is the folder's length on success).
Nothing else changes; the checks and the result are the same.

Usage: patch-dock-install-dir-events.py madeira-dock/src/launch.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: install-dir-events-v1"
OLD_EXPECTED = "    if (!length || length >= 32768) return 41;\n"
NEW_EXPECTED = ("    if (!length || length >= 32768) {\n"
                "        /* " + MARKER + " (tools/patch-dock-install-dir-events.py, round 630, N14Q6D): which check failed. */\n"
                "        o->event(\"launch-install-directory-expected-length\", (int32_t)length);\n"
                "        return 41;\n"
                "    }\n")
OLD_GOT = ("        !MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, actual_utf8, -1, actual, 32768)) return 41;\n")
NEW_GOT = ("        !MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, actual_utf8, -1, actual, 32768)) {\n"
           "        o->event(\"launch-install-directory-got\", got);   /* " + MARKER + " */\n"
           "        return 41;\n"
           "    }\n")
GOT_DECL = "    int32_t got = ((install_dir_fn)(*(void ***)manager)[5])(manager, appid, actual_utf8, sizeof(actual_utf8));\n"
LEN_DECL = "    DWORD length = GetEnvironmentVariableW(L\"MADEIRA_STEAM_HOST_EXPECTED_INSTALL\", expected, 32768);\n"


def patch(src):
    if MARKER in src:
        if src.count(NEW_EXPECTED) != 1 or src.count(NEW_GOT) != 1 or OLD_EXPECTED in src or OLD_GOT in src:
            raise ValueError("partial or changed install-dir-events patch")
        return src
    for name, old in (("the recorded folder's check", OLD_EXPECTED), ("the client's answer check", OLD_GOT),
                      ("the recorded folder's read", LEN_DECL), ("the client's install-folder call", GOT_DECL)):
        if src.count(old) != 1:
            raise ValueError(name + " of launch.c not found once (madeira-dock changed)")
    if not (src.index(LEN_DECL) < src.index(OLD_EXPECTED) < src.index(GOT_DECL) < src.index(OLD_GOT)):
        raise ValueError("the install-folder checks of launch.c are not in their order (madeira-dock changed)")
    return src.replace(OLD_EXPECTED, NEW_EXPECTED, 1).replace(OLD_GOT, NEW_GOT, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-install-dir-events: " + str(error))
    if result == source:
        print("patch-dock-install-dir-events: already in")
    else:
        path.write_text(result)
        print("madeira-dock: the silent install-folder failures (41) report which check failed")
