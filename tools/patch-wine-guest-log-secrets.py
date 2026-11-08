#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Keep sign-in values (password=, token=, secret=) out of the [guest-log] mirror (build 632; log only).

A launcher's own log can carry a one-use sign-in value on a logged command line (the Epic launcher prints -AUTH_PASSWORD=<exchange code>).
With env.MADEIRA_GUEST_LOG = all the mirror copies such lines into the session log, and a Send Log uploads it. BCD's logs hold none today
(a scan of 3,255 logs found only Chromium pipe tokens, crashpad annotations, .NET PublicKeyToken= and empty accessToken= values), so this is
defence in depth: in every mirrored line the value after password=, token= and secret= (any case) is written as <redacted>; the value ends
at a space, a tab, a double quote or an ampersand. Every other line is as before. Because the key may end a longer word, PublicKeyToken=
values are hidden too; they are not needed to read a log.

The code is upstream Madeira's wine commit ac5faff18 ("ntdll: guest log mirror skips [DISPLAY] log-level tags and redacts sign-in values",
125hz, Wine fork madeira-lgpl, LGPL-2.1-or-later). Only its second half is taken, as this script: the [DISPLAY] half is
tools/patch-wine-guest-log-tag.py already, and as a commit in the Wine fork the whole would stop BCD's guest-log tag, service and account-name
scripts from applying (they anchor on the text it rewrites; tried).

Applied to the wine checkout after tools/patch-wine-guest-log.py and its tag, service, Ren'Py, trace and account-name patches (the same
function), before "Build ntdll-unix"; the submodule is not committed to. The redaction runs on the whole line first, then the line is cut to
300 bytes and the account-name cut looks at the redacted copy.

Usage: patch-wine-guest-log-secrets.py [path/to/wine/dlls/ntdll/unix/file.c]
Idempotent; exits non-zero, naming the anchor, when the mirror has moved.
"""
import sys

MARKER = "madeira-bcd: [guest-log] secrets redacted"
ANCHOR = "/* A BOM can be its own NtWriteFile call."

HELPER = r"""/* """ + MARKER + r""" (tools/patch-wine-guest-log-secrets.py, build 632; upstream Madeira's wine commit ac5faff18): the value of a NAME=value
 * whose name ends in password, token or secret (any case) is written as <redacted>; a store's one-use sign-in code is on a launcher's logged
 * command line. The value ends at a space, a tab, a double quote or an ampersand. Returns the length written to dst (at most capacity - 1). */
static unsigned int ios_guest_log_secrets( const char *src, unsigned int len, char *dst, unsigned int capacity )
{
    static const char *const keys[] = { "password=", "token=", "secret=" };
    unsigned int i = 0, o = 0, k;

    while (i < len && o + 1 < capacity)
    {
        for (k = 0; k < sizeof(keys) / sizeof(keys[0]); k++)
        {
            unsigned int kl = strlen( keys[k] ), j;
            if (i + kl > len) continue;
            for (j = 0; j < kl; j++)
            {
                char c = src[i + j];
                if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
                if (c != keys[k][j]) break;
            }
            if (j == kl) break;
        }
        if (k == sizeof(keys) / sizeof(keys[0]))
        {
            dst[o++] = src[i++];
            continue;
        }
        {
            static const char mask[] = "<redacted>";
            unsigned int kl = strlen( keys[k] ), m;
            for (m = 0; m < kl && o + 1 < capacity; m++) dst[o++] = src[i++];
            for (m = 0; mask[m] && o + 1 < capacity; m++) dst[o++] = mask[m];
            while (i < len && src[i] != ' ' && src[i] != '\t' && src[i] != '"' && src[i] != '&') i++;
        }
    }
    dst[o] = 0;
    return o;
}

"""

EDITS = (
    ("the mirror's locals",
     "    char decoded[4096];\n    int svc = 0;   /* madeira-bcd: [guest-log] service */\n",
     "    char decoded[4096], clean[4096 + 16];   /* madeira-bcd: [guest-log] secrets redacted */\n    int svc = 0;   /* madeira-bcd: [guest-log] service */\n"),
    ("the line print (after the account-name patch)",
     "        shown = len > 300 ? 300 : len;\n"
     "        /* Keep a truncated UTF-8 line valid, including decoded UTF-16. */\n"
     "        if (shown < len)\n"
     "            while (shown && (text[start + shown] & 0xc0) == 0x80) shown--;\n"
     "        {   /* madeira-bcd: [guest-log] account names redacted */\n"
     "            int redacted = ios_guest_log_redact( (const char *)text + start, &shown );\n"
     "            dprintf( 2, \"[guest-log] %s#%u tid=%04x %.*s%s\\n\", svc ? \"svc \" : \"\", serial + 1,\n"
     "                     (unsigned int)GetCurrentThreadId(), (int)shown, (const char *)text + start,\n"
     "                     redacted ? \" <account name removed>\" : shown < len ? \" [truncated]\" : \"\" );\n"
     "        }\n",
     "        len = ios_guest_log_secrets( (const char *)text + start, len, clean, sizeof(clean) );   /* " + MARKER + " */\n"
     "        shown = len > 300 ? 300 : len;\n"
     "        /* Keep a truncated UTF-8 line valid, including decoded UTF-16. */\n"
     "        if (shown < len)\n"
     "            while (shown && (clean[shown] & 0xc0) == 0x80) shown--;\n"
     "        {   /* madeira-bcd: [guest-log] account names redacted */\n"
     "            size_t cut = shown;   /* ios_guest_log_redact() takes a size_t *; shown is an unsigned int */\n"
     "            int redacted = ios_guest_log_redact( clean, &cut );\n"
     "            shown = cut;\n"
     "            dprintf( 2, \"[guest-log] %s#%u tid=%04x %.*s%s\\n\", svc ? \"svc \" : \"\", serial + 1,\n"
     "                     (unsigned int)GetCurrentThreadId(), (int)shown, clean,\n"
     "                     redacted ? \" <account name removed>\" : shown < len ? \" [truncated]\" : \"\" );\n"
     "        }\n"),
)


def apply(src, path="file.c"):
    if "static void ios_guest_log_mirror(" not in src or src.count(ANCHOR) != 1:
        raise SystemExit("patch-wine-guest-log-secrets: the [guest-log] mirror is not in %s (apply tools/patch-wine-guest-log.py first)" % path)
    if "madeira-bcd: [guest-log] account names redacted" not in src:
        raise SystemExit("patch-wine-guest-log-secrets: apply tools/patch-wine-guest-log-redact.py first (%s)" % path)
    for what, old, new in EDITS:
        if src.count(old) != 1:
            raise SystemExit("patch-wine-guest-log-secrets: anchor %s not found exactly once in %s (the mirror moved)" % (what, path))
        src = src.replace(old, new)
    return src.replace(ANCHOR, HELPER + ANCHOR)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
    src = open(path).read()
    if MARKER in src:
        print("patch-wine-guest-log-secrets: already patched")
        return
    out = apply(src, path)   # before the file is opened for writing: a refusal must leave it as it was
    open(path, "w").write(out)
    print("patch-wine-guest-log-secrets: applied to " + path)


if __name__ == "__main__":
    main()
