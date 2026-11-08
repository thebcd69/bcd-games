#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Epic's sign-in arguments are masked where the runtime prints a command line (milestone 2); no Wine runs.

build/bcd_epic_mask.h hides the one-time code (-AUTH_PASSWORD=), the account's ID (-epicuserid=) and its display name
(-epicusername=) in a text before it is logged. It is included by build/ntdll-unix/process_ios.c ("NtCreateUserProcess: ...")
and by app/Madeira/WineProcessBridge.m ("[WineProc] argv[...]"). This check compiles the production header, in its narrow and its
16-bit form, and holds both to the same cases, a deterministic fuzz (the output is never longer than the input, nothing is written
when nothing is masked, no value survives, a text without a key is untouched), and reads the two call sites.
Needs python3 and a C compiler (AddressSanitizer and UBSan when available).
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


HEADER = ("/* SPDX-License-Identifier: GPL-3.0-only */\n/* Copyright 2026 thebcd69 (BCD) */\n"
          "/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */\n")
header = (R / "build/bcd_epic_mask.h").read_text()
check(header.startswith(HEADER), "build/bcd_epic_mask.h: BCD's licence header, exactly")
check(not re.search(r"#include <(?!stddef\.h>)", header), "the header includes only <stddef.h> (pure: no library call)")

ntdll = (R / "build/ntdll-unix/process_ios.c").read_text()
bridge = (R / "app/Madeira/WineProcessBridge.m").read_text()
check('#include "../bcd_epic_mask.h"' in ntdll and "bcd_epic_mask_w(" in ntdll, "process_ios.c includes the header and masks the command line it prints")
# madeira-bcd (ajan62, D12): the printed copy goes through upstream's redactor (build/madeira_redact.h) after the Epic mask.
check(re.search(r'ERR\("NtCreateUserProcess: image=%s cmdline=%s\\n",\s*debugstr_us\( &params->ImagePathName \), madeira_debugstr_cmdline_us\( &shown \)\);', ntdll),
      "...the line prints the masked copy, not params->CommandLine (then redacted)")
check(len(re.findall(r'(?<!madeira_)debugstr_us\(\s*&params->CommandLine\s*\)', ntdll)) == 0
      and ntdll.count("madeira_debugstr_cmdline_us( &params->CommandLine )") == 1,
      "...and the only other print of the command line is the TRACE line (off unless Wine's debug channel is on), redacted")
check('#include "../../build/bcd_epic_mask.h"' in bridge and "bcd_epic_mask_a(extra_argv[i]" in bridge, "WineProcessBridge.m includes the header and masks its argv lines")
check('madeira_redact_arg_a(masked_len >= 0 ? masked : extra_argv[i], &credential_next, shown, sizeof(shown));' in bridge
      and 'dprintf(STDERR_FILENO, "[WineProc] argv[%d] = %s\\n", 2 + i, shown);' in bridge
      and 'dprintf(STDERR_FILENO, "[WineProc] argv[%d] = %s\\n", 2 + i, extra_argv[i]);' not in bridge,
      "...the loop prints the masked copy (then redacted)")
# No other place of the runtime or the bridge prints MADEIRA_ARGS or a whole command line at a level that is on in a build.
for path in [R / "app/Madeira/WineProcessBridge.m", R / "build/ntdll-unix/process_ios.c", R / "build/ntdll-unix/env_ios.c"]:
    text = path.read_text()
    check(not re.search(r'(dprintf|fprintf|NSLog|os_log|LOG)\([^;]*\bgetenv\("MADEIRA_ARGS"\)', text), f"{path.name}: MADEIRA_ARGS is never printed")

main = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "bcd_epic_mask.h"

static int fails, total;
#define CHECK(c, what) do { total++; if (!(c)) { fails++; printf("FAIL: %s\n", what); } } while (0)

/* Runs both forms on `in` and returns the narrow result (NULL when -1); the wide form must agree. */
static const char *both(const char *in, const char *what)
{
    static char outa[4096];
    unsigned short win[1024], wout[1100];
    size_t n = strlen(in);
    memset(outa, 0x7E, sizeof(outa));
    long a = bcd_epic_mask_a(in, n, outa);
    for (size_t i = 0; i <= n; i++) win[i] = (unsigned char)in[i];
    memset(wout, 0x7E, sizeof(wout));
    long w = bcd_epic_mask_w(win, n, wout);
    CHECK(a == w, what);
    if (a >= 0) for (long i = 0; i < a; i++) CHECK(wout[i] == (unsigned char)outa[i], what);
    CHECK(a < 0 || (size_t)a <= n, "a masked text is never longer");
    if (a < 0) { CHECK(outa[0] == 0x7E && wout[0] == 0x7E7E, "nothing written when nothing is masked"); return NULL; }
    outa[a] = 0;
    return outa;
}

#define EXPECT(in, want) do { const char *got = both(in, "narrow and wide agree: " in); CHECK(got && strcmp(got, want) == 0, "masks: " in); \
                             if (!got || strcmp(got, want)) printf("      got: %s\n", got ? got : "(none)"); } while (0)
#define UNTOUCHED(in) CHECK(both(in, "narrow and wide agree: " in) == NULL, "untouched: " in)

int main(void)
{
    EXPECT("-AUTH_LOGIN=unused -AUTH_PASSWORD=0123456789abcdef0123456789abcdef -AUTH_TYPE=exchangecode",
           "-AUTH_LOGIN=unused -AUTH_PASSWORD=* -AUTH_TYPE=exchangecode");
    EXPECT("-epicuserid=fedcba9876543210fedcba9876543210 -epiclocale=en", "-epicuserid=* -epiclocale=en");
    EXPECT("-epicusername=Plain -x", "-epicusername=* -x");
    EXPECT("-epicusername=\"Some One With Spaces\" -epicuserid=abc", "-epicusername=* -epicuserid=*");
    EXPECT("\"C:\\Games\\a.exe\" -windowed -AUTH_PASSWORD=aaaa -AUTH_TYPE=exchangecode -epicapp=Sugar -epicenv=Prod -epicovt=C:\\ProgramData\\BCD\\Epic\\x.ovt -EpicPortal -epicusername=\"A B\" -epicuserid=1234abcd -epiclocale=en -epicsandboxid=ns -epicdeploymentid=dep",
           "\"C:\\Games\\a.exe\" -windowed -AUTH_PASSWORD=* -AUTH_TYPE=exchangecode -epicapp=Sugar -epicenv=Prod -epicovt=C:\\ProgramData\\BCD\\Epic\\x.ovt -EpicPortal -epicusername=* -epicuserid=* -epiclocale=en -epicsandboxid=ns -epicdeploymentid=dep");
    EXPECT("-AUTH_PASSWORD=abc", "-AUTH_PASSWORD=*");
    EXPECT("-auth_password=abc -EPICUSERID=def -EpicUserName=ghi", "-auth_password=* -EPICUSERID=* -EpicUserName=*");
    EXPECT("-AUTH_PASSWORD=a\t-epicuserid=b", "-AUTH_PASSWORD=*\t-epicuserid=*");
    EXPECT("\"-AUTH_PASSWORD=abc\" x", "\"-AUTH_PASSWORD=*\" x");
    EXPECT("'-AUTH_PASSWORD=abc' x", "'-AUTH_PASSWORD=*' x");
    EXPECT("-epicusername=\"unclosed name -epicuserid=abc", "-epicusername=*");
    EXPECT("-epicusername=\"\" -x", "-epicusername=* -x");
    EXPECT("-AUTH_PASSWORD=*", "-AUTH_PASSWORD=*");
    EXPECT("-AUTH_PASSWORD= -epicuserid=abc", "-AUTH_PASSWORD= -epicuserid=*");
    EXPECT("-epicuserid=a -epicuserid=b -AUTH_PASSWORD=c", "-epicuserid=* -epicuserid=* -AUTH_PASSWORD=*");

    UNTOUCHED("");
    UNTOUCHED("-AUTH_PASSWORD");
    UNTOUCHED("-AUTH_PASSWORD=");
    UNTOUCHED("-epicuserid= ");
    UNTOUCHED("x-AUTH_PASSWORD=abc");
    UNTOUCHED("--AUTH_PASSWORD=abc");
    UNTOUCHED("AUTH_PASSWORD=abc");
    UNTOUCHED("-AUTH_LOGIN=unused -AUTH_TYPE=exchangecode -epicapp=Sugar -epicenv=Prod -EpicPortal -epiclocale=en -epicsandboxid=ns -epicdeploymentid=dep -epicovt=C:\\x.ovt");
    UNTOUCHED("\"C:\\Program Files\\Epic Games\\Game\\Game.exe\" -windowed -fullscreen");
    UNTOUCHED("-epicuser");
    UNTOUCHED("-epicusernam=x");

    /* 16-bit text: non-ASCII characters pass through, a value of them is masked. */
    {
        unsigned short in[] = { '-','e','p','i','c','u','s','e','r','n','a','m','e','=', 0x65E5, 0x672C, ' ', 0xD83C, 0xDFAE, 0 };
        unsigned short out[64];
        long n = bcd_epic_mask_w(in, 19, out);
        CHECK(n == 18 && out[14] == '*' && out[13] == '=' && out[15] == ' ' && out[16] == 0xD83C, "a wide value of non-ASCII characters is masked, the rest stays");
        unsigned short keep[] = { 0x65E5, 0x672C, ' ', '-', 'x', 0xD83C, 0xDFAE };
        CHECK(bcd_epic_mask_w(keep, 7, out) == -1, "wide text with no key is untouched");
        unsigned short mixed[] = { 0x65E5, ' ', '-','A','U','T','H','_','P','A','S','S','W','O','R','D','=', 0x65E5, ' ', 0x672C };
        n = bcd_epic_mask_w(mixed, 20, out);
        CHECK(n == 20 && out[0] == 0x65E5 && out[17] == '*' && out[18] == ' ' && out[19] == 0x672C, "non-ASCII before and after a masked value survive");
    }

    /* A deterministic fuzz. */
    unsigned long long rng = 0x9E3779B97F4A7C15ull;
    const char *pieces[] = { "-AUTH_PASSWORD=", "-epicuserid=", "-epicusername=", "-epicapp=", "\"", " ", "\t", "abc", "0123456789abcdef", "-", "=", "x", "'", "\\", "\xC3\xA9" };
    for (int round = 0; round < 200000; round++)
    {
        char text[400]; size_t n = 0;
        int count = 1 + (int)(rng % 9);
        for (int k = 0; k < count; k++)
        {
            rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
            const char *p = pieces[(rng >> 8) % (sizeof(pieces) / sizeof(pieces[0]))];
            size_t l = strlen(p);
            if (n + l < sizeof(text)) { memcpy(text + n, p, l); n += l; }
        }
        text[n] = 0;
        char out[400];
        memset(out, 0x7E, sizeof(out));
        long r = bcd_epic_mask_a(text, n, out);
        if (r < 0) { if (out[0] != 0x7E) { fails++; printf("FAIL: wrote with nothing masked: %s\n", text); } continue; }
        if ((size_t)r > n) { fails++; printf("FAIL: longer: %s\n", text); continue; }
        out[r] = 0;
        /* No value survives: after each key in the output comes '*', or the key's value was empty. */
        const char *keys[] = { "-auth_password=", "-epicuserid=", "-epicusername=" };
        for (int k = 0; k < 3; k++)
        {
            for (size_t i = 0; i + strlen(keys[k]) <= (size_t)r; i++)
            {
                size_t kl = strlen(keys[k]);
                int same = 1;
                for (size_t j = 0; j < kl; j++) { char c = out[i + j]; if (c >= 'A' && c <= 'Z') c += 32; if (c != keys[k][j]) same = 0; }
                if (!same) continue;
                char before = i ? out[i - 1] : ' ';
                if (before != ' ' && before != '\t' && before != '"' && before != '\'') continue;
                char after = i + kl < (size_t)r ? out[i + kl] : 0;
                if (after != '*' && after != 0 && after != ' ' && after != '\t' && !(after == before && (before == '"' || before == '\''))) { fails++; printf("FAIL: a value survived: %s -> %s\n", text, out); break; }
            }
        }
        /* The wide form agrees. */
        unsigned short win[400], wout[400];
        for (size_t i = 0; i < n; i++) win[i] = (unsigned char)text[i];
        long w = bcd_epic_mask_w(win, n, wout);
        if (w != r) { fails++; printf("FAIL: wide differs: %s\n", text); continue; }
        for (long i = 0; i < r; i++) if (wout[i] != (unsigned char)out[i]) { fails++; printf("FAIL: wide text differs: %s\n", text); break; }
    }
    printf("mask fuzz: 200000 texts\n");
    if (fails) { printf("epic mask checks: %d of %d FAILED\n", fails, total); return 1; }
    printf("epic mask checks: all %d passed\n", total);
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="bcd-epic-mask-") as tmp:
    src = Path(tmp) / "check.c"
    src.write_text(main)
    exe = Path(tmp) / "check"
    cc = os.environ.get("CC", "cc")
    flags = [cc, "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-I", str(R / "build"), str(src), "-o", str(exe)]
    sanitize = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    built = subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True, text=True)
    if built.returncode != 0:
        built = subprocess.run(flags, capture_output=True, text=True)
    check(built.returncode == 0, "the production header compiles (-Wall -Wextra -Werror)", built.stderr[-3000:])
    if built.returncode == 0:
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
        last = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ""
        fails = [l for l in run.stdout.splitlines() if l.startswith(("FAIL", "      got"))]
        check(run.returncode == 0 and re.fullmatch(r"epic mask checks: all \d+ passed", last), "the masks pass: " + last, "\n".join(fails[:12]) + run.stderr[-2000:])

print(f"check-epic-mask: {failures} FAILED" if failures else "check-epic-mask: all passed")
sys.exit(1 if failures else 0)
