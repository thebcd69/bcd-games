#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: a game whose prepare request Valve's client accepts with zero jobs may go on to Valve's own launch, keyed (round 630,
agent A1 S14).

For a game whose depots flag custom executables, the Dock (madeira-dock/src/launch.c, upstream submodule) asks Valve's client to prepare
them before LaunchApp and starts the game only when every job it started reported success. BCD Dock start FRKBN1 (Borderlands: The
Pre-Sequel, build 626): the client accepted the request (ceg-request-result=1) and said it started no job (ceg-request=0); the Dock
counted that as a failure (ceg-result=0) and ended with host result 49, so the game never launched. Why the client had nothing to
prepare is not in the log. With MADEIRA_DOCK_CEG_ZERO_JOBS=1 (exactly "1"; off by default) an accepted request with zero jobs goes on to
the launch as a finished preparation would ("ceg-zero-jobs-launch=1", then "ceg-result=1"), and Valve's LaunchApp decides as usual: a
program that is not ready ends with the game's own error. Nothing of the preparation itself is touched; every other answer (a refusal, a
busy client, too many jobs, a failed job, the wait) is unchanged, and so is the key unset or any other value.

Usage: patch-dock-ceg-zero-jobs.py madeira-dock/src/launch.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: ceg-zero-jobs-v1"
OLD = ("                o->event(\"ceg-request-result\", status);\n"
       "                o->event(\"ceg-request\", jobs == UINT32_MAX ? -1 : (int32_t)jobs);\n"
       "                if (step != SH_CEG_WAIT) {\n"
       "                    o->event(\"ceg-result\", progress.failure);\n"
       "                    return 49;\n"
       "                }\n")
NEW = ("                o->event(\"ceg-request-result\", status);\n"
       "                o->event(\"ceg-request\", jobs == UINT32_MAX ? -1 : (int32_t)jobs);\n"
       "                if (step != SH_CEG_WAIT) {\n"
       "                    /* " + MARKER + " (tools/patch-dock-ceg-zero-jobs.py, round 630, FRKBN1): the client accepted the\n"
       "                     * request and started no job. MADEIRA_DOCK_CEG_ZERO_JOBS=1 (off by default) goes on to Valve's own\n"
       "                     * launch, which decides; every other answer fails as before. */\n"
       "                    if (status == 1 && jobs == 0 && wide_flag(L\"MADEIRA_DOCK_CEG_ZERO_JOBS\", L'1')) {\n"
       "                        o->event(\"ceg-zero-jobs-launch\", 1);\n"
       "                        o->event(\"ceg-result\", 1);\n"
       "                        return 0;\n"
       "                    }\n"
       "                    o->event(\"ceg-result\", progress.failure);\n"
       "                    return 49;\n"
       "                }\n")
FLAG = "static bool wide_flag(const wchar_t *name, wchar_t expected)\n"
FUNC = "static int prepare_custom_binaries(HMODULE module, void *client_user,\n"


def patch(src):
    if MARKER in src:
        if src.count(NEW) != 1 or src.count(OLD) != 0:
            raise ValueError("partial or changed ceg-zero-jobs patch")
        return src
    if src.count(OLD) != 1:
        raise ValueError("the prepare request's failure branch of launch.c not found once (madeira-dock changed)")
    if src.count(FLAG) != 1 or src.count(FUNC) != 1 or not (src.index(FLAG) < src.index(FUNC) < src.index(OLD)):
        raise ValueError("wide_flag or prepare_custom_binaries is not where it was (madeira-dock changed)")
    body = src[src.index(FUNC):src.index(OLD)]
    if "uint32_t jobs = UINT32_MAX;" not in body or "int32_t status = " not in body:
        raise ValueError("the request's status and jobs are not in scope where they were (madeira-dock changed)")
    return src.replace(OLD, NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-ceg-zero-jobs: " + str(error))
    if result == source:
        print("patch-dock-ceg-zero-jobs: already in")
    else:
        path.write_text(result)
        print("madeira-dock: MADEIRA_DOCK_CEG_ZERO_JOBS=1 lets an accepted prepare request with zero jobs go on to the launch")
