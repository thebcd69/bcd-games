#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: look at Valve's client service once more after its 2 s settle, and start it again when it died (night round 2026-11-11, G2 S3).

tools/patch-dock-ceg-service-start.py (round 632) starts Valve's registered Steam Client Service before the request for the per-account program (CEG), waits
until it reports running, sleeps 2 s for its pipe and goes on. R0GHX7 (Bionic Commando), HJZJ1P, 7XG9VK and WFRZ8A (Mafia II, build 634) show the service
reported running and then dying inside or right after the settle, while the host logged ceg-service-start=0 and asked a dead service for the job (result 55
or 3, a minute later). Now, after the settle, the state is read once more: "ceg-service-alive-after-settle" (1 running, 0 gone); a service that is gone is
started again, up to three rounds, each with the same wait and settle, and "ceg-service-restarts" says how many rounds ran; when it is still not running the
start reports -1 (the request that follows is made as before: the result of this step never fails the launch). A service that stays up behaves exactly as
before (one query more). MADEIRA_DOCK_CEG_SERVICE_RESTART=0 leaves the first reading as it was.

Must run after tools/patch-dock-ceg-service-start.py. Usage: patch-dock-ceg-service-restart.py madeira-dock/src/scm.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: ceg-service-restart-v1"
OLD = ("        if (!result) o->sleep_ms(SH_SERVICE_SETTLE_MS);\n"
       "    }\n"
       "    CloseServiceHandle(service);\n"
       "    return result;\n")
NEW = ("        if (!result) o->sleep_ms(SH_SERVICE_SETTLE_MS);\n"
       "        /* " + MARKER + " (tools/patch-dock-ceg-service-restart.py, night round 2026-11-11): is it still running after the settle? */\n"
       "        if (!result && !wide_flag(L\"MADEIRA_DOCK_CEG_SERVICE_RESTART\", L'0')) {\n"
       "            int32_t restarts = 0;\n"
       "            for (int round = 0; round < 3; ++round) {\n"
       "                bool alive = query_state(service, &status) && status.dwCurrentState == SERVICE_RUNNING;\n"
       "                if (!round) o->event(\"ceg-service-alive-after-settle\", alive);\n"
       "                if (alive) break;\n"
       "                ++restarts;\n"
       "                if (!StartServiceW(service, 0, NULL) && GetLastError() != ERROR_SERVICE_ALREADY_RUNNING) {\n"
       "                    result = (int32_t)GetLastError();\n"
       "                    break;\n"
       "                }\n"
       "                uint64_t again = o->now_ms();\n"
       "                while (sh_remaining_ms(o->now_ms(), again, SH_SERVICE_START_MS)) {\n"
       "                    if (query_state(service, &status) && status.dwCurrentState == SERVICE_RUNNING) break;\n"
       "                    o->sleep_ms(100);\n"
       "                }\n"
       "                o->sleep_ms(SH_SERVICE_SETTLE_MS);\n"
       "            }\n"
       "            if (restarts) {\n"
       "                o->event(\"ceg-service-restarts\", restarts);\n"
       "                if (!result && !(query_state(service, &status) && status.dwCurrentState == SERVICE_RUNNING)) result = -1;\n"
       "            }\n"
       "        }\n"
       "    }\n"
       "    CloseServiceHandle(service);\n"
       "    return result;\n")


def patch(src):
    if MARKER in src:
        if src.count(NEW) != 1:
            raise ValueError("partial or changed ceg-service-restart patch")
        return src
    if "madeira-bcd: ceg-service-start-v1" not in src:
        raise ValueError("tools/patch-dock-ceg-service-start.py has not run (its marker is missing)")
    if src.count(OLD) != 1:
        raise ValueError("the end of start_client_service in scm.c not found once (madeira-dock or patch-dock-ceg-service-start.py changed)")
    return src.replace(OLD, NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-ceg-service-restart: " + str(error))
    if result == source:
        print("patch-dock-ceg-service-restart: already in")
    else:
        path.write_text(result)
        print("madeira-dock: Valve's client service is looked at once more after its settle and started again when it died")
