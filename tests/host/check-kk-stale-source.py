#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A KosmicKrisp source tree whose patches are not the series any more is unpacked again (build 676).

Build 676 shipped build 675's KosmicKrisp: playport patch 0035 was corrected under the same name, and build-kosmickrisp.sh
took the kept tree's empty .madeira-applied-0035-... marker for "applied", so nir_to_msl.c was never compiled again (the
cache key changed and the step ran, in 0.0 min). Markers now hold the patch's SHA-256 and build/mesa-ios/kk-stale-source.sh
removes a tree whose markers do not match the series, before the recipe unpacks it.

  1. kk-stale-source.sh, run on trees made here: no tree, a tree with no marker of ours (somebody's own) and a tree whose
     markers match are left alone; an old empty marker, a changed patch and a patch gone from the series remove the tree;
  2. build-kosmickrisp.sh writes the SHA-256 into the marker (its patch loop run with a stand-in `patch`);
  3. build-kosmickrisp-bcd.sh runs the check for both profiles before the recipe, the KosmicKrisp cache key covers the
     script, and CI runs this check.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
MESA = R / "build/mesa-ios"
STALE = MESA / "kk-stale-source.sh"
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


check(STALE.exists() and os.access(STALE, os.X_OK), "build/mesa-ios/kk-stale-source.sh is there and executable")

with tempfile.TemporaryDirectory(prefix="kk-stale-") as d:
    d = Path(d)
    patches = d / "patches"
    patches.mkdir()
    (patches / "0001-a.patch").write_text("first\n")
    (patches / "0002-b.patch").write_text("second\n")

    def tree(name, markers):
        src = d / name
        (src / "src/kosmickrisp").mkdir(parents=True)
        (src / "src/kosmickrisp/meson.build").write_text("# mesa\n")
        for patch, content in markers.items():
            (src / (".madeira-applied-" + patch)).write_text(content)
        return src

    def run(src):
        return subprocess.run(["sh", str(STALE), str(src), str(patches)], capture_output=True, text=True)

    good = {p.name: sha(p) + "\n" for p in patches.glob("*.patch")}
    r = run(d / "missing")
    check(r.returncode == 0 and not r.stdout, "no tree: nothing to do", (r.returncode, r.stdout, r.stderr))
    own = tree("own", {})
    r = run(own)
    check(r.returncode == 0 and own.exists(), "a tree with no marker of ours is never touched", (r.stdout, r.stderr))
    ok = tree("ok", good)
    r = run(ok)
    check(r.returncode == 0 and ok.exists() and not r.stdout, "a tree whose markers match the series stays", (r.stdout, r.stderr))
    legacy = tree("legacy", {"0001-a.patch": "", "0002-b.patch": ""})
    r = run(legacy)
    check(r.returncode == 0 and not legacy.exists() and "0001-a.patch is not the one that was applied" in r.stdout,
          "an old empty marker (before build 676) removes the tree once", (r.stdout, r.stderr))
    changed = tree("changed", dict(good, **{"0002-b.patch": hashlib.sha256(b"second, before its fix\n").hexdigest() + "\n"}))
    r = run(changed)
    check(r.returncode == 0 and not changed.exists() and "0002-b.patch is not the one that was applied" in r.stdout,
          "a patch changed under the same name removes the tree (build 676's 0035)", (r.stdout, r.stderr))
    gone = tree("gone", dict(good, **{"0003-c.patch": "abc\n"}))
    r = run(gone)
    check(r.returncode == 0 and not gone.exists() and "0003-c.patch is no longer in the series" in r.stdout,
          "a patch gone from the series removes the tree", (r.stdout, r.stderr))
    partial = tree("partial", {"0001-a.patch": good["0001-a.patch"]})
    r = run(partial)
    check(r.returncode == 0 and partial.exists(), "a tree that has only some patches applied stays (the recipe applies the rest)",
          (r.stdout, r.stderr))

    # 2. the recipe's patch loop writes the SHA-256 (stand-in patch; the loop is cut out of build-kosmickrisp.sh)
    recipe = (MESA / "build-kosmickrisp.sh").read_text()
    start = recipe.index('for p in "$@"; do\n    [ -e "$p" ] || continue\n    m="$SRC/.madeira-applied-$(basename "$p")"')
    loop = recipe[start:recipe.index("\ndone\n", start) + 6]
    bin_ = d / "bin"
    bin_.mkdir()
    (bin_ / "patch").write_text("#!/bin/sh\ncat > /dev/null\n")
    (bin_ / "patch").chmod(0o755)
    fresh = tree("fresh", {})
    script = 'set -eu\nSRC="$1"\nshift\n' + loop
    env = dict(os.environ, PATH=str(bin_) + os.pathsep + os.environ["PATH"])
    r = subprocess.run(["bash", "-c", script, "loop", str(fresh), *map(str, sorted(patches.glob("*.patch")))],
                       capture_output=True, text=True, env=env)
    written = {m.name[len(".madeira-applied-"):]: m.read_text() for m in fresh.glob(".madeira-applied-*")}
    check(r.returncode == 0 and written == good, "build-kosmickrisp.sh's marker holds the patch's SHA-256", (r.stderr, written))
    r = run(fresh)
    check(fresh.exists() and not r.stdout, "...and a tree it just patched passes the check", r.stdout)

wrap = (MESA / "build-kosmickrisp-bcd.sh").read_text()
check('    SRC="$KK_WORK/mesa-$MESA_PLAYPORT"\n    sh "$HERE/kk-stale-source.sh" "$SRC" "$HERE/playport-patches"' in wrap
      and '    SRC="$KK_WORK/mesa-$MESA_RELEASE"\n    sh "$HERE/kk-stale-source.sh" "$SRC" "$HERE/kosmickrisp-patches"' in wrap,
      "build-kosmickrisp-bcd.sh checks the tree of either profile before its recipe unpacks and patches it")
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check(wf.count("'build/mesa-ios/fetch-playport.py', 'build/mesa-ios/kk-stale-source.sh',") == 2,
      "the KosmicKrisp cache key (restore and save) covers kk-stale-source.sh")
check("run: python3 tests/host/check-kk-stale-source.py" in wf, "CI runs this check")

print(("PASS" if not failures else "FAILED") + ": KosmicKrisp source trees follow the patch series (kk-stale-source.sh)")
sys.exit(1 if failures else 0)
