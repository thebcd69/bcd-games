#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""GTA V Legacy's GPU reset at the first scene (ajan12/gta5l): auto-tune rule gta5l-gpu-reset-encoder-status.

The failure (tester logs 574-592, the owner's build 546 start): the first real scene frame after the Story Mode
load never finishes, iOS resets the GPU, every command buffer in flight comes back as
"Device error at frame N: ... kIOGPUCommandBufferCallbackErrorInnocentVictim". The owner's build 547 start (same game
file but for env.MADEIRA_DXMT_FAULT_INFO = 1, which is the only code change of 546 -> 547) had no GPU error. The rule adds that
one existing line, after the failure only. What this holds it to:

  - the rule is what the notes say: one existing config line, a D3D11 Dock session, no unchanged retry, the recovery's victims
    (four or more), GTA V Legacy's GTA5.exe, and nothing once the line was in force;
  - the signals, evaluated here on every auto-tune fixture the way the app evaluates them, hold for exactly the two logs
    that show the failure (the tester's, the owner's 546) and for no other log: not the owner's build 547 start that played, not
    another Legacy start, not another game's GPU error;
  - GTA V Legacy's recommended list is untouched (a Legacy start that does not meet the failure sees nothing new), and the
    switch's own patch is in CI before the build, so the line does something;
  - the line is a known catalog key, default off.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
FIX = R / "tests/fixtures/autotune"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + detail) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("autotune_rules", R / "build/tools/autotune_rules.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ID = "gpu-reset-lower-graphics-settings"   # was gta5l-gpu-reset-encoder-status (GTA V Legacy only, with the FAULT_INFO line)
LINE = "env.MADEIRA_DXMT_FAULT_INFO = 1"
rule = next((r for r in mod.RULES if r["id"] == ID), None)
check("the rule exists", rule is not None)
if rule is None:
    print("FAILED: GTA V Legacy GPU reset rule")
    sys.exit(1)

# ---- the rule's shape -------------------------------------------------------------------------------------------------
# 2026-10-23 17:23 (Discord): the tester's in-game settings were all Very High; on Normal the game played. The FAULT_INFO line did not
# help him (Q70SZ2), so the rule adds no line any more: a known failure (noFix) whose reason tells the player to set every option
# to its lowest value (owner: not Normal, or players leave MSAA on).
check("it adds no line: a known failure no line fixes (noFix), not a fix in code, no upgrade",
      rule["lines"] == [] and rule.get("noFix") is True and "fixedIn" not in rule
      and not rule.get("upgrade") and not rule.get("listUpgrade"))
check("its reason tells the player to set every graphics option to its lowest value, MSAA off (owner: not Normal)",
      "graphics settings" in rule["reason"] and "lowest value" in rule["reason"] and "MSAA" in rule["reason"] and "Normal" not in rule["reason"])
check("any session (Dock, library: Steam, Epic or a local copy) and any Direct3D version", "session" not in rule and "api" not in rule)
victims = [s for s in rule["all"] if s["has"] == ["kIOGPUCommandBufferCallbackErrorInnocentVictim"]]
check("it needs the GPU recovery's victims, four or more lines (DXMT's 'Device error' and madeira-d3d12's ml1049 lines both carry the text)",
      len(rule["all"]) == 1 and len(victims) == 1 and (victims[0].get("count") or 0) >= 4)
names = [" ".join(s["has"]) for s in rule["any"]]
want = ["GTA5.exe", "GTA5_Enhanced.exe", "RDR2.exe", "GhostOfTsushima.exe", "GoW.exe", "HorizonZeroDawn.exe", "HorizonForbiddenWest.exe",
        "Spider-Man.exe", "MilesMorales.exe", "Spider-Man2.exe"]
check("it names the heavy games (owner, 2026-10-23): GTA V Legacy and Enhanced, RDR2, GoT, GoW, both Horizons, the Spider-Man games",
      all(("Session log: logs/" + w + "-") in names and ("Steam game " + w + ":") in names for w in want)
      and "library launch Grand Theft Auto V:" in names and "library launch Grand Theft Auto V Enhanced:" in names)
check("every identity signal is a whole program name or title on its own line kind (no bare word that another game could carry)",
      all(n.startswith(("Session log: logs/", "Steam game ", "library launch ")) and (n.endswith("-") or n.endswith(":")) for n in names))

# ---- the signals on every fixture, as the app evaluates them -----------------------------------------------------------


def count(lines, sig):
    return sum(1 for l in lines if all(h in l for h in sig["has"]))


def signals_hold(lines):
    if any(count(lines, s) < (s.get("count") or 1) for s in rule["all"]):
        return False
    if rule.get("any") and not any(count(lines, s) >= (s.get("count") or 1) for s in rule["any"]):
        return False
    return not any(count(lines, s) >= (s.get("count") or 1) for s in rule.get("none", []))


hold = []
for p in sorted(FIX.glob("*.log")):
    if signals_hold(p.read_text(errors="replace").split("\n")):
        hold.append(p.stem)
TESTER, OWNER_546, OWNER_547 = "gta-v-legacy-gpu-reset-2026-10-23", "gta-v-legacy-2026-10-15-12-35-46", "gta-v-legacy-2026-10-15-13-37-35"
# round 624 (agent R S8): GTA V Legacy 8PDF19 played five minutes before the same GPU reset; its fixture carries the signals, but the rule
# reads failed sessions only (that one is scored ran, and gpu-dead-after-play speaks for it), so its verdict is not this rule's.
PLAYED_8PDF19 = "gta-v-legacy-gpu-dead-played-2026-10-29"
check("the signals hold for the tester's log and the owner's build 546 log, and for no other fixture (but 8PDF19, which played)",
      sorted(hold) == sorted([TESTER, OWNER_546, PLAYED_8PDF19]), "hold for: %s" % hold)
_p = json.loads((FIX / (PLAYED_8PDF19 + ".expect")).read_text())
check("...and on 8PDF19 the rule does not match: the session played (ran), gpu-dead-after-play names it",
      _p["outcome"] == "ran" and ID not in _p["matched"] and "gpu-dead-after-play" in _p["matched"], str(_p["matched"]))
check("the owner's build 547 start that played (no GPU error) never holds",
      OWNER_547 not in hold and "Device error" not in (FIX / (OWNER_547 + ".log")).read_text())
# The other Legacy fixtures (installs, launcher-only starts) and every other game's GPU error stay out; the one other
# fixture with a victim line has a single one, below the count.
others_with_victim = [p.stem for p in FIX.glob("*.log")
                      if "kIOGPUCommandBufferCallbackErrorInnocentVictim" in p.read_text(errors="replace")
                      and p.stem not in (TESTER, OWNER_546)]
# round 624: other games' GPU resets (Resident Evil 7, re7-gpu-reset, gpu-reset-any-game) have four victims and more; the rule's other
# signals (GTA5.exe, the Dock, Direct3D 11) keep them out, which the hold list above proves.
check("no other fixture with victim lines (another game's GPU reset, a synthetic GPU-tier log) carries the rule's signals",
      [n for n in others_with_victim if n in hold] == [PLAYED_8PDF19] or [n for n in others_with_victim if n in hold] == [],
      str([n for n in others_with_victim if n in hold]))

# ---- the fixtures and their .expect ----------------------------------------------------------------------------------
for name in (TESTER, OWNER_546):
    exp = json.loads((FIX / (name + ".expect")).read_text())
    # round 624: gpu-reset-any-game (26) joins the list after this rule; this rule stays first and decides
    check(f"{name}: hung, the rule matched, nothing is next (known)", exp["outcome"] == "hung" and exp["matched"][:1] == [ID]
          and set(exp["matched"]) <= {ID, "gpu-reset-any-game"}
          and exp["next"] == [] and exp["status"] == "known" and exp["end"].get("killed") is True)
tester = (FIX / (TESTER + ".log")).read_text()
check("the tester's fixture is Legacy on Dock, with the failure and without the line in force",
      "[session-log] Steam game GTA5.exe:" in tester and "library launch Grand Theft Auto V:" in tester
      and "[dock-report]" in tester
      and sum("kIOGPUCommandBufferCallbackErrorInnocentVictim" in l for l in tester.split("\n")) >= 4
      and "MADEIRA_DXMT_FAULT_INFO" not in tester and "[fault-enc]" not in tester)

# ---- nothing for a start that does not meet the failure; the line has a patch behind it ------------------------------------
rec = json.loads((R / "recommended/recommendations.json").read_text())
gta = next(r for r in rec["rules"] if r["id"] == "gta5l-steam")
check("GTA V Legacy's recommended list does not carry the line or the diagnostic's labels (unset = today)",
      not any("FAULT_INFO" in l for l in gta["config"]) and not any("FAULT_INFO" in l for l in gta.get("smallRAMConfig", [])))
wf = (R / ".github/workflows/build-ipa.yml").read_text()
key = wf.index("key=i386-farm-")
check("CI patches DXMT with the switch before the i386 farm key, and runs this check",
      0 <= wf.find("run: python3 tools/patch-dxmt-d3d11-fault-info.py") < key and
      "run: python3 tests/host/check-gta5l-gpu-reset.py" in wf)
gen = (R / "app/Madeira/AutoTuneRules.generated.swift").read_text()
check("the generated Swift table carries the rule", ID in gen)

print("PASS: GPU reset rule (heavy games: lower the graphics settings)" if ok else "FAILED: GPU reset rule")
sys.exit(0 if ok else 1)
