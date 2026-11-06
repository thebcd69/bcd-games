#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 ft7t8drgwf-wq (the C code that this script writes into madeira-dock: Madeira build/dock-patches/0002, #519,
#   GPL-3.0-or-later with the Madeira Converter Exception)
# Copyright 2026 thebcd69 (BCD) (this script)
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""Madeira Dock: a prepare request for custom executables (CEG) that failed with 55 is sent again, up to 3 times, 1.5 s apart.

Valve's client answers 55 (RemoteCallFailed) as job result when it found no running service, demand-started it and failed the request
before the emulated service was up; the service may also have ended between an earlier start and the request. Its next request finds
the service running. The retry applies only to a request that already ended in a failure with exactly that code (the 62 report lines
of 24 BCD logs: "ceg-job-result=55", "ceg-result=55"); every other answer, a busy client, the 5-minute wait and a request that
succeeded are untouched. Report field: ceg-request-retry (the retry number).

Usage: patch-dock-ceg-retry.py madeira-dock/src/launch.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: ceg-retry-v1"

DEF_OLD = ("#define SH_CEG_WAIT_MS (5ULL * 60 * 1000)\n"
           "#define SH_CEG_BUSY_RETRY_MS 10000\n")
DEF_NEW = (DEF_OLD +
           "#define SH_CEG_REMOTE_CALL_FAILED 55\n"
           "#define SH_CEG_REMOTE_RETRIES 3\n"
           "#define SH_CEG_REMOTE_RETRY_MS 1500\n")

VAR_OLD = "    unsigned busy = 0, logged_jobs = 0;\n"
VAR_NEW = "    unsigned busy = 0, logged_jobs = 0, remote_retries = 0;\n"

LOOP_OLD = ("            if (step == SH_CEG_FAIL) {\n"
            "                o->event(\"ceg-finished-jobs\", (int32_t)progress.finished);\n"
            "                o->event(\"ceg-result\", progress.failure);\n"
            "                return 49;\n"
            "            }\n")
LOOP_NEW = ("            if (step == SH_CEG_FAIL && progress.failure == SH_CEG_REMOTE_CALL_FAILED &&\n"
            "                remote_retries < SH_CEG_REMOTE_RETRIES) {\n"
            "                /* " + MARKER + " (tools/patch-dock-ceg-retry.py, Madeira build/dock-patches/0002).\n"
            "                 * 55 (RemoteCallFailed): Valve's client found no running\n"
            "                 * service, demand-started it and failed this request before\n"
            "                 * the emulated service was up; the service may also have\n"
            "                 * ended between an earlier start and the request. Its next\n"
            "                 * request finds it running. */\n"
            "                o->event(\"ceg-request-retry\", ++remote_retries);\n"
            "                progress = (struct sh_ceg_progress){0};\n"
            "                waiting = false;\n"
            "                retry_at = o->now_ms() + SH_CEG_REMOTE_RETRY_MS;\n"
            "                continue;\n"
            "            }\n"
            + LOOP_OLD)

EDITS = ((DEF_OLD, DEF_NEW), (VAR_OLD, VAR_NEW), (LOOP_OLD, LOOP_NEW))
FUNC = "static int prepare_custom_binaries(HMODULE module, void *client_user,\n"


def patch(src):
    if MARKER in src:
        if any(src.count(new) != 1 for _, new in EDITS):
            raise ValueError("partial or changed ceg-retry patch")
        return src
    for old, _ in EDITS:
        if src.count(old) != 1:
            raise ValueError("an anchor of launch.c not found once (madeira-dock changed): " + old.strip().splitlines()[0][:60])
    if src.count(FUNC) != 1 or not (src.index(FUNC) < src.index(VAR_OLD) < src.index(LOOP_OLD)):
        raise ValueError("prepare_custom_binaries is not where it was (madeira-dock changed)")
    body = src[src.index(FUNC):src.index(LOOP_OLD)]
    for needed in ("struct sh_ceg_progress progress = {0};", "bool waiting = false;", "retry_at = begin;",
                   "while (!InterlockedCompareExchange(&interrupted, 0, 0)) {"):
        if needed not in body:
            raise ValueError("the request loop's state is not where it was (madeira-dock changed): " + needed)
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
        sys.exit("patch-dock-ceg-retry: " + str(error))
    if result == source:
        print("patch-dock-ceg-retry: already in")
    else:
        path.write_text(result)
        print("madeira-dock: a custom-executable request that failed with 55 is sent again (3 times, 1.5 s apart)")
