#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/build-wine-extra-dlls.sh: a persistent tree keeps its old extra DLLs unless MADEIRA_EXTRAS_REBUILD=1 says they are stale (round 2026-11-15, K6W).

The script builds a DLL only when the folder does not have its name, so the owner's build clone ships extras built on 2026-10-13 (its xaudio2_7.dll lacks the 2026-11-04
callback-order patch the 32-bit one has). The new `shipped()` is cut from the real script and run in bash against a made-up repository:
  - default (the switch unset or 0): every name that is in the folder counts as shipped, whatever the stamp says: exactly the old function;
  - MADEIRA_EXTRAS_REBUILD=1 without a stamp, or with a stamp of another Wine pin / patch script / script: a name that is in the folder and not tracked is NOT shipped (it is
    built again), a tracked one still is; with the current stamp every name in the folder is shipped again;
  - a patch script that changes makes the stamp stale; the stamp is written only by a run with the switch on and no failure (the last lines of the script).
"""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


text = (root / "tools/build-wine-extra-dlls.sh").read_text()
i = text.index("extras_stamp() {")
j = text.index("targets=\"\"; todo=\"\"")
funcs = text[i:j]
check("the stamp logic is in the script and the old one-line shipped() is gone", "MADEIRA_EXTRAS_REBUILD" in text and "shipped() { ls \"$SHIP\" | tr 'A-Z' 'a-z' | grep -qx \"$1.dll\"; }\n" not in text)
check("the stamp is written only at the end, only with the switch on and no failure",
      text.rstrip().endswith('[ "${MADEIRA_EXTRAS_REBUILD:-0}" = 1 ] && [ -z "$failed" ] && extras_stamp > "$EXTRAS_STAMP_FILE"\nexit 0'))
with tempfile.TemporaryDirectory(prefix="extras-rebuild-") as t:
    t = Path(t)
    R = t / "repo"
    (R / "wine").mkdir(parents=True)
    (R / "tools").mkdir()
    (R / "app/Madeira/arm64ec-windows").mkdir(parents=True)
    ship = R / "app/Madeira/arm64ec-windows"
    for n in ("Tracked.dll", "extra80.dll", "xaudio2_7.dll"):
        (ship / n).write_text("x")
    (R / "tools/patch-wine-a.py").write_text("# a\n")
    (R / "tools/build-wine-extra-dlls.sh").write_text("# script\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}

    def git(*a, cwd=R):
        return subprocess.run(["git"] + list(a), cwd=cwd, env=env, capture_output=True, text=True)
    git("init", "-q")
    git("add", "app/Madeira/arm64ec-windows/Tracked.dll")
    git("commit", "-q", "-m", "tracked")
    git("init", "-q", cwd=R / "wine")
    (R / "wine/f").write_text("1")
    git("add", "f", cwd=R / "wine")
    git("commit", "-q", "-m", "pin", cwd=R / "wine")

    def run(switch, names, stamp_mode=None):
        script = "set -u\nR='%s'\nSHIP='%s'\nB='%s'\n%s\n" % (R, ship, t / "build-arm64ec", funcs)
        if stamp_mode == "write":
            script += 'extras_stamp > "$EXTRAS_STAMP_FILE"\n'
            script += 'STALE_EXTRAS=0; if [ "${MADEIRA_EXTRAS_REBUILD:-0}" = 1 ] && [ "$(cat "$EXTRAS_STAMP_FILE" 2>/dev/null)" != "$(extras_stamp)" ]; then STALE_EXTRAS=1; fi\n'
        script += "".join('shipped %s && echo "%s yes" || echo "%s no"\n' % (n, n, n) for n in names)
        e = dict(env)
        if switch is not None:
            e["MADEIRA_EXTRAS_REBUILD"] = switch
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e)
        return dict(l.split() for l in r.stdout.splitlines() if l.strip()), r.stderr
    names = ["tracked", "extra80", "xaudio2_7", "absent"]
    for sw in (None, "0"):
        got, err = run(sw, names)
        check("switch %s: every name in the folder is shipped, an absent one is not (the old function)" % (sw,), got == {"tracked": "yes", "extra80": "yes", "xaudio2_7": "yes", "absent": "no"}, got or err)
    got, err = run("1", names)
    check("switch 1, no stamp: the tracked file is shipped, the extras of an earlier run are not (they are built again)",
          got == {"tracked": "yes", "extra80": "no", "xaudio2_7": "no", "absent": "no"}, got or err)
    got, err = run("1", names, stamp_mode="write")
    check("switch 1 with the current stamp: every name in the folder is shipped again", got == {"tracked": "yes", "extra80": "yes", "xaudio2_7": "yes", "absent": "no"}, got or err)
    (t / "build-arm64ec.extras-stamp").write_text("0" * 64 + "\n")
    got, err = run("1", names)
    check("switch 1 with a stamp of something else (a patch script changed): stale again", got["extra80"] == "no" and got["tracked"] == "yes", got or err)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs this check", "python3 tests/host/check-extras-rebuild.py" in wf)
print("PASS: extras rebuild stamp" if ok else "FAILED: extras rebuild stamp")
sys.exit(0 if ok else 1)
