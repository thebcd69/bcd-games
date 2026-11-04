#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Keep the player's account names out of the [guest-log] mirror (round 630).

A launcher's own log names the player's accounts: Rockstar's launcher.log writes "Steam user: <name>", "Saved profile for
<name>", "Signed in online to profile: <name>" and "Gamer:"<name>"[handle=SC <number>]". With env.MADEIRA_GUEST_LOG = all (the
GTA IV row since build 626) the mirror copied those lines into the session log, and a Send Log uploads it (agent R, round 630).
The mirror now keeps such a line up to that word and ends it with "<account name removed>"; every other line is as before.

Applied to the wine checkout after tools/patch-wine-guest-log.py and its tag, service, Ren'Py and trace patches (the same
function), before "Build ntdll-unix"; the submodule is not committed to.

Usage: patch-wine-guest-log-redact.py [path/to/wine/dlls/ntdll/unix/file.c]
Idempotent; exits non-zero when an anchor is missing (the mirror moved).
"""
import sys

MARKER = "madeira-bcd: [guest-log] account names redacted"
ANCHOR = "/* A BOM can be its own NtWriteFile call."
HELPER = r"""/* """ + MARKER + r""" (tools/patch-wine-guest-log-redact.py, round 630): a launcher's own log names the
 * player's accounts (Rockstar's launcher.log); the line is kept up to the word that names the account, so an uploaded log never
 * holds the name. Returns 1 when the line was cut. */
static int ios_guest_log_redact( const char *line, size_t *shown )
{
    static const char *const marks[] = { "Steam user:", "Saved profile for", "Signed in online to profile:", "Gamer:", "handle=SC ",
                                         "Signed in as", "Logged in as", "Username:", "UserName:", "username=", "Nickname:",
                                         "Display name:", "Account name:" };
    size_t best = *shown, k, i, ml;
    int hit = 0;

    for (k = 0; k < sizeof(marks) / sizeof(marks[0]); k++)
    {
        ml = strlen( marks[k] );
        for (i = 0; i + ml <= *shown; i++)
        {
            if (memcmp( line + i, marks[k], ml )) continue;
            if (i + ml < best) best = i + ml;
            hit = 1;
            break;
        }
    }
    if (hit) *shown = best;
    return hit;
}

"""
PRINT = ("        dprintf( 2, \"[guest-log] %s#%u tid=%04x %.*s%s\\n\", svc ? \"svc \" : \"\", serial + 1,\n"
         "                 (unsigned int)GetCurrentThreadId(),\n"
         "                 (int)shown, (const char *)text + start, shown < len ? \" [truncated]\" : \"\" );\n")
NEW_PRINT = ("        {   /* " + MARKER + " */\n"
             "            int redacted = ios_guest_log_redact( (const char *)text + start, &shown );\n"
             "            dprintf( 2, \"[guest-log] %s#%u tid=%04x %.*s%s\\n\", svc ? \"svc \" : \"\", serial + 1,\n"
             "                     (unsigned int)GetCurrentThreadId(), (int)shown, (const char *)text + start,\n"
             "                     redacted ? \" <account name removed>\" : shown < len ? \" [truncated]\" : \"\" );\n"
             "        }\n")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
    src = open(path).read()
    if MARKER in src:
        print("patch-wine-guest-log-redact: already patched")
        return
    if "static void ios_guest_log_mirror(" not in src or src.count(ANCHOR) != 1:
        sys.exit("patch-wine-guest-log-redact: the [guest-log] mirror is not in %s (apply tools/patch-wine-guest-log.py first)" % path)
    if src.count(PRINT) != 1:
        sys.exit("patch-wine-guest-log-redact: the mirror's line print (after the service patch) was not found exactly once in %s" % path)
    out = src.replace(ANCHOR, HELPER + ANCHOR).replace(PRINT, NEW_PRINT)
    open(path, "w").write(out)
    print("patch-wine-guest-log-redact: applied to " + path)


if __name__ == "__main__":
    main()
