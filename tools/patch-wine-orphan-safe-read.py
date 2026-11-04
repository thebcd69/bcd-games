#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The orphan-lock monitor reads a lock word without faulting (round 630, agent A2 S2).

The monitor (ios_orphan_check, every 250 ms) looks at the locks that three or more parked waiters wait on. It "checked" the page
with msync() and then read the word directly; msync() also succeeds on a PROT_NONE page, so when the waiters belonged to a 32-bit
program that had died and whose window BCD had already released, the read faulted on BCD's own native thread and the app ended
(the Steam client 9PB1B5, build 626: "[mach-deliver] ... NATIVE-THREAD DECLINE ... Madeira`ios_orphan_check", "[host-fault]
sig=10 ... on a NON-guest thread"). Both reads (the monitor's and ios_srw_reap_exclusive's) now go through
ios_alert_waiter_read() (tools/patch-wine-waiter-read.py: a Mach copy that answers 0 instead of faulting), and a word that cannot
be read is skipped. A readable word is read and used exactly as before.

Applied after tools/patch-wine-waiter-read.py (which defines the reader) and tools/patch-wine-lock-orphan.py, as CI does, to
wine/dlls/ntdll/unix/sync.c; the submodule is not committed to.

Usage: patch-wine-orphan-safe-read.py [wine/dlls/ntdll/unix/sync.c]
Idempotent; exits non-zero when an anchor is missing.
"""
import sys

MARKER = "madeira-bcd orphan-safe-read"

READER = "static int ios_alert_waiter_read( void *out, const void *addr, size_t size )\n{"
REAP_HEAD = "void ios_srw_reap_exclusive( unsigned long long lock_addr, unsigned long long dead_teb )\n{"
REAP_DECL = ("/* " + MARKER + " (tools/patch-wine-orphan-safe-read.py, round 630): the fault-safe reader, defined below */\n"
             "static int ios_alert_waiter_read( void *out, const void *addr, size_t size );\n\n")
REAP_READ = ("    do\n"
             "    {\n"
             "        old = *word;\n")
REAP_READ_NEW = ("    do\n"
                 "    {\n"
                 "        /* " + MARKER + ": a released window's page passes msync() but faults on a plain read (9PB1B5) */\n"
                 "        if (!ios_alert_waiter_read( &old, (const void *)word, sizeof(old) )) return;\n")
CHECK_READ = ("            if (msync( page, 0x4000, MS_ASYNC )) continue;\n"
              "            word = *(volatile unsigned int *)(ULONG_PTR)lock;\n")
CHECK_READ_NEW = ("            if (msync( page, 0x4000, MS_ASYNC )) continue;\n"
                  "            /* " + MARKER + " (round 630): msync() accepts a PROT_NONE page, the window a dead 32-bit program's\n"
                  "             * waiters pointed into (the Steam client 9PB1B5); a word that cannot be read is skipped */\n"
                  "            if (!ios_alert_waiter_read( &word, (const void *)(ULONG_PTR)lock, sizeof(word) )) continue;\n")


def patch(src):
    if MARKER in src:
        if src.count(REAP_READ_NEW) != 1 or src.count(CHECK_READ_NEW) != 1 or src.count(REAP_DECL) != 1:
            raise ValueError("partial or changed orphan-safe-read")
        return src
    if src.count(READER) != 1:
        raise ValueError("ios_alert_waiter_read is missing (apply tools/patch-wine-waiter-read.py first)")
    for name, anchor in (("ios_srw_reap_exclusive", REAP_HEAD), ("its read loop", REAP_READ), ("ios_orphan_check's read", CHECK_READ)):
        if src.count(anchor) != 1:
            raise ValueError("%s not found exactly once" % name)
    reap = src.index(REAP_HEAD)
    if not (reap < src.index(REAP_READ) < src.index(READER) < src.index(CHECK_READ)):
        raise ValueError("unexpected order of the reaper, the reader and the monitor")
    src = src.replace(REAP_HEAD, REAP_DECL + REAP_HEAD, 1)
    src = src.replace(REAP_READ, REAP_READ_NEW, 1)
    return src.replace(CHECK_READ, CHECK_READ_NEW, 1)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/sync.c"
    src = open(path).read()
    try:
        out = patch(src)
    except ValueError as error:
        sys.exit("patch-wine-orphan-safe-read: %s in %s" % (error, path))
    if out == src:
        print("patch-wine-orphan-safe-read: already patched")
        return
    open(path, "w").write(out)
    print("patch-wine-orphan-safe-read: applied to " + path)


if __name__ == "__main__":
    main()
