#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Weak C imports from the system libraries in the built app (2026-11-20).

A C function that is newer than the app's oldest iOS is imported weakly: on an older iOS it resolves to NULL, and C code that calls
it without checking jumps to address 0. BCD 0.1.682 was the first build with the iOS 27 SDK; Wine's configure found pipe2() (new
in iOS 27's libc), and every game launch on iOS 26 and older crashed in server_pipe. Swift and Objective-C calls are guarded by the
compiler (availability checks, @backDeployed), so this looks only at libSystem, libc++ and libobjc imports.

  check-weak-libsystem.py <Madeira.app>     every Mach-O in the bundle; fails on a weak import outside KNOWN

A new name here is not always a bug: if the code checks the function for NULL (or the call sits behind __builtin_available), add it
to KNOWN with the reason. Otherwise make the build stop using it (for Wine: #undef its HAVE_ macro in config.h, as for pipe2).
"""
import re
import subprocess
import sys
from pathlib import Path

KNOWN = {
    "__availability_version_check": "the compiler's own availability check (__builtin_available)",
    "_os_sync_wait_on_address": "iOS 17.4; the app needs iOS 18",
    "_os_sync_wait_on_address_with_timeout": "iOS 17.4; the app needs iOS 18",
    "_os_sync_wake_by_address_all": "iOS 17.4; the app needs iOS 18",
    "_os_sync_wake_by_address_any": "iOS 17.4; the app needs iOS 18",
    "_pthread_mutexattr_init": "always present; Mesa declares its pthread use weak",
    "_pthread_mutexattr_settype": "always present; Mesa declares its pthread use weak",
    "_pthread_mutexattr_destroy": "always present; Mesa declares its pthread use weak",
}
LIBS = ("libSystem", "libc++", "libobjc")
LINE = re.compile(r"^\s*(?:0x[0-9A-Fa-f]+\s+)?(\S+)\s+\[weak-import\]\s+\(from ([^)]+)\)")


def weak_c_imports(listing):
    """The weak imports from LIBS in one `dyld_info -imports` listing."""
    out = []
    for line in listing.splitlines():
        m = LINE.match(line)
        if m and m.group(2).split("/")[-1].split(".")[0] in LIBS:
            out.append((m.group(1), m.group(2)))
    return out


def is_macho(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
    except OSError:
        return False


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    app = Path(sys.argv[1])
    bad, seen = [], 0
    for f in sorted(p for p in app.rglob("*") if p.is_file() and not p.is_symlink() and is_macho(p)):
        r = subprocess.run(["xcrun", "dyld_info", "-imports", str(f)], capture_output=True, text=True)
        seen += 1
        for sym, lib in weak_c_imports(r.stdout):
            if sym not in KNOWN:
                bad.append(f"{f.relative_to(app)}: {sym} (from {lib})")
    if bad:
        for b in bad:
            print(f"::error::weak C import newer than the app's oldest iOS: {b}")
        print(f"check-weak-libsystem: {len(bad)} unknown weak C import(s) in {seen} Mach-O files: on an older iOS each resolves "
              "to NULL and a call to it crashes (see this script's header)")
        return 1
    print(f"check-weak-libsystem: {seen} Mach-O files, no unknown weak C import")
    return 0


if __name__ == "__main__":
    sys.exit(main())
