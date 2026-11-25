#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: when Valve's client service could not be kept running in a service manager this host started, start one fresh services.exe and try once more
(round 714, agent F1 S3; Steam CEG class A: Mafia II 066P0C, CSP7E6, CT98X6, NS4R70, EYCX54, PunkBuster PKTC7C).

In class A the service's first RPC connection to services.exe loses its server thread (RPC_S_CALL_FAILED 1726), the reconnect has no context handle, the
service ends and the host's start reports -1 after its restart rounds (tools/patch-dock-ceg-service-restart.py); the CEG request then fails with 55 and the
start ends in 49. Within a session the class is stable; between sessions on one phone it flips, and the only state of a session the Dock host owns is the
services.exe it started. The cause is not proved, so this is an experiment, and it runs only where the start has already failed: after
"ceg-service-start" = -1, and only when this host started the manager, it ends that services.exe, starts a fresh one ("ceg-scm-restarted" = 1), checks the
registration and starts the service once more ("ceg-service-start-after-restart", the same codes). A fresh manager that does not answer reports
"ceg-scm-restart-error" and ends the step as a missing manager does. It costs one manager start and one service start (about 25 s) on a start that fails
today. MADEIRA_DOCK_CEG_SCM_RESTART=0 leaves the failure as it was.

Must run after tools/patch-dock-ceg-service-start.py and tools/patch-dock-ceg-service-restart.py.
Usage: patch-dock-ceg-scm-restart.py madeira-dock/src/scm.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: ceg-scm-restart-v1"
OLD = ("    if (registered && !wide_flag(L\"MADEIRA_DOCK_CEG_SERVICE_START\", L'0'))\n"
       "        o->event(\"ceg-service-start\", start_client_service(o, manager));\n"
       "    CloseServiceHandle(manager);\n"
       "    return sh_ceg_scm_result(true, registered);\n")
NEW = ("    if (registered && !wide_flag(L\"MADEIRA_DOCK_CEG_SERVICE_START\", L'0')) {\n"
       "        int32_t started = start_client_service(o, manager);\n"
       "        o->event(\"ceg-service-start\", started);\n"
       "        /* " + MARKER + " (tools/patch-dock-ceg-scm-restart.py, round 714): the service could not be kept running in the manager this host\n"
       "         * started: one fresh services.exe, then the service once more. */\n"
       "        if (started == -1 && manager_process && !wide_flag(L\"MADEIRA_DOCK_CEG_SCM_RESTART\", L'0')) {\n"
       "            CloseServiceHandle(manager);\n"
       "            manager = NULL;\n"
       "            if (WaitForSingleObject(manager_process, 0) != WAIT_OBJECT_0 && TerminateProcess(manager_process, 0))\n"
       "                WaitForSingleObject(manager_process, SH_PROCESS_EXIT_MS);\n"
       "            CloseHandle(manager_process);\n"
       "            manager_process = NULL;\n"
       "            o->sleep_ms(500);\n"
       "            DWORD again = start_manager(o, &manager, \"ceg-scm-restarted\");\n"
       "            if (again || !manager) {\n"
       "                o->event(\"ceg-scm-restart-error\", (int32_t)(again ? again : ERROR_SERVICE_NOT_ACTIVE));\n"
       "                return sh_ceg_scm_result(false, false);\n"
       "            }\n"
       "            registered = service_registered(o, manager, &missing);\n"
       "            if (registered) o->event(\"ceg-service-start-after-restart\", start_client_service(o, manager));\n"
       "        }\n"
       "    }\n"
       "    CloseServiceHandle(manager);\n"
       "    return sh_ceg_scm_result(true, registered);\n")


def patch(src):
    if MARKER in src:
        if src.count(NEW) != 1:
            raise ValueError("partial or changed ceg-scm-restart patch")
        return src
    for need, tool in (("madeira-bcd: ceg-service-start-v1", "patch-dock-ceg-service-start.py"),
                       ("madeira-bcd: ceg-service-restart-v1", "patch-dock-ceg-service-restart.py")):
        if need not in src:
            raise ValueError("tools/%s has not run (its marker is missing)" % tool)
    if src.count(OLD) != 1:
        raise ValueError("the service start at the end of sh_ceg_scm_prepare in scm.c not found once (madeira-dock or patch-dock-ceg-service-start.py changed)")
    if src.count("static HANDLE manager_process;") != 1 or src.count("static DWORD start_manager(") != 1:
        raise ValueError("manager_process or start_manager in scm.c not found once (madeira-dock changed)")
    return src.replace(OLD, NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-ceg-scm-restart: " + str(error))
    if result == source:
        print("patch-dock-ceg-scm-restart: already in")
    else:
        path.write_text(result)
        print("madeira-dock: a service that could not be kept running gets one fresh services.exe and one more start")
