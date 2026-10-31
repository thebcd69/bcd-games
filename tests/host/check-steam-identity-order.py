#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_STEAM_IDENTITY = none from a game's own file takes effect (round 624, agent R).

WineProcessBridge.m read MADEIRA_STEAM_IDENTITY near the start of wine_process_thread, about 210 lines before the game's own `env.`
lines are exported, so the key from a game's file (the Epic GTA V / RDR2 lists, auto-tune's line) was never seen: 0 of 2,182
logs printed its line. A second check after the game's env loop unsets the Steam identity when the key says none.
"""
import sys
from pathlib import Path

src = (Path(__file__).resolve().parents[2] / "app/Madeira/WineProcessBridge.m").read_text()
ok = True


def check(name, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    ok &= bool(cond)


loop = src.find('fprintf(stderr, "[madeira-env] game %s=%s\\n", k.UTF8String, v.UTF8String);')
late = src.find('const char *late_identity = getenv("MADEIRA_STEAM_IDENTITY");')
once = src.find("bcd-debug-once.txt")
early = src.find('const char *steam_identity = getenv("MADEIRA_STEAM_IDENTITY");')
check("the early check is still there", early > 0)
check("a second check runs after the game's env lines are exported (and before the debug-once block)", 0 < loop < late < once)
blk = src[late:late + 900]
check("it unsets the three Steam identity variables only for the value none",
      'strcmp(late_identity, "none") == 0' in blk and all('unsetenv("%s");' % v in blk for v in ("SteamAppPath", "SteamGameId", "SteamAppId")))
check("it says so in the log", "[steam-env] MADEIRA_STEAM_IDENTITY=none (from the game's file)" in blk)
print("PASS: steam identity order" if ok else "FAILED: steam identity order")
sys.exit(0 if ok else 1)
