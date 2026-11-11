#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: MADEIRA_DOCK_KEEP_ALIVE = * holds the session while a program of the game's own install folder runs (night round 2026-11-11, X2 S2).

tools/patch-dock-keep-alive.py (round 3 of October) holds the end of a Dock session while processes of NAMED images exist. Control's starter
(Control.exe) starts Control_DX11.exe and ends; Valve's client stops reporting the app as running, the host ends the session 3 s later and puts the Steam
entries back (pid 0, ActiveUser 0) 15 s before the real game's SteamAPI_Init (G2CRAR). With the list "*" the session is held while any process whose image
lies under the game's install folder (MADEIRA_STEAM_HOST_EXPECTED_INSTALL, the folder the host verified) is alive, except resident helpers whose file name
holds service, crash, report, easyanticheat, unins (uninstallers), setup or redist (they must not hold the host for ever). Off unless the key is set (default: no list, no
change); an exe list works as before. Needs tools/patch-dock-keep-alive.py first. Usage: patch-dock-keep-alive-tree.py madeira-dock/src/launch.c
(idempotent; fails by name when an anchor moved)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: keep-alive-tree-v1"
INC_OLD = "#include <tlhelp32.h>\n"
INC_NEW = "#include <tlhelp32.h>\n#include <wctype.h>\n"
FN_OLD = ("/* madeira-bcd: MADEIRA_DOCK_KEEP_ALIVE, see tools/patch-dock-keep-alive.py. */\n"
          "static bool keep_alive_running(void)\n")
HELPERS = r'''/* madeira-bcd: keep-alive-tree-v1 (tools/patch-dock-keep-alive-tree.py, night round 2026-11-11, G2CRAR): MADEIRA_DOCK_KEEP_ALIVE = * holds the session while a
 * program of the game's install folder runs. A resident service, crash reporter, anti-cheat or installer of the folder must not hold the host for ever. */
static bool keep_alive_name_denied(const wchar_t *exe)
{
    static const wchar_t *const words[] = { L"service", L"crash", L"report", L"easyanticheat", L"unins", L"setup", L"redist" };
    wchar_t low[260];
    size_t i;
    for (i = 0; exe[i] && i < 259; ++i) low[i] = (wchar_t)towlower(exe[i]);
    low[i] = 0;
    for (i = 0; i < sizeof(words) / sizeof(words[0]); ++i) if (wcsstr(low, words[i])) return true;
    return false;
}
static bool install_tree_running(void)
{
    static wchar_t root[1024];
    DWORD length = GetEnvironmentVariableW(L"MADEIRA_STEAM_HOST_EXPECTED_INSTALL", root, 1022);
    if (!length || length >= 1022) return false;
    if (root[length - 1] != L'\\') { root[length++] = L'\\'; root[length] = 0; }
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return false;
    PROCESSENTRY32W entry;
    memset(&entry, 0, sizeof(entry));
    entry.dwSize = sizeof(entry);
    bool found = false;
    for (BOOL ok = Process32FirstW(snap, &entry); ok && !found; ok = Process32NextW(snap, &entry)) {
        if (entry.th32ProcessID == GetCurrentProcessId() || keep_alive_name_denied(entry.szExeFile)) continue;
        HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, entry.th32ProcessID);
        wchar_t image[1024];
        DWORD size = 1024;
        if (process && QueryFullProcessImageNameW(process, 0, image, &size) && size > length && !_wcsnicmp(image, root, length)) found = true;
        if (process) CloseHandle(process);
    }
    CloseHandle(snap);
    return found;
}

'''
TEST_OLD = "    if (!n || n >= 1024) return false;\n    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);\n"
TEST_NEW = ("    if (!n || n >= 1024) return false;\n"
            "    if (list[0] == L'*' && !list[1]) return install_tree_running();   /* " + MARKER + " */\n"
            "    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);\n")


def patch(src):
    if MARKER in src:
        if HELPERS not in src or TEST_NEW not in src or INC_NEW not in src:
            raise ValueError("partial or changed keep-alive-tree patch")
        return src
    if "MADEIRA_DOCK_KEEP_ALIVE" not in src:
        raise ValueError("tools/patch-dock-keep-alive.py has not run (MADEIRA_DOCK_KEEP_ALIVE is missing)")
    for what, old in (("the tlhelp32 include", INC_OLD), ("keep_alive_running's head", FN_OLD), ("keep_alive_running's list test", TEST_OLD)):
        if src.count(old) != 1:
            raise ValueError("%s of launch.c not found once (madeira-dock or patch-dock-keep-alive.py changed)" % what)
    return src.replace(INC_OLD, INC_NEW, 1).replace(FN_OLD, HELPERS + FN_OLD, 1).replace(TEST_OLD, TEST_NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-keep-alive-tree: " + str(error))
    if result == source:
        print("patch-dock-keep-alive-tree: already in")
    else:
        path.write_text(result)
        print("madeira-dock: MADEIRA_DOCK_KEEP_ALIVE = * holds the session while a program of the game's install folder runs")
