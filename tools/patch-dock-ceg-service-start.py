#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 ft7t8drgwf-wq (the C code that this script writes into madeira-dock: Madeira build/dock-patches/0001, #519,
#   GPL-3.0-or-later with the Madeira Converter Exception)
# Copyright 2026 thebcd69 (BCD) (this script)
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""Madeira Dock: start Valve's registered Steam Client Service before the prepare request for custom executables (CEG).

A game whose depots flag custom executables (CheckGuid) needs Valve's client to prepare the per-account executables before
LaunchApp (madeira-dock/src/launch.c, upstream submodule; src/scm.c is the service side). Valve's client connects to its service's
pipe first and demand-starts the service only when that fails; under emulation the 32-bit service needs well over a second to start,
and the client's request had already failed by then: service_log.txt "Failed to create Service pipe (GLE 2)", then ceg-job-result=55
(RemoteCallFailed) while the service was still starting. This is the stop of 24 BCD logs of 12 Steam games (Call of Duty: Black Ops,
Spec Ops: The Line, Saints Row, Mafia II, ...; builds 574-630).

The host now starts the registered service through the service manager, as Valve's client would, and waits until it reports running
plus a 2 s settle for its pipe (bounded: 30 s, and a first start after a Steam update gets 20 s more for the updater's restart).
Nothing is created, registered, configured or patched: only Valve's own service is started, only for a start that asks for the
custom-executable preparation (MADEIRA_STEAM_HOST_CEG=1, set by the app for those games alone), and the result of the start never
fails the launch (the request that follows decides, as before). Report fields: ceg-service-start (0 running, a Win32 error, -1 it
stopped again) and ceg-service-start-retry. MADEIRA_DOCK_CEG_SERVICE_START=0 skips the start.

Usage: patch-dock-ceg-service-start.py madeira-dock/src/scm.c (idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: ceg-service-start-v1"

HEAD_OLD = (" * installer as Valve's client would. Dock itself never creates, registers,\n"
            " * configures or starts any service, and logs only numeric results.\n"
            " */\n")
HEAD_NEW = (" * installer as Valve's client would. Dock itself never creates, registers or\n"
            " * configures any service, starts only Valve's registered one through the\n"
            " * service manager (start_client_service), and logs only numeric results.\n"
            " */\n")

DEF_OLD = "#define SH_SERVICE_INSTALL_MS 60000\n"
DEF_NEW = (DEF_OLD +
           "#define SH_SERVICE_START_MS 30000ULL\n"
           "#define SH_SERVICE_SETTLE_MS 2000\n"
           "#define SH_SERVICE_UPDATE_GRACE_MS 20000ULL\n")

FUNC_OLD = ("}\n"
            "\n"
            "int32_t sh_ceg_scm_prepare(const struct sh_observer *o, HMODULE client)\n"
            "{\n")
FUNC_NEW = ("}\n"
            "\n"
            "/* " + MARKER + " (tools/patch-dock-ceg-service-start.py, Madeira build/dock-patches/0001) */\n"
            "static bool query_state(SC_HANDLE service, SERVICE_STATUS_PROCESS *status);\n"
            "\n"
            "/* Valve's client connects to its service's pipe first and demand-starts the\n"
            " * service only when that fails. On device the 32-bit service needs well over\n"
            " * a second to start under emulation, and the client's request had already\n"
            " * failed by then: service_log.txt \"Failed to create Service pipe (GLE 2)\",\n"
            " * then ceg-job-result=55 (RemoteCallFailed) while the service was still\n"
            " * starting. On a desktop the service is normally already running when a game\n"
            " * is launched, so it is started here through the service manager, as Valve's\n"
            " * client would start it, and the request waits until it reports running plus\n"
            " * a short settle for its pipe. Returns 0 (running), a Win32 error, or -1 when\n"
            " * it stopped again after one more start. MADEIRA_DOCK_CEG_SERVICE_START=0 skips this.\n"
            " */\n"
            "static int32_t start_client_service(const struct sh_observer *o, SC_HANDLE manager)\n"
            "{\n"
            "    SC_HANDLE service = OpenServiceW(manager, steam_service, SERVICE_START | SERVICE_QUERY_STATUS);\n"
            "    if (!service) return (int32_t)GetLastError();\n"
            "    SERVICE_STATUS_PROCESS status = {0};\n"
            "    int32_t result = 0;\n"
            "    bool restarted = false;\n"
            "    if (!query_state(service, &status) || status.dwCurrentState != SERVICE_RUNNING) {\n"
            "        /* 1053: the service process ended before it reached the manager. A\n"
            "         * first start after a Steam update does that: the service copies its\n"
            "         * new files, runs SteamServiceTmp.exe /Update and is restarted by it\n"
            "         * (seen on device), so the wait below still applies. */\n"
            "        uint64_t grace = 2000;\n"
            "        if (!StartServiceW(service, 0, NULL)) {\n"
            "            DWORD error = GetLastError();\n"
            "            if (error == ERROR_SERVICE_REQUEST_TIMEOUT) {\n"
            "                /* give the updater time to copy and restart it itself */\n"
            "                grace = SH_SERVICE_UPDATE_GRACE_MS;\n"
            "                o->event(\"ceg-service-start-retry\", (int32_t)error);\n"
            "            } else if (error != ERROR_SERVICE_ALREADY_RUNNING) result = (int32_t)error;\n"
            "        }\n"
            "        uint64_t begin = o->now_ms();\n"
            "        while (!result) {\n"
            "            if (query_state(service, &status)) {\n"
            "                if (status.dwCurrentState == SERVICE_RUNNING) break;\n"
            "                /* STOPPED right after the start is the manager not having\n"
            "                 * updated it yet; later it means the service ended. */\n"
            "                if (status.dwCurrentState == SERVICE_STOPPED && o->now_ms() - begin >= grace) {\n"
            "                    /* still stopped: one more start of our own */\n"
            "                    if (!restarted) {\n"
            "                        restarted = true;\n"
            "                        if (StartServiceW(service, 0, NULL) || GetLastError() == ERROR_SERVICE_ALREADY_RUNNING) {\n"
            "                            grace = 2000;\n"
            "                            begin = o->now_ms();\n"
            "                            continue;\n"
            "                        }\n"
            "                    }\n"
            "                    result = -1;\n"
            "                    break;\n"
            "                }\n"
            "            }\n"
            "            if (!sh_remaining_ms(o->now_ms(), begin, SH_SERVICE_START_MS)) {\n"
            "                result = ERROR_TIMEOUT;\n"
            "                break;\n"
            "            }\n"
            "            o->sleep_ms(100);\n"
            "        }\n"
            "        if (!result) o->sleep_ms(SH_SERVICE_SETTLE_MS);\n"
            "    }\n"
            "    CloseServiceHandle(service);\n"
            "    return result;\n"
            "}\n"
            "\n"
            "int32_t sh_ceg_scm_prepare(const struct sh_observer *o, HMODULE client)\n"
            "{\n")

CALL_OLD = ("    CloseServiceHandle(manager);\n"
            "    return sh_ceg_scm_result(true, registered);\n")
CALL_NEW = ("    if (registered && !wide_flag(L\"MADEIRA_DOCK_CEG_SERVICE_START\", L'0'))\n"
            "        o->event(\"ceg-service-start\", start_client_service(o, manager));\n"
            + CALL_OLD)

EDITS = ((HEAD_OLD, HEAD_NEW), (DEF_OLD, DEF_NEW), (FUNC_OLD, FUNC_NEW), (CALL_OLD, CALL_NEW))


def patch(src):
    if MARKER in src:
        if any(src.count(new) != 1 for _, new in EDITS):
            raise ValueError("partial or changed ceg-service-start patch")
        return src
    for old, _ in EDITS:
        if src.count(old) != 1:
            raise ValueError("an anchor of scm.c not found once (madeira-dock changed): " + old.strip().splitlines()[0][:60])
    for needed in ("static bool query_state(SC_HANDLE service, SERVICE_STATUS_PROCESS *status)\n{\n",
                   "static const wchar_t steam_service[] = L\"Steam Client Service\";\n",
                   "static bool wide_flag(const wchar_t *name, wchar_t expected)\n"):
        if src.count(needed) != 1:
            raise ValueError("a helper the patch uses is not where it was (madeira-dock changed): " + needed.strip().splitlines()[0][:60])
    if not src.index("static bool wide_flag(") < src.index(FUNC_OLD):
        raise ValueError("wide_flag is not defined before sh_ceg_scm_prepare (madeira-dock changed)")
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
        sys.exit("patch-dock-ceg-service-start: " + str(error))
    if result == source:
        print("patch-dock-ceg-service-start: already in")
    else:
        path.write_text(result)
        print("madeira-dock: Valve's registered Steam Client Service is started before the custom-executable request")
