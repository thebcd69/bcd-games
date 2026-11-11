#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: publish the Steam discovery entries into the registry view a game reads, and replay steam_api's own calls (night round 2026-11-11, X2 S1 + S4b).

steam_api64.dll opens HKCU Software\\Valve\\Steam\\ActiveProcess with KEY_WOW64_32KEY (0x20219) and a 32-bit game uses its default view. In a prefix whose
HKCU hive has a Software\\Wow6432Node key both land in Software\\Wow6432Node\\Valve\\Steam, a tree this host never wrote: the Dock opens the plain key (the
64-bit tree) and publishes pid, ActiveUser and SteamPID there. K7QKB6 (Hitman: Blood Money), 25C9NT and 9HEY99 (Bulletstorm), NR2WGS (Medal of Honor:
Airborne) logged "HKCU ActiveProcess pid: key not opened" for the 32-bit game while the 64-bit tree held the Dock's pid 1 s old; KQMTVT (LEGO Marvel
Super Heroes) found a dead real client's pid 260 there. Without Wow6432Node the 32-bit view IS the plain key, and every write below repeats a value that is
already there. This patch never creates Wow6432Node.

madeira-dock/src/launch.c (upstream submodule) gets, after tools/patch-dock-steam-readback.py:
  - the same pid and ActiveUser, and the static strings SteamClientDll, SteamClientDll64 (ActiveProcess) and SteamPath, SteamExe (Steam) where the other view
    lacks them or names a path that is not on the drive, written into the 32-bit view (RegCreateKeyExW with KEY_WOW64_32KEY); applied after the three
    publishes and once a second while the game starts (a node that appears late is covered); the two dynamic values go back first at "done:", before the
    64-bit restore;
  - launch-view-mirror (bits: 1 the 32-bit ActiveProcess key opened, 2 its pid was missing or another one before the write, 4 pid written, 8 ActiveUser
    written, 16 the Steam key's strings step ran; emitted when the mask changes), launch-view-error (the Win32 error of the open);
  - launch-host-pid (this host's pid, the value the game should read; G6 S10), launch-steamapi-probe-pre / launch-steamapi-probe / launch-steamapi-probe-t / launch-steamapi-probe-running: the five calls of steam_api64 as a mask: 1
    RegOpenKeyExW(HKCU, ActiveProcess, 0, 0x20219) ok, 2 SteamClientDll64 read (REG_SZ, not empty), 4 pid read, 8 pid is this host, 16 OpenProcess(0x400) ok, 32
    the process is still active; 63 = the game finds Steam. "-pre" is what the game would have seen before the mirror, the others after it (-t: while the
    game starts, only when the mask changes).
Numbers only. MADEIRA_DOCK_STEAM_VIEWS=0 skips the mirror (the probes still run). Usage: patch-dock-steam-views.py madeira-dock/src/launch.c
(idempotent; fails by name when an anchor moved)
"""
import sys

MARKER = "madeira-bcd: steam-views-v1"
PREREQ = "madeira-bcd: launch-discovery-readback"

HELPERS = r'''/* madeira-bcd: steam-views-v1 (tools/patch-dock-steam-views.py, night round 2026-11-11; K7QKB6, 25C9NT, 9HEY99, NR2WGS, KQMTVT). steam_api64.dll reads
 * HKCU Software\Valve\Steam\ActiveProcess with KEY_WOW64_32KEY and a 32-bit game with its default view; with a Software\Wow6432Node key in HKCU both
 * reach a tree this host never wrote. Without the node the 32-bit view is the plain key: the writes below repeat what is there. */
struct view_dword { DWORD previous; bool existed, changed; };
struct view_mirror { HKEY active32; bool recorded; struct view_dword pid, user; };
static bool view_read_dword(HKEY key, const wchar_t *name, DWORD *out)
{
    DWORD type = 0, size = sizeof(DWORD);
    return RegQueryValueExW(key, name, NULL, &type, (BYTE *)out, &size) == ERROR_SUCCESS && type == REG_DWORD && size == sizeof(DWORD);
}
static bool view_write_dword(HKEY key, const wchar_t *name, DWORD value)
{
    return RegSetValueExW(key, name, 0, REG_DWORD, (const BYTE *)&value, sizeof(DWORD)) == ERROR_SUCCESS;
}
/* a static string is copied only when the other view lacks it or names something that is not on the drive */
static bool view_copy_sz(HKEY from, HKEY to, const wchar_t *name)
{
    wchar_t src[520], dst[520];
    DWORD src_type = 0, src_size = sizeof(src) - 2, dst_type = 0, dst_size = sizeof(dst) - 2;
    memset(src, 0, sizeof(src));
    memset(dst, 0, sizeof(dst));
    if (RegQueryValueExW(from, name, NULL, &src_type, (BYTE *)src, &src_size) != ERROR_SUCCESS || src_type != REG_SZ || !src[0]) return false;
    if (RegQueryValueExW(to, name, NULL, &dst_type, (BYTE *)dst, &dst_size) == ERROR_SUCCESS && dst_type == REG_SZ &&
        (!wcscmp(src, dst) || GetFileAttributesW(dst) != INVALID_FILE_ATTRIBUTES)) return false;
    return RegSetValueExW(to, name, 0, REG_SZ, (const BYTE *)src, (DWORD)((wcslen(src) + 1) * sizeof(wchar_t))) == ERROR_SUCCESS;
}
/* Returns the launch-view-mirror mask, or -1 when the 32-bit view's ActiveProcess key does not open (GetLastError is not meaningful: see the event). */
static int32_t view_mirror_publish(struct view_mirror *m, HKEY active64, DWORD pid, DWORD user, LSTATUS *error)
{
    int32_t bits = 0;
    DWORD now = 0;
    if (!m->active32) {
        *error = RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\Valve\\Steam\\ActiveProcess", 0, NULL, 0,
                                 KEY_QUERY_VALUE | KEY_SET_VALUE | KEY_WOW64_32KEY, NULL, &m->active32, NULL);
        if (*error != ERROR_SUCCESS) { m->active32 = NULL; return -1; }
    }
    bits |= 1;
    if (!view_read_dword(m->active32, L"pid", &now) || now != pid) bits |= 2;
    if (!m->recorded) {
        m->recorded = true;
        m->pid.existed = view_read_dword(m->active32, L"pid", &m->pid.previous);
        m->user.existed = view_read_dword(m->active32, L"ActiveUser", &m->user.previous);
    }
    if (view_write_dword(m->active32, L"pid", pid)) { m->pid.changed = true; bits |= 4; }
    if (view_write_dword(m->active32, L"ActiveUser", user)) { m->user.changed = true; bits |= 8; }
    {   /* the static strings: only where missing or dead (the same key: nothing to do) */
        HKEY steam64 = NULL, steam32 = NULL;
        view_copy_sz(active64, m->active32, L"SteamClientDll");
        view_copy_sz(active64, m->active32, L"SteamClientDll64");
        if (RegOpenKeyExW(HKEY_CURRENT_USER, L"Software\\Valve\\Steam", 0, KEY_QUERY_VALUE, &steam64) == ERROR_SUCCESS &&
            RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\Valve\\Steam", 0, NULL, 0, KEY_QUERY_VALUE | KEY_SET_VALUE | KEY_WOW64_32KEY,
                            NULL, &steam32, NULL) == ERROR_SUCCESS) {
            view_copy_sz(steam64, steam32, L"SteamPath");
            view_copy_sz(steam64, steam32, L"SteamExe");
            bits |= 16;
        }
        if (steam64) RegCloseKey(steam64);
        if (steam32) RegCloseKey(steam32);
    }
    return bits;
}
/* only the two dynamic values go back; the static strings stay like the seeded ones. Runs before the 64-bit restore: with no Wow6432Node the two keys are one. */
static void view_mirror_restore(struct view_mirror *m)
{
    if (!m->active32) return;
    if (m->pid.changed) {
        if (m->pid.existed) view_write_dword(m->active32, L"pid", m->pid.previous); else RegDeleteValueW(m->active32, L"pid");
    }
    if (m->user.changed) {
        if (m->user.existed) view_write_dword(m->active32, L"ActiveUser", m->user.previous); else RegDeleteValueW(m->active32, L"ActiveUser");
    }
    RegCloseKey(m->active32);
    m->active32 = NULL;
}
static bool view_mirror_wanted(void)
{
    wchar_t text[4] = {0};
    return !(GetEnvironmentVariableW(L"MADEIRA_DOCK_STEAM_VIEWS", text, 4) == 1 && text[0] == L'0');
}
/* The calls of steam_api64.dll (SteamAPI_GetSteamInstallPath's key read and SteamAPI_IsSteamRunning): bit 1 RegOpenKeyExW(HKCU, ActiveProcess, 0, 0x20219) ok,
 * 2 SteamClientDll64 read (REG_SZ, not empty), 4 pid read, 8 pid is host_pid, 16 OpenProcess(0x400, pid) ok, 32 the process is still active. 63: the game finds Steam. */
static int32_t dock_steamapi_probe(DWORD host_pid)
{
    int32_t bits = 0;
    HKEY key = NULL;
    DWORD type = 0, size = sizeof(DWORD), pid = 0, dll_size = 1038;
    wchar_t dll[520];
    memset(dll, 0, sizeof(dll));
    if (RegOpenKeyExW(HKEY_CURRENT_USER, L"Software\\Valve\\Steam\\ActiveProcess", 0, 0x20219, &key) != ERROR_SUCCESS) return bits;
    bits |= 1;
    if (RegQueryValueExW(key, L"SteamClientDll64", NULL, &type, (BYTE *)dll, &dll_size) == ERROR_SUCCESS && type == REG_SZ && dll[0]) bits |= 2;
    if (RegQueryValueExW(key, L"pid", NULL, &type, (BYTE *)&pid, &size) == ERROR_SUCCESS) {
        bits |= 4;
        if (pid == host_pid) bits |= 8;
    }
    RegCloseKey(key);
    HANDLE process = OpenProcess(0x400, FALSE, pid);
    if (process) {
        DWORD code = 0;
        bits |= 16;
        if (GetExitCodeProcess(process, &code) && code == 259) bits |= 32;
        CloseHandle(process);
    }
    return bits;
}

'''

STEPS = [
    ("the place for the helpers (before `interrupted`)",
     "static volatile LONG interrupted;\n",
     HELPERS + "static volatile LONG interrupted;\n"),
    ("the launch's view state (after the readback state)",
     "    bool dock_running_readback = false, dock_reassert_on = dock_reassert_wanted();\n",
     "    bool dock_running_readback = false, dock_reassert_on = dock_reassert_wanted();\n"
     "    /* madeira-bcd: steam-views-v1 state; declared here so every `goto done` finds it set. */\n"
     "    struct view_mirror dock_view = {0};\n"
     "    int32_t dock_view_bits = INT32_MIN, dock_probe_last = INT32_MIN;\n"
     "    bool dock_views_on = view_mirror_wanted();\n"),
    ("the publish event with its readback",
     '    o->event("launch-client-discovery-published", 1);\n'
     '    o->event("launch-discovery-readback", dock_readback(values));\n',
     '    o->event("launch-client-discovery-published", 1);\n'
     '    o->event("launch-discovery-readback", dock_readback(values));\n'
     '    {   /* madeira-bcd: steam-views-v1: what the game would see before the mirror, the mirror, and what it sees after it */\n'
     '        LSTATUS view_error = ERROR_SUCCESS;\n'
     '        o->event("launch-host-pid", (int32_t)GetCurrentProcessId());   /* the pid the game should read (G6 S10: LEGO Marvel found 260) */\n'
     '        o->event("launch-steamapi-probe-pre", dock_steamapi_probe(GetCurrentProcessId()));\n'
     '        if (dock_views_on) {\n'
     '            dock_view_bits = view_mirror_publish(&dock_view, active, GetCurrentProcessId(), (DWORD)steamid, &view_error);\n'
     '            o->event(dock_view_bits < 0 ? "launch-view-error" : "launch-view-mirror", dock_view_bits < 0 ? (int32_t)view_error : dock_view_bits);\n'
     '        }\n'
     '        dock_probe_last = dock_steamapi_probe(GetCurrentProcessId());\n'
     '        o->event("launch-steamapi-probe", dock_probe_last);\n'
     '    }\n'),
    ("the once-a-second readback of the wait loop",
     "                dock_max_gap = 0;\n"
     "            }\n"
     "            if (dock_running_at && !dock_running_readback && now - dock_running_at >= 5000) {\n",
     "                dock_max_gap = 0;\n"
     "                {   /* madeira-bcd: steam-views-v1: a node that appears while the game starts is covered; an event only when a mask changes */\n"
     "                    LSTATUS view_error = ERROR_SUCCESS;\n"
     "                    int32_t view_bits = dock_views_on ? view_mirror_publish(&dock_view, active, GetCurrentProcessId(), (DWORD)steamid, &view_error) : dock_view_bits;\n"
     "                    int32_t probe_bits = dock_steamapi_probe(GetCurrentProcessId());\n"
     "                    if (view_bits != dock_view_bits) {\n"
     "                        dock_view_bits = view_bits;\n"
     '                        o->event(view_bits < 0 ? "launch-view-error" : "launch-view-mirror", view_bits < 0 ? (int32_t)view_error : view_bits);\n'
     "                    }\n"
     "                    if (probe_bits != dock_probe_last) {\n"
     "                        dock_probe_last = probe_bits;\n"
     '                        o->event("launch-steamapi-probe-t", probe_bits);\n'
     "                    }\n"
     "                }\n"
     "            }\n"
     "            if (dock_running_at && !dock_running_readback && now - dock_running_at >= 5000) {\n"),
    ("the readback 5 s after the game runs",
     '                o->event("launch-discovery-readback-running", dock_readback(values));\n',
     '                o->event("launch-discovery-readback-running", dock_readback(values));\n'
     '                o->event("launch-steamapi-probe-running", dock_steamapi_probe(GetCurrentProcessId()));   /* madeira-bcd: steam-views-v1 */\n'),
    ("the end of the launch (before the 64-bit restore)",
     "done:\n    SetConsoleCtrlHandler(on_control, FALSE);\n",
     "done:\n    SetConsoleCtrlHandler(on_control, FALSE);\n"
     "    view_mirror_restore(&dock_view);   /* madeira-bcd: steam-views-v1: before the 64-bit restore below (the same key without Wow6432Node) */\n"),
]


def patch(src):
    if MARKER in src:
        if any(new not in src for _, _, new in STEPS):
            raise ValueError("partial or changed steam-views patch")
        return src
    if PREREQ not in src:
        raise ValueError("tools/patch-dock-steam-readback.py has not run (its marker is missing)")
    for what, old, _ in STEPS:
        if src.count(old) != 1:
            raise ValueError("anchor not found exactly once (%s) -- madeira-dock or an earlier launch.c patch changed" % what)
    for what, old, new in STEPS:
        src = src.replace(old, new, 1)
    return src


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    with open(sys.argv[1]) as handle:
        source = handle.read()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-dock-steam-views: " + str(error))
    if result == source:
        print("patch-dock-steam-views: already in")
    else:
        with open(sys.argv[1], "w") as handle:
            handle.write(result)
        print("madeira-dock: the Steam discovery entries are mirrored into the 32-bit registry view; steam_api's five calls are replayed and reported")
