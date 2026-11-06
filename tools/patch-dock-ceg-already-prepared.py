#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 ft7t8drgwf-wq (the C code that this script writes into madeira-dock: Madeira build/dock-patches/0003, #519,
#   GPL-3.0-or-later with the Madeira Converter Exception)
# Copyright 2026 thebcd69 (BCD) (this script)
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""Madeira Dock: a prepare request for custom executables (CEG) that Valve's client answers OK with no job means "already prepared".

Valve's client answers OK and starts no job once it has produced this account's executables (every request after the first successful
one, until the files are replaced again). Dock counted that as a failure (ceg-request=0, ceg-result=0, host result 49) and the game
never launched: BCD Dock start FRKBN1 (Borderlands: The Pre-Sequel, build 626), and the same case upstream saw as "Black Ops stopped
with code 49 until Repair". The host now goes on to Valve's own LaunchApp (report field ceg-already-prepared=1, then ceg-result=1),
which decides as usual: nothing here makes, patches or fakes an executable, and a program that is not ready ends with the game's own
check. Only a FIRST request is trusted this way: after a request that was retried (tools/patch-dock-ceg-retry.py) the files were not
prepared, so an OK with no job still fails closed (49, ceg-result=0). An unset job count (the client wrote none) still fails.

Needs tools/patch-dock-ceg-retry.py first (remote_retries). Replaces tools/patch-dock-ceg-zero-jobs.py (the keyed experiment for the
same case); the two cannot both apply, the retired one's anchor sits where this one inserts.

Usage: patch-dock-ceg-already-prepared.py madeira-dock/src/launch.c madeira-dock/src/validation.c
       (idempotent; both files or none; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: ceg-already-prepared-v1"

L_DOC_OLD = ("/* Returns 0 once every started job reported success, otherwise 49 (or 12\n"
             " * for a malformed callback). Only numeric results are reported: no paths.\n"
             " */\n")
L_DOC_NEW = ("/* Returns 0 once every started job reported success, or at once when the\n"
             " * first request started none (already prepared), otherwise 49 (or 12 for a\n"
             " * malformed callback). Only numeric results are reported: no paths.\n"
             " */\n")
L_REQ_OLD = ("                o->event(\"ceg-request\", jobs == UINT32_MAX ? -1 : (int32_t)jobs);\n"
             "                if (step != SH_CEG_WAIT) {\n")
L_REQ_NEW = ("                o->event(\"ceg-request\", jobs == UINT32_MAX ? -1 : (int32_t)jobs);\n"
             "                /* " + MARKER + " (tools/patch-dock-ceg-already-prepared.py, Madeira\n"
             "                 * build/dock-patches/0003). Already prepared (OK, no job started). Trusted only from a\n"
             "                 * first request: after a failed job (the retry below) the\n"
             "                 * files were not prepared, so no job then still fails. */\n"
             "                if (step == SH_CEG_DONE && !remote_retries) {\n"
             "                    o->event(\"ceg-already-prepared\", 1);\n"
             "                    o->event(\"ceg-result\", 1);\n"
             "                    return 0;\n"
             "                }\n"
             "                if (step != SH_CEG_WAIT) {\n")
L_EDITS = ((L_DOC_OLD, L_DOC_NEW), (L_REQ_OLD, L_REQ_NEW))

V_OLD = ("    if (result != 1 || !jobs || jobs > SH_CEG_MAX_JOBS) {\n"
         "        progress->failure = result == 1 ? 0 : result;\n"
         "        return SH_CEG_FAIL;\n"
         "    }\n"
         "    *progress = (struct sh_ceg_progress){jobs, 0, 0};\n"
         "    return SH_CEG_WAIT;\n"
         "}\n")
V_NEW = ("    if (result != 1 || jobs > SH_CEG_MAX_JOBS) {\n"
         "        progress->failure = result == 1 ? 0 : result;\n"
         "        return SH_CEG_FAIL;\n"
         "    }\n"
         "    *progress = (struct sh_ceg_progress){jobs, 0, 0};\n"
         "    /* " + MARKER + " (tools/patch-dock-ceg-already-prepared.py, Madeira build/dock-patches/0003).\n"
         "     * OK with no job started: Valve's client found nothing left to prepare.\n"
         "     * It answers so once it has produced this account's executables (every\n"
         "     * request after the first successful one, until the files are replaced\n"
         "     * again); the game's own check of them is untouched. An unset count\n"
         "     * (UINT32_MAX) still fails above. */\n"
         "    return jobs ? SH_CEG_WAIT : SH_CEG_DONE;\n"
         "}\n")
RETRY_MARKER = "madeira-bcd: ceg-retry-v1"
FUNC = "static int prepare_custom_binaries(HMODULE module, void *client_user,\n"


def patch_launch(src):
    if MARKER in src:
        if any(src.count(new) != 1 for _, new in L_EDITS):
            raise ValueError("partial or changed ceg-already-prepared patch in launch.c")
        return src
    for old, _ in L_EDITS:
        if src.count(old) != 1:
            raise ValueError("an anchor of launch.c not found once (madeira-dock changed): " + old.strip().splitlines()[0][:60])
    if RETRY_MARKER not in src or "unsigned busy = 0, logged_jobs = 0, remote_retries = 0;\n" not in src:
        raise ValueError("tools/patch-dock-ceg-retry.py has not been applied (remote_retries is missing)")
    if "madeira-bcd: ceg-zero-jobs-v1" in src:
        raise ValueError("tools/patch-dock-ceg-zero-jobs.py is in launch.c (retired: it and this patch cannot both apply)")
    if src.count(FUNC) != 1 or not (src.index(FUNC) < src.index(L_REQ_OLD)):
        raise ValueError("prepare_custom_binaries is not where it was (madeira-dock changed)")
    body = src[src.index(FUNC):src.index(L_REQ_OLD)]
    if "enum sh_ceg_step step = sh_ceg_request_step(status, jobs, &progress);" not in body or "uint32_t jobs = UINT32_MAX;" not in body:
        raise ValueError("the request's step and jobs are not in scope where they were (madeira-dock changed)")
    for old, new in L_EDITS:
        src = src.replace(old, new, 1)
    return src


def patch_validation(src):
    if MARKER in src:
        if src.count(V_NEW) != 1:
            raise ValueError("partial or changed ceg-already-prepared patch in validation.c")
        return src
    if src.count(V_OLD) != 1:
        raise ValueError("sh_ceg_request_step of validation.c not found once (madeira-dock changed)")
    return src.replace(V_OLD, V_NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-2].strip())
    launch_path, validation_path = Path(sys.argv[1]), Path(sys.argv[2])
    launch_source, validation_source = launch_path.read_text(), validation_path.read_text()
    try:
        launch_result = patch_launch(launch_source)
        validation_result = patch_validation(validation_source)
    except ValueError as error:
        sys.exit("patch-dock-ceg-already-prepared: " + str(error))
    if launch_result == launch_source and validation_result == validation_source:
        print("patch-dock-ceg-already-prepared: already in")
    elif (launch_result == launch_source) != (validation_result == validation_source):
        sys.exit("patch-dock-ceg-already-prepared: only one of launch.c and validation.c has the patch")
    else:
        launch_path.write_text(launch_result)
        validation_path.write_text(validation_result)
        print("madeira-dock: an OK request with no job started (already prepared) goes on to Valve's launch")
