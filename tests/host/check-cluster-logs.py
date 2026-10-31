#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/cluster-logs.py: a session that played is sorted apart from the failures (round 624, agent B S10; triage tools only).

Cult of the Lamb (HH6KNK, build 622) played 146 s with 8,081 frames, and its log carries 102 handled Mono null checks: the triage tool
gave it an access-violation signature and listed it among the failures. With the change a log whose session summary says it played
(60 s or more, 600 frames or more; a partial summary counts) gets "played+<signature>" and is listed after the failures; nothing else
changes. Synthetic lines only (no user data); the tool is loaded and called directly, then run on a temporary tree of two logs.
"""
from pathlib import Path
import importlib.util, json, os, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cluster_logs", root / "tools/cluster-logs.py")
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


base = ["[bcd] library launch Test Game"] + ["x"] * 130
av = ["D B4 Exception: Code: C0000005 Address: 7276019BD1 AV READ of 9B2"]


def log(*summaries):
    return base + av + list(summaries)


old_sig = cl.signature(log())
check('signature() itself is unchanged: the first access violation with its host address', old_sig == "av:read@host~BD1:9B2", old_sig)
check('a partial summary of 146 s and 8,081 frames is a session that played (HH6KNK)',
      cl.played(log("[22:18:42.068] [INFO] [session] length=178s partial=1 play=146s frames=8081 avg=56.5")) is True)
check('59 s of play, or 599 frames, or a short whole session is not',
      cl.played(log("[session] length=178s partial=1 play=59s frames=8081")) is False
      and cl.played(log("[session] length=178s play=146s frames=599")) is False
      and cl.played(log("[session] length=20s play=19s frames=900")) is False)
check('of two summaries the longest counts; the unclean-session line is no summary',
      cl.played(log("[session] length=20s partial=1 play=10s frames=500", "[session] length=200s play=180s frames=9000")) is True
      and cl.played(log("[session] the last session of X did not end cleanly (in front)")) is False)

# Round 624 (agent B S5; ETNA7Y): a thread whose process ended itself with exit code 0 faults while FEX tears it down; those faults are not the
# session's failure. Another thread's fault after it still is (a launcher that exits cleanly must not hide its game's crash).
clean = ["00ac:err:process:NtTerminateProcess NtTerminateProcess(handle=0x0, exit_code=0x0) syscalls=0 unix_calls=56097"]
teardown = ["D AC Exception: Code: C0000005 Address: 71F565635C AV READ of 100000000"]
check('a thread\'s faults after its own clean exit are skipped (ETNA7Y)', cl.signature(base + clean + teardown) == "none",
      cl.signature(base + clean + teardown))
check('...another thread\'s fault after it is still the signature', cl.signature(base + clean + teardown + av) == "av:read@host~BD1:9B2",
      cl.signature(base + clean + teardown + av))
check('...and the same thread\'s fault before its exit too', cl.signature(base + teardown + clean) == "av:read@host~35C",
      cl.signature(base + teardown + clean))
spec_f = importlib.util.spec_from_file_location("log_facts", root / "tools/log-facts.py")
lf = importlib.util.module_from_spec(spec_f)
spec_f.loader.exec_module(lf)
keys = [k for k, _ in lf.exceptions(base + teardown + clean + teardown + av)]
check('log-facts counts the exiting thread\'s later faults apart ("@after-clean-exit")',
      ("C0000005", "READ", "host~35C") in keys and ("C0000005", "READ", "after-clean-exit") in keys
      and ("C0000005", "READ", "host~BD1") in keys, keys)

with tempfile.TemporaryDirectory() as t:
    day = Path(t) / "2026-10-29"
    day.mkdir()
    (day / "AAAAAA.txt").write_text("\n".join(log("[session] length=178s partial=1 play=146s frames=8081")) + "\n")
    (day / "BBBBBB.txt").write_text("\n".join(log()) + "\n")
    for code in ("AAAAAA", "BBBBBB"):
        (day / (code + ".json")).write_text(json.dumps({"uploaded": "2026-10-29T08:00:00Z",
                                                        "meta": {"game": "Test Game", "device": "iPhone16,1", "os": "18.5", "build": 624}}))
    r = subprocess.run([sys.executable, str(root / "tools/cluster-logs.py"), "--dir", t, "--json"], capture_output=True, text=True)
    out = r.stdout
    check('the tool runs on a tree of two logs', r.returncode == 0, r.stderr[-1500:])
    if r.returncode == 0:
        try:
            data = json.loads(out)
        except ValueError:
            data = None
        text = json.dumps(data) if data is not None else out
        check('the played log gets "played+" and the other keeps its plain signature',
              'played+av:read@host~BD1:9B2' in text and '"av:read@host~BD1:9B2"' in text, text[:1500])
        r2 = subprocess.run([sys.executable, str(root / "tools/cluster-logs.py"), "--dir", t], capture_output=True, text=True)
        lines = r2.stdout.splitlines()
        i_fail = next((i for i, l in enumerate(lines) if 'av:read@host~BD1:9B2' in l and 'played+' not in l), None)
        i_play = next((i for i, l in enumerate(lines) if 'played+av:read@host~BD1:9B2' in l), None)
        check('the failure is listed before the played session', i_fail is not None and i_play is not None and i_fail < i_play, r2.stdout[-1500:])

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check('CI runs this check', 'python3 tests/host/check-cluster-logs.py' in wf)
print('PASS: cluster-logs' if ok else 'FAIL: cluster-logs')
raise SystemExit(0 if ok else 1)
