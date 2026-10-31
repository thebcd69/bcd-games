#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[guest-log] keeps the managed stack trace under an exception line (madeira-bcd, round 624; logging only).

Blasphemous (JPZBGJ, build 622, agent B S2 (d)): the game's own handler wrote "Ups! There was an unhandled exception:
NullReferenceException" to output_log.txt and the mirror took that line (it has the word "exception"), but not the stack
frames under it ("  at Foo.Bar () [0x00000] in <filename unknown>:0"): they have no keyword, so the log could not say which
call failed. With the patch the default mode (keyword lines) keeps, right after a kept line that holds "exception:" (any
case: "NullReferenceException: ...", "... unhandled exception: ..."), up to 12 lines that start with "at " after blanks
(Mono's and .NET's frames) or with "(Filename:" (Unity's trailer); 36 such lines a process at most. A line of any other kind
ends the trace. The lines count against the same budget (64 by default), and everything else is as it was: the file-name
gate, the de-duplication, MADEIRA_GUEST_LOG=all|0 and the Rockstar service's own mirror. Guest I/O is never changed.

Applied to the wine checkout after tools/patch-wine-guest-log.py and its tag, service and Ren'Py patches (the same function)
and before "Build ntdll-unix"; the submodule is not committed to (the BCD wine mirror already carries the base mirror, so a
change of the base patch would not reach a build: hence a patch of its own). Works with and without the service patch's
gate. Idempotent; exits non-zero, writing nothing, when an anchor is missing.

Usage: patch-wine-guest-log-trace.py [path/to/wine/dlls/ntdll/unix/file.c]
"""
import sys

MARKER = "madeira-bcd: [guest-log] trace"

ANCHOR = "/* A BOM can be its own NtWriteFile call."

HELPER = r"""/* """ + MARKER + r""" (tools/patch-wine-guest-log-trace.py, round 624, Blasphemous JPZBGJ): a managed stack frame
 * ("  at A.B () [0x00000] in <filename unknown>:0", .NET's "   at A.B()") or Unity's "(Filename: ... Line: N)" after a trace, and an
 * exception line that starts one ("NullReferenceException: ...", "... unhandled exception: ..."). */
static int ios_guest_log_frame( const char *line, size_t len )
{
    size_t i = 0;

    while (i < len && (line[i] == ' ' || line[i] == '\t')) i++;
    if (i && len - i >= 3 && line[i] == 'a' && line[i + 1] == 't' && line[i + 2] == ' ') return 1;
    return len >= 10 && !strncmp( line, "(Filename:", 10 );
}

static int ios_guest_log_exception( const char *line, size_t len )
{
    size_t i;

    for (i = 0; i + 10 <= len; i++)
        if (!strncasecmp( line + i, "exception:", 10 )) return 1;
    return 0;
}

/* 1: a frame of the trace that follows an exception line, kept although it has no keyword (12 after each exception line, 36 a
 * process, still inside the line budget). Any other line ends the trace; an exception line (kept by its keyword) starts a new one. */
static int ios_guest_log_trace( const char *line, size_t len )
{
    static unsigned int trace_left, trace_total;

    if (__atomic_load_n( &trace_left, __ATOMIC_RELAXED ) && ios_guest_log_frame( line, len )
        && __atomic_add_fetch( &trace_total, 1, __ATOMIC_RELAXED ) <= 36)
    {
        __atomic_sub_fetch( &trace_left, 1, __ATOMIC_RELAXED );
        return 1;
    }
    __atomic_store_n( &trace_left, ios_guest_log_exception( line, len ) ? 12 : 0, __ATOMIC_RELAXED );
    return 0;
}

"""

# The keyword filter as tools/patch-wine-guest-log.py writes it, and as tools/patch-wine-guest-log-service.py leaves it.
GATES = ("        if (m == 1 && !ios_guest_log_keep( (const char *)text + start, len )) continue;\n",
         "        if (m == 1 && !svc && !ios_guest_log_keep( (const char *)text + start, len )) continue;\n")
CALL = ("        if (m == 1 && ios_guest_log_trace( (const char *)text + start, len )) goto keep_line;   /* " + MARKER + " */\n")
LABEL = "keep_line:\n"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
    src = open(path).read()
    if MARKER in src:
        print("patch-wine-guest-log-trace: already patched")
        return
    if "static void ios_guest_log_mirror(" not in src or src.count(ANCHOR) != 1:
        sys.exit("patch-wine-guest-log-trace: the [guest-log] mirror is not in %s (apply tools/patch-wine-guest-log.py first)" % path)
    found = [g for g in GATES if src.count(g) == 1]
    if len(found) != 1 or sum(src.count(g) for g in GATES) != 1:
        sys.exit("patch-wine-guest-log-trace: the mirror's keyword filter was not found exactly once in %s" % path)
    gate = found[0]
    out = src.replace(ANCHOR, HELPER + ANCHOR).replace(gate, CALL + gate + LABEL)
    open(path, "w").write(out)
    print("patch-wine-guest-log-trace: applied to " + path)


if __name__ == "__main__":
    main()
