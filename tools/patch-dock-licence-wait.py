#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: a sign-in that came late still gets time for the licence list (round 630, agent U S3).

The Dock's session (madeira-dock/src/session.c, upstream submodule) waits 90 s from the logon start for Valve's client to sign in and
then to list the requested app among the account's licences, and ends with result 34 when the time is up. BCD Dock start 444TS8
(build 626): the sign-in itself took 64 s (session-logon-start-result at 03:03:14, session-authenticated-online at 03:04:18), the
licence list had 26 s left and had not come (session-timeout-subscription-count=0), so the start ended with code 34 although the client
was signed in.

The 90 s stay the minimum. When the first sign-in comes, the wait is moved to 45 s after it if that is later, and never past 180 s from
the logon start; one "session-licence-wait-ms" report field says so. A start that signs in within 45 s (all the healthy ones: 1 to 6 s)
keeps exactly the 90 s, and a start whose licence list arrives ends the loop as before; only a late sign-in waits longer, up to 45 s
more. MADEIRA_DOCK_LICENCE_WAIT=0: the 90 s as before.

Usage: patch-dock-licence-wait.py madeira-dock/src/session.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: licence-wait-v1"
LOOP_OLD = "    for (unsigned tick = 0; tick < 4500 && o->now_ms() - begin < 90000; ++tick) {\n"
LOOP_NEW = (
    "    /* " + MARKER + " (tools/patch-dock-licence-wait.py, round 630, 444TS8): the 90 s count from the logon start; a sign-in\n"
    "     * that came late gets 45 s after it for the licence list, never past 180 s from the logon start. MADEIRA_DOCK_LICENCE_WAIT=0:\n"
    "     * the 90 s as before. */\n"
    "    const char *licence_wait = getenv(\"MADEIRA_DOCK_LICENCE_WAIT\");\n"
    "    uint64_t deadline = begin + 90000;\n"
    "    bool deadline_moved = licence_wait && !strcmp(licence_wait, \"0\");\n"
    "    for (unsigned tick = 0; tick < 9000 && o->now_ms() < deadline; ++tick) {\n"
)
ONLINE_OLD = ("            if (!was_online) {\n"
              "                was_online = true;\n"
              "                online_at = now;\n"
              "                o->event(\"session-authenticated-online\", 1);\n"
              "            }\n")
ONLINE_NEW = ("            if (!was_online) {\n"
              "                was_online = true;\n"
              "                online_at = now;\n"
              "                o->event(\"session-authenticated-online\", 1);\n"
              "                if (!deadline_moved) {   /* " + MARKER + ": the first sign-in only */\n"
              "                    uint64_t want = now + 45000, cap = begin + 180000;\n"
              "\n"
              "                    deadline_moved = true;\n"
              "                    if (want > deadline) {\n"
              "                        deadline = want < cap ? want : cap;\n"
              "                        o->event(\"session-licence-wait-ms\", (int32_t)(deadline - begin));\n"
              "                    }\n"
              "                }\n"
              "            }\n")


def patch(src):
    if MARKER in src:
        if src.count(LOOP_NEW) != 1 or src.count(ONLINE_NEW) != 1 or LOOP_OLD in src:
            raise ValueError("partial or changed licence-wait patch")
        return src
    if src.count(LOOP_OLD) != 1:
        raise ValueError("the 90 s licence loop of session.c not found once (madeira-dock changed)")
    loop = src.index(LOOP_OLD)
    if src.count(ONLINE_OLD) != 1 or src.index(ONLINE_OLD) < loop:
        raise ValueError("the first sign-in branch inside the licence loop not found once (madeira-dock changed)")
    if "uint64_t begin = o->now_ms(), online_at = 0" not in src[:loop]:
        raise ValueError("the loop's begin and online_at are not where they were (madeira-dock changed)")
    return src.replace(LOOP_OLD, LOOP_NEW, 1).replace(ONLINE_OLD, ONLINE_NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-licence-wait: " + str(error))
    if result == source:
        print("patch-dock-licence-wait: already in")
    else:
        path.write_text(result)
        print("madeira-dock: a late sign-in gets 45 s for the licence list (at most 180 s from the logon start)")
