#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Compiler availability warnings newer than the app's oldest iOS (2026-11-20).

clang warns "'pipe2' is only available on iOS 27.0 or newer [-Wunguarded-availability-new]" when C or Objective-C code calls an
API newer than the deployment target without an availability check. The warning comes from the SDK headers, so it describes real
devices: the iOS 26.4 simulator's libsystem_kernel exports pipe2, an iPadOS 26.6.2 iPad does not, and 0.1.682 crashed every game
launch there. This turns such warnings in the build's diagnostics into an error.

  check-availability-warnings.py --min 18.0 <file or dir> [...]   every *.err and *.log under each dir, and each file given
"""
import argparse
import re
import sys
from pathlib import Path

WARN = re.compile(r"'([^']+)' is only available on iOS (\d+(?:\.\d+)*) or newer")


def version(s):
    return tuple(int(x) for x in s.split("."))


def findings(text, minimum):
    """{(api, version)} of the warnings in `text` above `minimum`."""
    return {(m.group(1), m.group(2)) for m in WARN.finditer(text) if version(m.group(2)) > minimum}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", required=True, help="the app's MinimumOSVersion, e.g. 18.0")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    minimum = version(a.min)
    files = []
    for p in map(Path, a.paths):
        if p.is_dir():
            files += [f for f in p.rglob("*") if f.is_file() and f.suffix in (".err", ".log")]
        elif p.is_file():
            files.append(p)
    bad = {}
    for f in files:
        for api, ver in findings(f.read_text(errors="replace"), minimum):
            bad.setdefault((api, ver), f)
    for (api, ver), f in sorted(bad.items()):
        print(f"::error::{api} is only available on iOS {ver}, the app runs on iOS {a.min}: called without an availability check "
              f"(first seen in {f})")
    print(f"check-availability-warnings: {len(files)} file(s), {len(bad)} API(s) newer than iOS {a.min} called unguarded")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
