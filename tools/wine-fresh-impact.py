#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Which Wine modules does a set of source changes reach? (tools/build-wine-fresh-dlls.sh, FRESH_AARCH64=patched; tests/host/check-win64-fresh.py)

  wine-fresh-impact.py PIN_TREE PATCHED_TREE     prints the module directories (dlls/<d>, programs/<p>) whose build input changed, one a line

A changed file counts for the module directory it is in (dlls/<d>/..., programs/<p>/...; never dlls/ntdll/unix/ and server/, which are not PE code), for every
module whose Makefile.in names the directory in PARENTSRC and lists the changed file in its own SOURCES (xinput1_1 builds xinput1_3's main.c), and, for a change under libs/vkd3d, for every module whose
Makefile.in links libvkd3d (VKD3D_PE_LIBS: wined3d; the others that name VKD3D_PE_CFLAGS only include its headers). Other trees (include/, libs/wine ...) are not changed by any patch script; one that was would need its
own rule, and the script says so (exit 2) rather than guess.
"""
import filecmp
import os
import re
import sys
from pathlib import Path


def changed(pin, new):
    out = []
    for dirpath, dirs, files in os.walk(new):
        rel = os.path.relpath(dirpath, new)
        dirs[:] = [d for d in dirs if not (rel == "." and (d.startswith("build-") or d == ".git"))]
        for f in files:
            if rel == "." and (f == ".git" or f.startswith("build-")):
                continue
            p = os.path.join(rel, f) if rel != "." else f
            o = os.path.join(pin, p)
            if not os.path.exists(o) or not filecmp.cmp(o, os.path.join(new, p), shallow=False):
                out.append(p)
    for dirpath, _dirs, files in os.walk(pin):
        rel = os.path.relpath(dirpath, pin)
        for f in files:
            p = os.path.join(rel, f) if rel != "." else f
            if not os.path.exists(os.path.join(new, p)):
                out.append(p)
    return sorted(out)


def main():
    pin, new = sys.argv[1], sys.argv[2]
    files = changed(pin, new)
    parents, vkd3d_users, child_text = {}, set(), {}
    for kind in ("dlls", "programs"):
        for mk in Path(new, kind).glob("*/Makefile.in"):
            t = mk.read_text(errors="replace")
            m = re.search(r"^PARENTSRC\s*=\s*\.\./(\S+)", t, re.M)
            if m:
                parents.setdefault(m.group(1), set()).add("%s/%s" % (kind, mk.parent.name))
                child_text["%s/%s" % (kind, mk.parent.name)] = t
            if "VKD3D_PE_LIBS" in t:
                vkd3d_users.add("%s/%s" % (kind, mk.parent.name))
    mods, unknown = set(), []
    for p in files:
        if "/unix/" in p or p.startswith("server/") or p.endswith((".orig", ".rej")):
            continue
        m = re.match(r"(dlls|programs)/([^/]+)/", p)
        if m:
            d = "%s/%s" % (m.group(1), m.group(2))
            mods.add(d)
            base = os.path.basename(p)
            for c in parents.get(m.group(2), set()):   # a PARENTSRC child counts only when its own SOURCES name the changed file
                if re.search(r"(?<![\w.])" + re.escape(base) + r"(?![\w.])", child_text[c]):
                    mods.add(c)
        elif p.startswith("libs/vkd3d/"):
            mods |= vkd3d_users
        else:
            unknown.append(p)
    if unknown:
        print("wine-fresh-impact: a changed file is outside dlls/, programs/ and libs/vkd3d: %s" % " ".join(unknown[:5]), file=sys.stderr)
        sys.exit(2)
    for d in sorted(mods):
        print(d)


if __name__ == "__main__":
    main()
