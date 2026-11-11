#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's ws2_32 can refuse Valve's crash-upload host at once (night round 2026-11-11, T2 S-GM1; opt-in).

Garry's Mod (Steam 4000, BCD Dock, Send Log GYG6SG, build 630, iPad14,8 under LiveContainer; RDR2 2PGRBB the same on 2026-11-01): the Steam client in
the Dock host runs its crash handler twice while it starts, and the handler connects to crash.steampowered.com:443. On a network that drops that host
the TCP connect never completes: `[srv-conn-done] dport=443 event=|HUP err=60` 74 s after the connect, again 75 s later, each followed by wininet's
12029; only then does the client log on, and the game process is created 3 min 50 s after Play. The upload is useless in BCD (the dump is refused by the
minidump gate, the reporter process by the process gate), and no game uses that host for anything visible.

With env.MADEIRA_DNS_REFUSE_CRASH = 1 the DNS wrapper (madeira_dns_getaddrinfo, dlls/ws2_32/unixlib.c, the one that already logs every `[dns]` lookup)
answers an exact, case-insensitive "crash.steampowered.com" with EAI_NONAME (WSAHOST_NOT_FOUND to the program) before any lookup, and logs
`[dns] madeira-bcd refused #N "crash.steampowered.com" = EAI_NONAME at once (MADEIRA_DNS_REFUSE_CRASH)`; wininet then fails with 12007 at once instead of
12029 after 75 s. Every other name, and every lookup with the key unset or 0, is exactly what it was. The default below (DEFAULT_REFUSE) is the policy
switch: 0 = opt-in (this build of the patch); 1 = refuse unless MADEIRA_DNS_REFUSE_CRASH = 0 (owner question f13: a default for every Wine process).
The ws2_32 unix library is built from the pinned tree, which already carries the wrapper; this script only adds the refusal to it. Idempotent; fails by
name when the wrapper moved.
Usage: patch-wine-dns-refuse-crash.py [WINE-DIR | path/to/wine/dlls/ws2_32/unixlib.c]
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: dns refuse crash"
FILE = "dlls/ws2_32/unixlib.c"
DEFAULT_REFUSE = 0

FUNC_ANCHOR = ("static int madeira_dns_getaddrinfo( const char *node, const char *service, const struct addrinfo *hints,\n"
               "                                    struct addrinfo **res )\n")
HELPER = (
    "/* " + MARKER + " (tools/patch-wine-dns-refuse-crash.py, night round 2026-11-11, Garry's Mod GYG6SG): Valve's crash upload blocked the Dock host's\n"
    " * start for 150 s on a network that drops crash.steampowered.com. env.MADEIRA_DNS_REFUSE_CRASH = 1 answers that exact name with EAI_NONAME at once;\n"
    " * unset, the default below. */\n"
    "#include <strings.h>\n"
    "static int madeira_dns_refuse_crash( const char *node )\n"
    "{\n"
    "    static int refuse = -1;\n"
    "\n"
    "    if (!node || strcasecmp( node, \"crash.steampowered.com\" )) return 0;\n"
    "    if (refuse < 0)\n"
    "    {\n"
    "        const char *e = getenv( \"MADEIRA_DNS_REFUSE_CRASH\" );\n"
    "        refuse = e && e[0] ? e[0] == '1' : %d;\n"
    "    }\n"
    "    return refuse;\n"
    "}\n"
    "\n") % DEFAULT_REFUSE
CALL_OLD = ("    clock_gettime( CLOCK_MONOTONIC, &t0 );\n"
            "\n"
            "    *res = NULL;\n"
            "    if (!timeout_ms)\n")
CALL_NEW = ("    if (madeira_dns_refuse_crash( node ))   /* " + MARKER + " */\n"
            "    {\n"
            "        *res = NULL;\n"
            "        fprintf( stderr, \"[dns] madeira-bcd refused #%d \\\"%s\\\" = EAI_NONAME at once (MADEIRA_DNS_REFUSE_CRASH)\\n\", n, node );\n"
            "        return EAI_NONAME;\n"
            "    }\n" + CALL_OLD)


def patch(src):
    if MARKER in src:
        if src.count(HELPER) != 1 or src.count(CALL_NEW) != 1:
            raise ValueError("partial or changed dns refuse-crash patch")
        return src
    if src.count(FUNC_ANCHOR) != 1:
        raise ValueError("madeira_dns_getaddrinfo of ws2_32/unixlib.c not found once (the wrapper moved: tools/patch-wine-dns-trace.py)")
    if src.count(CALL_OLD) != 1:
        raise ValueError("the clock_gettime / lookup lines of madeira_dns_getaddrinfo changed")
    if not src.index(FUNC_ANCHOR) < src.index(CALL_OLD) < src.index(FUNC_ANCHOR) + 4000:
        raise ValueError("the lookup is no longer inside madeira_dns_getaddrinfo")
    return src.replace(FUNC_ANCHOR, HELPER + FUNC_ANCHOR, 1).replace(CALL_OLD, CALL_NEW, 1)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-dns-refuse-crash.py [WINE-DIR | path/to/wine/dlls/ws2_32/unixlib.c]")
    arg = Path(sys.argv[1])
    path = arg if arg.suffix == ".c" else arg / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-dns-refuse-crash: " + str(error))
    if result == source:
        print("patch-wine-dns-refuse-crash: already patched")
    else:
        path.write_text(result)
        print("patch-wine-dns-refuse-crash: patched")


if __name__ == "__main__":
    main()
