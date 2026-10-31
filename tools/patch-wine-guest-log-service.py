#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[guest-log] also mirrors what RockstarService.exe writes to a text file (madeira-bcd; logging only).

The Rockstar Games Launcher mirrors fine (launcher.log, tools/patch-wine-guest-log*.py), but the service it talks to
logs somewhere else under a name the session logs never showed: in the failing runs of builds 574/576 the Launcher
printed "Failed to connect to the Rockstar Games Library Service" (error 159) and nothing said what the service itself
thought. The mirror only reads *.log files and output_log.txt, only lines with a keyword (error, fail, ...), and only
64 lines of the whole session, which the Launcher's own start-up lines spend long before the service writes.

With the patch a process whose image is RockstarService.exe is mirrored differently (nobody else is):
  - any regular file it writes, whatever its name (the file's path is announced once, "[guest-log] file <path>");
  - every text line, not only the keyword ones;
  - its own budget of 60 lines ("[guest-log] svc #N tid=... text"), so the Launcher's lines neither use it up nor are
    cut by it.
Binary writes are still refused, MADEIRA_GUEST_LOG=0 still turns the whole mirror off, and for every other process
the mirror is the one it was (the budget, the keyword filter and the .log gate are not touched). Guest I/O results are
never changed.

Applied to the wine checkout after tools/patch-wine-guest-log.py and patch-wine-guest-log-tag.py (the same function) and
before "Build ntdll-unix"; the submodule is not committed to. Idempotent; exits non-zero, writing nothing, when an anchor
is missing (the mirror is not there, or its code moved).

Usage: patch-wine-guest-log-service.py [path/to/wine/dlls/ntdll/unix/file.c]
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
src = open(path).read()
marker = "madeira-bcd: [guest-log] service"
if marker in src:
    print("patch-wine-guest-log-service: already patched")
    sys.exit(0)

EDITS = [
    # (what, old, new)
    ("the mirror's counters",
     "    static unsigned int limit, emitted, announced;\n",
     "    static unsigned int limit, emitted, announced, svc_emitted;\n"),
    ("the mirror's locals",
     "    char decoded[4096];\n\n    if (m < 0)\n",
     "    char decoded[4096];\n    int svc = 0;   /* " + marker + " */\n\n    if (m < 0)\n"),
    ("the budget gate",
     "    if (!m || length < 5) goto out;\n"
     "    if (__atomic_load_n( &emitted, __ATOMIC_RELAXED ) >= __atomic_load_n( &limit, __ATOMIC_RELAXED )) goto out;\n",
     "    if (!m || length < 5) goto out;\n"
     "    svc = ios_guest_log_is_service();\n"
     "    if (svc ? __atomic_load_n( &svc_emitted, __ATOMIC_RELAXED ) >= IOS_GUEST_LOG_SERVICE_LINES\n"
     "            : __atomic_load_n( &emitted, __ATOMIC_RELAXED ) >= __atomic_load_n( &limit, __ATOMIC_RELAXED )) goto out;\n"),
    ("the keyword filter",
     "        if (m == 1 && !ios_guest_log_keep( (const char *)text + start, len )) continue;\n",
     "        if (m == 1 && !svc && !ios_guest_log_keep( (const char *)text + start, len )) continue;\n"),
    ("the file name gate",
     "            if ((!ext || strcasecmp( ext, \".log\" )) && strcasecmp( name, \"output_log.txt\" )) goto out;\n",
     "            if (!svc && (!ext || strcasecmp( ext, \".log\" )) && strcasecmp( name, \"output_log.txt\" )) goto out;\n"),
    ("the line counter",
     "        serial = __atomic_fetch_add( &emitted, 1, __ATOMIC_RELAXED );\n"
     "        if (serial >= __atomic_load_n( &limit, __ATOMIC_RELAXED )) break;\n",
     "        serial = __atomic_fetch_add( svc ? &svc_emitted : &emitted, 1, __ATOMIC_RELAXED );\n"
     "        if (serial >= (svc ? IOS_GUEST_LOG_SERVICE_LINES : __atomic_load_n( &limit, __ATOMIC_RELAXED ))) break;\n"),
    ("the printed line",
     "        dprintf( 2, \"[guest-log] #%u tid=%04x %.*s%s\\n\", serial + 1, (unsigned int)GetCurrentThreadId(),\n",
     "        dprintf( 2, \"[guest-log] %s#%u tid=%04x %.*s%s\\n\", svc ? \"svc \" : \"\", serial + 1,\n"
     "                 (unsigned int)GetCurrentThreadId(),\n"),
]

HELPER = r'''#define IOS_GUEST_LOG_SERVICE_LINES 60

/* ''' + marker + r''' (tools/patch-wine-guest-log-service.py): is the calling process RockstarService.exe, or (round 624,
 * agent R S27; ZTQN18: the installer left no log) Rockstar's launcher installer, Rockstar-Games-Launcher.exe? */
static int ios_guest_log_is_service( void )
{
    static const char *const want[] = { "rockstarservice.exe", "rockstar-games-launcher.exe" };
    const PEB *peb = NtCurrentTeb() ? NtCurrentTeb()->Peb : NULL;
    const RTL_USER_PROCESS_PARAMETERS *pp = peb ? peb->ProcessParameters : NULL;
    const WCHAR *w;
    size_t len, base = 0, k, n;

    if (!pp || !(w = pp->ImagePathName.Buffer)) return 0;
    len = pp->ImagePathName.Length / sizeof(WCHAR);
    for (k = 0; k < len; k++) if (w[k] == '\\' || w[k] == '/') base = k + 1;
    for (n = 0; n < sizeof(want) / sizeof(want[0]); n++)
    {
        size_t wl = strlen( want[n] );
        if (len - base != wl) continue;
        for (k = 0; k < wl; k++)
        {
            WCHAR c = w[base + k];
            if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
            if (c != (WCHAR)want[n][k]) break;
        }
        if (k == wl) return 1;
    }
    return 0;
}

'''

anchor = "static void ios_guest_log_mirror( int fd, const void *buffer, unsigned int length )\n{\n"
if "static void ios_guest_log_mirror" not in src or "guest-log-test:begin" not in src:
    sys.exit("patch-wine-guest-log-service: the [guest-log] mirror is not in %s (anchor: ios_guest_log_mirror)" % path)
if src.count(anchor) != 1:
    sys.exit("patch-wine-guest-log-service: ios_guest_log_mirror anchor found %d times in %s" % (src.count(anchor), path))
out = src.replace(anchor, HELPER + anchor)
for what, old, new in EDITS:
    if out.count(old) != 1:
        sys.exit("patch-wine-guest-log-service: anchor not found exactly once (%s) in %s: the mirror moved" % (what, path))
    out = out.replace(old, new)
open(path, "w").write(out)
print("patch-wine-guest-log-service: applied to " + path)
