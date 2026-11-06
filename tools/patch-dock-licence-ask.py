#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 JesseLovelace (the C change that this script writes into madeira-dock: madeira-dock cf11b7c, "Ask for ownership soon after
#   sign-in instead of after a fixed 5 s", GPL-3.0-or-later with the Madeira Converter Exception)
# Copyright 2026 thebcd69 (BCD) (this script and the guard that keeps the first 5 s from failing a start)
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""Madeira Dock: ownership is asked 500 ms after the sign-in, not after a fixed 5 s (round 632, upstream v0.2.0 C17).

After Valve's client reports signed in, the Dock waited a fixed 5 s before it first asked whether the account owns the game (the
subscription list is the client's own answer). In 1,939 BCD Dock sessions the gap between "session-authenticated-online" and
"session-requested-app-entitled" is 5.0 s (98.6% between 4.5 and 6.5 s): a client that already had the licence list sat idle. The first
question is now asked 500 ms after the sign-in, and the list is polled every 250 ms until the old 5 s are over.

Before the old 5 s run out, only a yes is acted on: an early question that is not answered with "the app is in the subscription list" does
nothing (no failure, no probe line); "entitled but not in the list yet" (result 35 at 5 s), an unreadable list (36) and a failed allocation
would otherwise end the start earlier than they did, and the answer to them is still the one the loop gives from the old 5 s on. From 5 s after
the sign-in on, the loop is exactly what it was (same questions, same pace, same probe lines, same failures).

MADEIRA_DOCK_LICENCE_WAIT_MS sets the first question (0..30000 ms; anything else: 500). 5000 gives the old timing. Report field:
session-licence-answer-ms (ms from the sign-in to the answer; only for an answer before the old 5 s). The variable of that name in upstream's patch is called licence_ask_ms here: the
round 630 licence-wait patch (tools/patch-dock-licence-wait.py) has a `licence_wait` of its own in the same function.

Usage: patch-dock-licence-ask.py madeira-dock/src/session.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: licence-ask-v1"

DECL_OLD = ("    unsigned logged_callbacks = 0, online_callbacks = 0, blips = 0, lost = 0, connect_failures = 0;\n"
            "    result = 34;\n")
DECL_NEW = ("    unsigned logged_callbacks = 0, online_callbacks = 0, blips = 0, lost = 0, connect_failures = 0;\n"
            "    /* " + MARKER + " (tools/patch-dock-licence-ask.py; madeira-dock cf11b7c): how long after sign-in before ownership is\n"
            "     * first asked. It used to be a fixed 5 s in which a client that already had the licence list sat idle. Before the old\n"
            "     * 5 s only a yes is acted on, so asking early can fail nothing earlier than before. MADEIRA_DOCK_LICENCE_WAIT_MS sets\n"
            "     * it (0..30000); 5000 is the old timing. */\n"
            "    uint64_t licence_ask_ms = 500;\n"
            "    const char *ask_setting = getenv(\"MADEIRA_DOCK_LICENCE_WAIT_MS\");\n"
            "    if (ask_setting && *ask_setting) {\n"
            "        char *ask_end = NULL;\n"
            "        unsigned long ask_value = strtoul(ask_setting, &ask_end, 10);\n"
            "        if (ask_end && !*ask_end && ask_value <= 30000) licence_ask_ms = ask_value;\n"
            "    }\n"
            "    result = 34;\n")

ASK_OLD = ("        if (was_online && !offline_since && now - online_at >= 5000) {\n"
           "            bool entitled = ((subscribed_fn)v[181])(client_user, (uint32_t)app);\n"
           "            if (!entitled && now - last_probe >= 10000) {\n")
ASK_NEW = ("        if (was_online && !offline_since && now - online_at >= licence_ask_ms) {\n"
           "            /* " + MARKER + ": before the old fixed 5 s only a listed app is acted on (and nothing is probed or logged). */\n"
           "            bool early = now - online_at < 5000;\n"
           "            bool entitled = ((subscribed_fn)v[181])(client_user, (uint32_t)app);\n"
           "            if (!entitled && !early && now - last_probe >= 10000) {\n")

LIST_OLD = "            if (entitled || (use_list && now - last_list >= 1000)) {\n"
LIST_NEW = ("            bool ask_list = early ? ((entitled || use_list) && now - last_list >= 250)\n"
            "                                  : (entitled || (use_list && now - last_list >= 1000));\n"
            "            if (ask_list) {\n")

ALLOC_OLD = ("                uint32_t *apps = calloc(65536, sizeof(uint32_t));\n"
             "                if (!apps) { result = 36; break; }\n"
             "                int32_t count = ((subscriptions_fn)v[182])(client_user, apps, 65536, true);\n"
             "                bool listed = false;\n"
             "                if (count < 0 || count >= 65536) {\n"
             "                    free(apps); result = 36; break;\n"
             "                }\n")
ALLOC_NEW = ("                uint32_t *apps = calloc(65536, sizeof(uint32_t));\n"
             "                if (!apps) {\n"
             "                    if (!early) { result = 36; break; }\n"
             "                    last_list = now;   /* too early to tell: asked again */\n"
             "                    goto licence_later;\n"
             "                }\n"
             "                int32_t count = ((subscriptions_fn)v[182])(client_user, apps, 65536, true);\n"
             "                bool listed = false;\n"
             "                if (count < 0 || count >= 65536) {\n"
             "                    free(apps);\n"
             "                    if (!early) { result = 36; break; }\n"
             "                    last_list = now;   /* too early to tell: asked again */\n"
             "                    goto licence_later;\n"
             "                }\n")

YES_OLD = ("                if (entitled || listed) {\n"
           "                    o->event(\"session-requested-app-entitled\", entitled);\n")
YES_NEW = ("                if (entitled || listed) {\n"
           "                    /* " + MARKER + ": entitled but not in the list yet is no answer before the old 5 s (it ends the start with 35 after them). */\n"
           "                    if (early && !listed) goto licence_later;\n"
           "                    if (early) o->event(\"session-licence-answer-ms\", (int32_t)(now - online_at));\n"
           "                    o->event(\"session-requested-app-entitled\", entitled);\n")

TAIL_OLD = ("        o->sleep_ms(20);\n"
            "    }\n"
            "    if (blips) o->event(\"session-online-blips\", (int32_t)blips);\n")
TAIL_NEW = ("licence_later:\n"
            "        o->sleep_ms(20);\n"
            "    }\n"
            "    if (blips) o->event(\"session-online-blips\", (int32_t)blips);\n")

EDITS = ((DECL_OLD, DECL_NEW), (ASK_OLD, ASK_NEW), (LIST_OLD, LIST_NEW), (ALLOC_OLD, ALLOC_NEW), (YES_OLD, YES_NEW), (TAIL_OLD, TAIL_NEW))


def patch(src):
    if MARKER in src:
        if any(src.count(new) != 1 for _, new in EDITS):
            raise ValueError("partial or changed licence-ask patch")
        return src
    for old, _ in EDITS:
        if src.count(old) != 1:
            raise ValueError("an anchor of session.c not found once (madeira-dock changed): " + old.strip().splitlines()[0][:70])
    positions = [src.index(old) for old, _ in EDITS]
    if positions != sorted(positions):
        raise ValueError("the licence loop's parts are not in the order they were (madeira-dock changed)")
    if "licence_ask_ms" in src or "licence_later:" in src:
        raise ValueError("a name the patch adds is already in session.c (madeira-dock changed)")
    for old, new in EDITS:
        src = src.replace(old, new, 1)
    return src


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-licence-ask: " + str(error))
    if result == source:
        print("patch-dock-licence-ask: already in")
    else:
        path.write_text(result)
        print("madeira-dock: ownership is asked 500 ms after the sign-in (only a yes counts before the old 5 s)")
