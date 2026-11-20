#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: a Steam game's Launch arguments reach the game, as Steam's own Launch Options do (2026-11-20).

dockhost.exe asks Valve's client to start the game with IClientAppManager::LaunchApp(gameid, launch option, 0, user arguments); the
host always passed "" as the user arguments (its CLIENT_LAYOUTS.md named MADEIRA_STEAM_HOST_ARGUMENTS for them, never wired). Now
both calls (the first and the retry after an update) pass MADEIRA_STEAM_HOST_ARGUMENTS, which the app sets from the game's Launch
arguments and removes when there are none: with no variable the call is exactly as before. Report field: launch-user-args-length
(the length only; a longer text than the host's 4 KB buffer is dropped and reported as -1).

Usage: patch-dock-user-args.py madeira-dock/src/launch.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: user-args-v1"

READ_OLD = '    o->event("launch-option-index", (int32_t)launch_option);\n'
READ_NEW = (READ_OLD +
            "    /* " + MARKER + " (tools/patch-dock-user-args.py): the game's Launch arguments, Valve's pszUserArgs;\n"
            "     * no variable: \"\" as before. */\n"
            "    static char user_args[4096];\n"
            "    user_args[0] = 0;\n"
            "    {\n"
            '        DWORD args_length = GetEnvironmentVariableA("MADEIRA_STEAM_HOST_ARGUMENTS", user_args, sizeof(user_args));\n'
            "        if (args_length >= sizeof(user_args)) { user_args[0] = 0; o->event(\"launch-user-args-length\", -1); }\n"
            '        else if (args_length) o->event("launch-user-args-length", (int32_t)args_length);\n'
            "    }\n")
CALL_OLD = "((launch_fn)(*(void ***)manager)[2])(manager, &gameid, launch_option, 0, \"\");"
CALL_NEW = "((launch_fn)(*(void ***)manager)[2])(manager, &gameid, launch_option, 0, user_args);"


def patch(src):
    if MARKER in src:
        if src.count(READ_NEW) != 1 or src.count(CALL_NEW) != 2 or CALL_OLD in src:
            raise ValueError("partial or changed user-args patch")
        return src
    if src.count(READ_OLD) != 1:
        raise ValueError("the launch option's report line is not there once (madeira-dock changed)")
    if src.count(CALL_OLD) != 2:
        raise ValueError(f"LaunchApp is called {src.count(CALL_OLD)} times with \"\" (expected 2: the start and the retry)")
    if not src.index(READ_OLD) < src.index(CALL_OLD):
        raise ValueError("the launch option is read after LaunchApp (madeira-dock changed)")
    return src.replace(READ_OLD, READ_NEW, 1).replace(CALL_OLD, CALL_NEW)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-user-args: " + str(error))
    if result == source:
        print("patch-dock-user-args: already in")
    else:
        path.write_text(result)
        print("madeira-dock: a Steam game's Launch arguments go to Valve's LaunchApp (MADEIRA_STEAM_HOST_ARGUMENTS)")
