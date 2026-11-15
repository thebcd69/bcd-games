#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The [guest-log] mirror remembers its last 16 lines, keeps a JVM fatal-error report and reads console.txt (round 660, M032; log only).

Three changes to the mirror (ios_guest_log_mirror in wine/dlls/ntdll/unix/file.c), all on what it prints, nothing else:
- a line seen among the last 16 is not printed again; the one-line memory printed an alternating exception / stack-line pair for ever
  (Cult of the Lamb NN2D05);
- the 80 lines after a JVM fatal-error header ("# A fatal error has been detected ...") from the same file are kept whatever their words:
  the exception, pc, problematic frame and registers follow on lines without the mirror's keywords (Project Zomboid's hs_err report);
- console.txt is read as a log (Project Zomboid's Java log), next to *.log and output_log.txt.

Applied LAST to the mirror: after tools/patch-wine-guest-log.py and its tag, service, Ren'Py, trace, account-name and sign-in-value scripts,
whose anchors are the lines this one rewrites (round 660 first made these changes inside tools/patch-wine-guest-log.py, which kept those
scripts from applying: build 661's host checks found it). Before "Build ntdll-unix"; the submodule is not committed to.

Usage: patch-wine-guest-log-ring.py [path/to/wine/dlls/ntdll/unix/file.c]
Idempotent; exits non-zero, writing nothing, when an anchor is missing (the mirror moved).
"""
import sys

MARKER = "madeira-bcd: [guest-log] ring of 16"

EDITS = [
    ("the mirror's line memory",
     "    static unsigned long long previous_hash, file_hashes[8];\n",
     "    static unsigned long long recent_hash[16], file_hashes[8];   /* " + MARKER + " (round 660, M032): the last 16 lines */\n"
     "    static unsigned int recent_at, fatal_left;\n"
     "    static int fatal_fd = -1;   /* a JVM fatal-error report: its next lines are kept whatever their words */\n"),
    ("the trace and keyword filters",
     "        if (m == 1 && ios_guest_log_trace( (const char *)text + start, len )) goto keep_line;   /* madeira-bcd: [guest-log] trace */\n"
     "        if (m == 1 && !svc && !ios_guest_log_keep( (const char *)text + start, len )) continue;\n",
     "        if (len >= 32 && !memcmp( text + start, \"# A fatal error has been detected\", 32 ))\n"
     "        {   /* madeira-bcd (round 660, M032): Java's hs_err header; the exception, pc, frame and registers follow on lines without the words */\n"
     "            __atomic_store_n( &fatal_fd, fd, __ATOMIC_RELAXED );\n"
     "            __atomic_store_n( &fatal_left, 80, __ATOMIC_RELAXED );\n"
     "        }\n"
     "        if (m == 1 && ios_guest_log_trace( (const char *)text + start, len )) goto keep_line;   /* madeira-bcd: [guest-log] trace */\n"
     "        if (m == 1 && !svc && !(fd == __atomic_load_n( &fatal_fd, __ATOMIC_RELAXED ) && __atomic_load_n( &fatal_left, __ATOMIC_RELAXED )) &&\n"
     "            !ios_guest_log_keep( (const char *)text + start, len )) continue;\n"),
    ("the file-name gate",
     "            /* only *.log and output_log.txt; never arbitrary text files */\n"
     "            if (!svc && (!ext || strcasecmp( ext, \".log\" )) && strcasecmp( name, \"output_log.txt\" ) && !ios_guest_log_renpy_file( name )) goto out;\n",
     "            /* only *.log, output_log.txt and console.txt (Project Zomboid's Java log; round 660, M032); never arbitrary text files */\n"
     "            if (!svc && (!ext || strcasecmp( ext, \".log\" )) && strcasecmp( name, \"output_log.txt\" ) && strcasecmp( name, \"console.txt\" ) &&\n"
     "                !ios_guest_log_renpy_file( name )) goto out;\n"),
    ("the repeat test",
     "        if (__atomic_exchange_n( &previous_hash, hash, __ATOMIC_RELAXED ) == hash) continue;\n",
     "        {   /* madeira-bcd (round 660, M032): a line seen among the last 16 is not printed again (an alternating exception/stack-line\n"
     "             * pair, which the one-line memory printed for ever: Cult of the Lamb NN2D05) */\n"
     "            int seen = 0;\n"
     "            for (j = 0; j < 16 && !seen; j++) seen = __atomic_load_n( &recent_hash[j], __ATOMIC_RELAXED ) == hash;\n"
     "            if (seen) continue;\n"
     "            __atomic_store_n( &recent_hash[__atomic_fetch_add( &recent_at, 1, __ATOMIC_RELAXED ) & 15], hash, __ATOMIC_RELAXED );\n"
     "        }\n"
     "        if (fd == __atomic_load_n( &fatal_fd, __ATOMIC_RELAXED ) && __atomic_load_n( &fatal_left, __ATOMIC_RELAXED ))\n"
     "            __atomic_fetch_sub( &fatal_left, 1, __ATOMIC_RELAXED );\n"),
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
    src = open(path).read()
    if MARKER in src:
        print("patch-wine-guest-log-ring: already patched")
        return 0
    if "static void ios_guest_log_mirror(" not in src:
        sys.exit("patch-wine-guest-log-ring: the [guest-log] mirror is not in %s" % path)
    if "madeira-bcd: [guest-log] secrets redacted" not in src:
        sys.exit("patch-wine-guest-log-ring: apply tools/patch-wine-guest-log-secrets.py first (%s)" % path)
    out = src
    for what, old, new in EDITS:
        if out.count(old) != 1:
            sys.exit("patch-wine-guest-log-ring: %s not found exactly once in %s (found %d): the mirror moved" % (what, path, out.count(old)))
        out = out.replace(old, new)
    open(path, "w").write(out)
    print("patch-wine-guest-log-ring: applied to " + path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
