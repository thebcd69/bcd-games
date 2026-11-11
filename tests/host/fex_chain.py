# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Shared by tests/host/check-fex-*.py (night round 2026-11-11): the CI's FEX patch chain, run on a copy of the pinned FEX sources.

tools/build-xtajit64.sh (ARM64EC module) and tools/build-xtajit-wow64.sh (WOW64 module) apply their patch scripts in a fixed order.
A check for one script wants the sources in exactly the state that script sees in the build, so this reads the build script itself:

    tree = fex_chain.copy_sources(fex_src, tmp)            # Source/, FEXCore/ and External/rpmalloc of the pinned tree
    fex_chain.run_chain(root, "ec", tree, before="patch-fex-ios-x.py")      # every script ahead of x
    ... run x itself, assert on the files ...
    fex_chain.run_chain(root, "ec", tree, after="patch-fex-ios-x.py")       # every script behind x still applies

kind is "ec" (tools/build-xtajit64.sh) or "wow" (tools/build-xtajit-wow64.sh). A script the pin's branch skips (the ARM64EC build's
`else` branch when the pin already carries the fork's overlays) is skipped here too. Returns the (script, returncode, output) list;
run_chain does not raise, the caller asserts.
"""
from pathlib import Path
import re, shlex, shutil, subprocess, sys

CALL = re.compile(r'^\s*python3 "\$R/tools/(patch-fex-ios-[A-Za-z0-9-]+\.py)"(.*)$')


def copy_sources(fex, dst):
    dst = Path(dst)
    for d in ("Source", "FEXCore"):
        shutil.copytree(fex / d, dst / d)
    rp = fex / "External/rpmalloc"
    if rp.is_dir():
        shutil.copytree(rp, dst / "External/rpmalloc")
    return dst


def chain_steps(root, kind, tree):
    """[(script name, argv)] in build order for this pin."""
    sh = (Path(root) / ("tools/build-xtajit64.sh" if kind == "ec" else "tools/build-xtajit-wow64.sh")).read_text()
    shvars = dict(re.findall(r'^([A-Z]+)="([^"$]*)"$', sh, re.M))
    pin_overlays = False
    if kind == "ec":
        mod = Path(tree) / "Source/Windows/ARM64EC/Module.cpp"
        pin_overlays = mod.is_file() and "ec-ret-direct-v1 madeira-bcd" in mod.read_text()
    branch = None
    steps = []
    for line in sh.splitlines():
        s = line.strip()
        if kind == "ec":
            if s.startswith('if grep -q "ec-ret-direct-v1'):
                branch = "if"
                continue
            if branch and s == "else":
                branch = "else"
                continue
            if branch and s == "fi":
                branch = None
                continue
            if branch == "if" and not pin_overlays:
                continue
            if branch == "else" and pin_overlays:
                continue
        m = CALL.match(line)
        if not m:
            continue
        args = m.group(2).split(" || ")[0]
        for k, v in shvars.items():
            args = args.replace("$" + k, v)
        args = args.replace('"$R/FEX"', str(tree)).replace("$R/FEX/", str(tree) + "/").replace("$R/", str(root) + "/")
        steps.append((m.group(1), shlex.split(args)))
    return steps


def run_chain(root, kind, tree, before=None, after=None):
    """Run the build's scripts: those ahead of `before`, or those behind `after` (both None: all). Returns [(script, rc, output)]."""
    steps = chain_steps(root, kind, tree)
    names = [n for n, _ in steps]
    lo, hi = 0, len(steps)
    if before:
        if before not in names:
            return [(before, 99, "not in the build script: " + before)]
        hi = names.index(before)
    if after:
        if after not in names:
            return [(after, 99, "not in the build script: " + after)]
        lo = names.index(after) + 1
    out = []
    for name, argv in steps[lo:hi]:
        r = subprocess.run([sys.executable, str(Path(root) / "tools" / name)] + argv, capture_output=True, text=True)
        out.append((name, r.returncode, (r.stdout + r.stderr).strip()))
    return out


def failures(results):
    return [(n, rc, o) for n, rc, o in results if rc != 0]
