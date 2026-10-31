#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock: read the published Steam values back while the game starts; two opt-in experiments for Steam 113 (round 624).

The Rockstar Games Launcher's SteamAPI_Init found no running Steam in 39 of 187 Rockstar Dock sessions of the archive (Launcher
error 113, LAUNCHER_ERR_STEAM_FAILED; 5T9WRM, AX6XNN), while this host reported a clean launch: the values published, the
request submitted, no client error, and at the end launch-discovery-restored=1 (restore() found the three values intact). The
rate is clustered by device (iPhone17,2 11 of 13, iPhone18,1 11 of 13, the iPads 1 of 33). What the Launcher sees in between
was never logged. madeira-dock/src/launch.c (upstream submodule) gets, after patch-dock-game-scm.py and patch-dock-keep-alive.py:

  launch-discovery-readback     the three values publish() wrote, read back: bit 0 pid, bit 1 ActiveUser, bit 2 SteamPID equal
                                to what was written, bit 3 a read failed (7 = all intact). After publishing, after the launch
                                request, then once a second until the game runs (at most 40 times);
  launch-loop-gap-ms            the longest gap between two turns of the wait loop since the previous readback (the loop sleeps
                                50 ms; a starved host on a 6-core phone shows here);
  launch-discovery-readback-running   once, 5 s after the game runs (what a working start looks like);
  env.MADEIRA_DOCK_PUMP_MS = 1..50   the wait loop sleeps that long instead of 50 ms for 20 s after the launch request;
  env.MADEIRA_DOCK_REASSERT = 1      a published value found changed is written again (launch-discovery-reasserted: the writes).

Numbers only, no paths. Without the two keys the launch does exactly what it did; the readback reads three DWORDs a second.
Idempotent; fails by name when an anchor moves. Usage: patch-dock-steam-readback.py madeira-dock/src/launch.c
"""
import sys

MARKER = "madeira-bcd: launch-discovery-readback"

HELPERS = r'''/* madeira-bcd: launch-discovery-readback (round 624, Steam 113; tools/patch-dock-steam-readback.py). The three values
 * publish() wrote, read back: bit 0 pid, bit 1 ActiveUser, bit 2 SteamPID equal to what was written, bit 3 a read failed. */
static int32_t dock_readback(const struct saved_value *values)
{
    int32_t mask = 0;
    for (int i = 0; i < 3; ++i) {
        DWORD current = 0, size = sizeof(current), type = 0;
        if (!values[i].key ||
            RegQueryValueExW(values[i].key, values[i].name, NULL, &type, (BYTE *)&current, &size) != ERROR_SUCCESS ||
            type != REG_DWORD)
            mask |= 8;
        else if (current == values[i].written) mask |= 1 << i;
    }
    return mask;
}

/* env.MADEIRA_DOCK_REASSERT = 1: a published value found changed is written again; returns the writes. */
static int32_t dock_reassert(struct saved_value *values)
{
    int32_t writes = 0;
    for (int i = 0; i < 3; ++i) {
        DWORD current = 0, size = sizeof(current), type = 0;
        if (!values[i].changed) continue;
        if (RegQueryValueExW(values[i].key, values[i].name, NULL, &type, (BYTE *)&current, &size) == ERROR_SUCCESS &&
            type == REG_DWORD && current == values[i].written) continue;
        if (RegSetValueExW(values[i].key, values[i].name, 0, REG_DWORD, (const BYTE *)&values[i].written,
                           sizeof(DWORD)) == ERROR_SUCCESS) ++writes;
    }
    return writes;
}

static bool dock_reassert_wanted(void)
{
    wchar_t text[4] = {0};
    return GetEnvironmentVariableW(L"MADEIRA_DOCK_REASSERT", text, 4) == 1 && text[0] == L'1';
}

/* env.MADEIRA_DOCK_PUMP_MS = 1..50: the wait loop's sleep for 20 s after the launch request (else 50 ms, as always). */
static uint32_t dock_sleep_ms(const struct sh_observer *o, uint64_t since)
{
    static int read;
    static uint32_t ms;
    if (!read) {
        wchar_t text[8] = {0};
        read = 1;
        if (GetEnvironmentVariableW(L"MADEIRA_DOCK_PUMP_MS", text, 8) && text[0]) {
            long v = wcstol(text, NULL, 10);
            if (v >= 1 && v <= 50) ms = (uint32_t)v;
        }
    }
    return ms && o->now_ms() - since < 20000 ? ms : 50;
}

'''

STEPS = [
    ("the place for the helpers (before `interrupted`)",
     "static volatile LONG interrupted;\n",
     HELPERS + "static volatile LONG interrupted;\n"),
    ("the launch's registry state (`values[3]`)",
     "    struct saved_value values[3] = {0};\n",
     "    struct saved_value values[3] = {0};\n"
     "    /* madeira-bcd: launch-discovery-readback state; declared here so every `goto done` finds it set. */\n"
     "    uint64_t dock_last_turn = 0, dock_max_gap = 0, dock_last_readback = 0, dock_running_at = 0;\n"
     "    int32_t dock_readbacks = 0, dock_reasserted = 0;\n"
     "    bool dock_running_readback = false, dock_reassert_on = dock_reassert_wanted();\n"),
    ("the publish event",
     '    o->event("launch-client-discovery-published", 1);\n',
     '    o->event("launch-client-discovery-published", 1);\n'
     '    o->event("launch-discovery-readback", dock_readback(values));\n'),
    ("the launch request event",
     '    o->event("launch-request-submitted", call != 0);\n',
     '    o->event("launch-request-submitted", call != 0);\n'
     '    dock_last_readback = o->now_ms();\n'
     '    o->event("launch-discovery-readback", dock_readback(values));\n'),
    ("the top of the launch wait loop",
     "    result = 44;\n    while (!InterlockedCompareExchange(&interrupted, 0, 0)) {\n",
     "    result = 44;\n    while (!InterlockedCompareExchange(&interrupted, 0, 0)) {\n"
     "        {   /* madeira-bcd: launch-discovery-readback, the loop gap and the experiments */\n"
     "            uint64_t now = o->now_ms();\n"
     "            if (dock_last_turn && now - dock_last_turn > dock_max_gap) dock_max_gap = now - dock_last_turn;\n"
     "            dock_last_turn = now;\n"
     "            if (seen_running && !dock_running_at) dock_running_at = now;\n"
     "            if (!seen_running && dock_readbacks < 40 && now - dock_last_readback >= 1000) {\n"
     "                dock_last_readback = now;\n"
     "                ++dock_readbacks;\n"
     '                o->event("launch-discovery-readback", dock_readback(values));\n'
     '                o->event("launch-loop-gap-ms", (int32_t)(dock_max_gap > 600000 ? 600000 : dock_max_gap));\n'
     "                dock_max_gap = 0;\n"
     "            }\n"
     "            if (dock_running_at && !dock_running_readback && now - dock_running_at >= 5000) {\n"
     "                dock_running_readback = true;\n"
     '                o->event("launch-discovery-readback-running", dock_readback(values));\n'
     "            }\n"
     "            if (dock_reassert_on && !seen_running) dock_reasserted += dock_reassert(values);\n"
     "        }\n"),
    ("the retry sleep",
     "        if (retry_at) { o->sleep_ms(50); continue; }\n",
     "        if (retry_at) { o->sleep_ms(dock_sleep_ms(o, begin)); continue; }\n"),
    ("the loop's own sleep",
     "        if (!seen_running && o->now_ms() - begin > 90000) break;\n        o->sleep_ms(50);\n",
     "        if (!seen_running && o->now_ms() - begin > 90000) break;\n        o->sleep_ms(dock_sleep_ms(o, begin));\n"),
    ("the restore event",
     '    o->event("launch-discovery-restored", restored);\n',
     '    if (dock_reassert_on) o->event("launch-discovery-reasserted", dock_reasserted);\n'
     '    o->event("launch-discovery-restored", restored);\n'),
]


def main():
    if len(sys.argv) != 2:
        print("usage: patch-dock-steam-readback.py madeira-dock/src/launch.c", file=sys.stderr)
        return 2
    path = sys.argv[1]
    src = open(path).read()
    if MARKER in src:
        print("already patched")
        return 0
    for what, old, new in STEPS:
        if src.count(old) != 1:
            print(f"patch-dock-steam-readback: anchor not found exactly once ({what}) -- launch.c changed", file=sys.stderr)
            return 1
        src = src.replace(old, new, 1)
    open(path, "w").write(src)
    print("patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
