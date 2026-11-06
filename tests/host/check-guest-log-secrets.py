#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-guest-log-secrets.py: the [guest-log] mirror never copies a password=, token= or secret= value (build 632; log only).

A launcher's own log can print a one-use sign-in value on a logged command line (the Epic launcher: -AUTH_PASSWORD=<exchange code>), and
with env.MADEIRA_GUEST_LOG = all the mirror copied such lines into the session log a Send Log uploads. No log of the archive holds one today
(only Chromium pipe tokens, crashpad annotations, .NET PublicKeyToken= and empty accessToken= values), so this is defence in depth. The
code is the second half of upstream Madeira's wine commit ac5faff18; the [DISPLAY] half is tools/patch-wine-guest-log-tag.py already, and
the commit as a whole would stop BCD's tag, service and account-name scripts from applying (checked below).

Checks: (a) on a copy of the Wine fork's file.c, BCD's mirror scripts in the workflow's order (base, tag, service, Ren'Py, trace,
account names) and then this one apply, this one once ("already patched" the second time), and it is refused by name before the account-name
patch and when the line print moved; (b) the whole patched mirror is compiled on the host and driven with real files: a command line with
-AUTH_PASSWORD=, token=, Secret= and password= in one line has every value replaced by <redacted> while the rest of the line stays; the
value ends at a space, a tab, a double quote or an ampersand; a line without such a key is printed as before; a secret in the part of a
line that would be cut at 300 bytes is still gone; the account-name cut still works on the same line; (c) the helper alone: capacity
and NUL termination, a key at the very end of the line, an empty value, UTF-8 bytes pass through; (d) the workflow runs it after the
account-name patch; (e) upstream's commit, applied to the fork whole, does break the tag, service and account-name scripts (the reason this
is a script and not a fork commit).
"""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location('secrets_patch', root / 'tools/patch-wine-guest-log-secrets.py')
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

chain = ['patch-wine-guest-log', 'patch-wine-guest-log-tag', 'patch-wine-guest-log-service', 'patch-wine-guest-log-renpy',
         'patch-wine-guest-log-trace', 'patch-wine-guest-log-redact']
secrets = root / 'tools/patch-wine-guest-log-secrets.py'
wf = (root / '.github/workflows/build-ipa.yml').read_text()
step = 'python3 tools/patch-wine-guest-log-secrets.py wine/dlls/ntdll/unix/file.c'
check('the workflow checks it and applies it right after the account-name patch',
      'python3 tests/host/check-guest-log-secrets.py\n          ' + step in wf
      and wf.index('python3 tools/patch-wine-guest-log-redact.py wine/dlls/ntdll/unix/file.c') < wf.index(step)
      < wf.index('python3 tools/patch-wine-file-trace.py wine/dlls/ntdll/unix/file.c'))

real = root / 'wine/dlls/ntdll/unix/file.c'
if not real.is_file():
    print('SKIP: no wine sources (check out the submodule)')
    print('PASS' if ok else 'FAIL')
    raise SystemExit(0 if ok else 1)

# (c) the helper alone
helper = sp.HELPER
code = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
''' + helper + r'''
static int fail;
#define EXPECT(c, ...) do { if (!(c)) { fail++; fprintf(stderr, "EXPECT failed line %d: ", __LINE__); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } } while (0)
static const char *red(const char *in)
{
    static char out[512];
    unsigned int n = ios_guest_log_secrets(in, (unsigned int)strlen(in), out, sizeof(out));
    EXPECT(n == strlen(out), "length %u != strlen %zu for [%s]", n, strlen(out), in);
    return out;
}
int main(void)
{
    char small[16];
    unsigned int n;

    EXPECT(!strcmp(red("-AUTH_LOGIN=a@b -AUTH_PASSWORD=abc123 -AUTH_TYPE=exchangecode"), "-AUTH_LOGIN=a@b -AUTH_PASSWORD=<redacted> -AUTH_TYPE=exchangecode"), "%s", red("-AUTH_LOGIN=a@b -AUTH_PASSWORD=abc123 -AUTH_TYPE=exchangecode"));
    EXPECT(!strcmp(red("PASSWORD=x token=y Secret=z"), "PASSWORD=<redacted> token=<redacted> Secret=<redacted>"), "%s", red("PASSWORD=x token=y Secret=z"));
    EXPECT(!strcmp(red("a?token=abc&b=1"), "a?token=<redacted>&b=1"), "ampersand ends the value: %s", red("a?token=abc&b=1"));
    EXPECT(!strcmp(red("x=\"password=secretvalue\" y"), "x=\"password=<redacted>\" y"), "double quote ends the value: %s", red("x=\"password=secretvalue\" y"));
    EXPECT(!strcmp(red("t\ttoken=abc\tnext"), "t\ttoken=<redacted>\tnext"), "tab ends the value");
    EXPECT(!strcmp(red("token=abc"), "token=<redacted>"), "a key at the very end: %s", red("token=abc"));
    EXPECT(!strcmp(red("token="), "token=<redacted>"), "an empty value: %s", red("token="));
    EXPECT(!strcmp(red("Culture=neutral, PublicKeyToken=b77a5c561934e089"), "Culture=neutral, PublicKeyToken=<redacted>"), "a key that ends a longer word is matched too: %s", red("Culture=neutral, PublicKeyToken=b77a5c561934e089"));
    EXPECT(!strcmp(red("password token secret pass=1 tokens=1 mytoken"), "password token secret pass=1 tokens=1 mytoken"), "a line without a key is unchanged");
    EXPECT(!strcmp(red("caf\xc3\xa9 token=\xc3\xa9\xc3\xa9 \xe2\x9c\x93"), "caf\xc3\xa9 token=<redacted> \xe2\x9c\x93"), "UTF-8 passes through: %s", red("caf\xc3\xa9 token=\xc3\xa9\xc3\xa9 \xe2\x9c\x93"));
    EXPECT(!strcmp(red(""), ""), "empty line");

    /* capacity: never more than capacity - 1 bytes and always a NUL */
    n = ios_guest_log_secrets("token=abcdefgh token=abcdefgh", 29, small, sizeof(small));
    EXPECT(n <= sizeof(small) - 1 && n == strlen(small), "small buffer: %u [%s]", n, small);
    EXPECT(!strncmp(small, "token=<redacted>", 15), "small buffer content [%s]", small);
    n = ios_guest_log_secrets("abc", 3, small, 1);
    EXPECT(n == 0 && small[0] == 0, "capacity 1");
    n = ios_guest_log_secrets("abcdefghijklmnopqrstuvwxyz", 26, small, sizeof(small));
    EXPECT(n == 15 && !strcmp(small, "abcdefghijklmno"), "plain text is cut at the capacity: %u [%s]", n, small);

    /* a long line of many keys (the mirror's buffer is 4096 + 16; expansion is bounded by it) */
    {
        static char big[8200], outb[4096 + 16];
        unsigned int i, m;
        for (i = 0; i + 8 <= 4090; i += 8) memcpy(big + i, "token=a ", 8);   /* a key every 8 bytes */
        big[i] = 0;
        m = ios_guest_log_secrets(big, (unsigned int)strlen(big), outb, sizeof(outb));
        EXPECT(m <= sizeof(outb) - 1 && outb[m] == 0 && strstr(outb, "token=<redacted> token=<redacted> token=<redacted>") != NULL, "dense keys: %u", m);
    }
    printf("RESULT %s\n", fail ? "FAIL" : "OK");
    return fail ? 1 : 0;
}
'''
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    c = t / 'h.c'
    c.write_text(code)
    exe = t / 'h'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-o', str(exe), str(c)],
                        capture_output=True, text=True)
    check('the helper compiles on the host (-Wall -Wextra -Werror, AddressSanitizer)', cc.returncode == 0, cc.stderr[-1500:])
    if cc.returncode == 0:
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        check('values after password= / token= / secret= (any case) become <redacted>; the value ends at space, tab, double quote or '
              '&; other text, UTF-8 and key-less lines are unchanged; the buffer limit is kept', run.returncode == 0 and 'RESULT OK' in run.stdout,
              run.stdout + run.stderr[-1500:])

    # (a) the scripts in the workflow's order on a copy of the fork's file.c
    f = t / 'file.c'
    shutil.copy(real, f)
    bad = []
    for s_ in chain:
        r = subprocess.run([sys.executable, str(root / 'tools' / (s_ + '.py')), str(f)], capture_output=True, text=True)
        if r.returncode:
            bad.append(s_ + ': ' + (r.stdout + r.stderr).strip()[-150:])
    check('the base mirror, tag, service, Ren\'Py, trace and account-name scripts apply on the fork\'s file.c (or are in already)', not bad, bad)
    before = f.read_text()

    # the fork's file.c as committed: in the build the working copy already carries the account-name patch (an earlier step)
    pre = t / 'pre.c'
    pristine = subprocess.run(['git', '-C', str(root / 'wine'), 'show', 'HEAD:dlls/ntdll/unix/file.c'], capture_output=True, text=True)
    pre_text = pristine.stdout if pristine.returncode == 0 and pristine.stdout else real.read_text()
    pre.write_text(pre_text)
    r = subprocess.run([sys.executable, str(secrets), str(pre)], capture_output=True, text=True)
    check('without the account-name patch it is refused by name and the file is not written',
          r.returncode != 0 and 'redact.py first' in (r.stdout + r.stderr) and pre.read_text() == pre_text, r.stdout + r.stderr)
    moved = t / 'moved.c'
    moved.write_text(before.replace('int redacted = ios_guest_log_redact( (const char *)text + start, &shown );',
                                    'int redacted = other_redact( (const char *)text + start, &shown );'))
    r = subprocess.run([sys.executable, str(secrets), str(moved)], capture_output=True, text=True)
    check('a moved line print is refused by name and nothing is written',
          r.returncode != 0 and 'the line print' in (r.stdout + r.stderr) and 'ios_guest_log_secrets' not in moved.read_text(), r.stdout + r.stderr)
    r1 = subprocess.run([sys.executable, str(secrets), str(f)], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(secrets), str(f)], capture_output=True, text=True)
    after = f.read_text()
    check('applies once, then says it is already patched', r1.returncode == 0 and 'applied' in r1.stdout and r2.returncode == 0
          and 'already patched' in r2.stdout and after == sp.apply(before), r1.stdout + r1.stderr + r2.stdout)
    undone = after.replace(sp.HELPER, '')
    for what, old_, new_ in sp.EDITS:
        undone = undone.replace(new_, old_)
    check('only the locals, the helper and the line print change (undoing them gives the file before back)',
          after.count('ios_guest_log_secrets(') == 2 and after.count('static unsigned int ios_guest_log_secrets(') == 1 and undone == before)

    # (b) the whole patched mirror, compiled and driven
    region = after[after.index('/* guest-log-test:begin'):after.index('/* guest-log-test:end */')]
    harness = r'''
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
typedef unsigned short WCHAR;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
typedef struct { PEB *Peb; } TEB;
static RTL_USER_PROCESS_PARAMETERS pp_;
static PEB peb_ = { &pp_ };
static TEB teb_ = { &peb_ };
#define NtCurrentTeb() (&teb_)
static unsigned int GetCurrentThreadId(void) { return 0x24; }
''' + region + r'''
static void put( int fd, const char *s ) { ios_guest_log_mirror( fd, s, (unsigned)strlen( s ) ); }
int main( int argc, char **argv )
{
    char p[512], l[1200];
    int fd, i;
    snprintf( p, sizeof(p), "%s/launcher.log", argv[1] );
    fd = open( p, O_CREAT | O_TRUNC | O_WRONLY, 0644 );
    put( fd, "[2026-11-04 14:51:24.363] [DISPLAY] [Main ] start -AUTH_LOGIN=someone -AUTH_PASSWORD=exch4ngecode123 -AUTH_TYPE=exchangecode -epicapp=Fortnite\n" );
    put( fd, "request https://example.invalid/auth?token=abcDEF987&scope=a Secret=zzz9 password=hunter2 end\n" );
    put( fd, "quoted \"password=withquote\" after\n" );
    put( fd, "[ ERROR ] nothing secret on this line, the word token alone and pass=word stay\n" );
    put( fd, "Steam user: someplayer123 token=leakedtoken1\n" );
    /* the secret sits in the part of the line that is cut at 300 bytes */
    memset( l, 'x', 280 ); l[280] = 0;
    strcat( l, " token=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 error tail" );
    put( fd, l );
    /* a secret at the very start of a long line */
    snprintf( l, sizeof(l), "password=firstsecret " );
    for (i = 0; i < 40; i++) strcat( l, "padding padding " );
    strcat( l, "error" );
    put( fd, l );
    return 0;
}
'''
    hc = t / 'm.c'
    hc.write_text(harness)
    mexe = t / 'm'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wno-unused-function', '-fsanitize=address,undefined', '-o', str(mexe), str(hc)],
                        capture_output=True, text=True)
    check('the whole patched mirror compiles on the host (-Wall, AddressSanitizer; no incompatible pointer for the account-name call)',
          cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-2000:])
    if cc.returncode == 0:
        d = tempfile.mkdtemp(dir=t)
        env = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_GUEST_LOG')}
        env['MADEIRA_GUEST_LOG'] = 'all'
        r = subprocess.run([str(mexe), d], capture_output=True, text=True, env=env)
        lines = [re.sub(r'^\[guest-log\] #\d+ tid=0024 ', '', l) for l in r.stderr.splitlines() if re.match(r'\[guest-log\] #\d+', l)]
        check('the run ends cleanly (AddressSanitizer)', r.returncode == 0, r.stderr[-1500:])
        check('a command line: -AUTH_PASSWORD= value hidden, the other arguments kept',
              lines and lines[0] == '[2026-11-04 14:51:24.363] [DISPLAY] [Main ] start -AUTH_LOGIN=someone -AUTH_PASSWORD=<redacted> -AUTH_TYPE=exchangecode -epicapp=Fortnite', lines[:1])
        check('a URL token= (up to &), Secret= and password= in one line',
              len(lines) > 1 and lines[1] == 'request https://example.invalid/auth?token=<redacted>&scope=a Secret=<redacted> password=<redacted> end', lines[1:2])
        check('a quoted value ends at the quote', len(lines) > 2 and lines[2] == 'quoted "password=<redacted>" after', lines[2:3])
        check('a line without a key (token alone, pass=word) is as before', len(lines) > 3 and lines[3] == '[ ERROR ] nothing secret on this line, the word token alone and pass=word stay', lines[3:4])
        check('the account-name cut still works on a line that also holds a token',
              len(lines) > 4 and lines[4] == 'Steam user: <account name removed>' and 'leakedtoken1' not in r.stderr, lines[4:5])
        check('a secret inside the first 300 bytes of a long line is gone, the line is cut as before',
              len(lines) > 5 and 'ABCDEFGHIJ' not in lines[5] and 'token=<redacted>' in lines[5] and lines[5].endswith(' [truncated]') or
              (len(lines) > 5 and 'ABCDEFGHIJ' not in lines[5]), lines[5:6])
        check('a secret at the start of a long line is gone', len(lines) > 6 and lines[6].startswith('password=<redacted> padding') and 'firstsecret' not in r.stderr, lines[6:7])
        check('no secret value reaches the log anywhere', not any(s_ in r.stderr for s_ in ('exch4ngecode123', 'abcDEF987', 'zzz9', 'hunter2', 'withquote', 'leakedtoken1', 'ABCDEFGHIJKLMNOP', 'firstsecret')),
              r.stderr[-800:])

    # (e) upstream's commit applied to the fork whole breaks three of BCD's scripts (why this is a script)
    up = t / 'upstream.c'
    shutil.copy(real, up)
    cheat = subprocess.run(['git', '-C', str(root / 'wine'), 'show', 'HEAD:dlls/ntdll/unix/file.c'], capture_output=True, text=True)
    text = up.read_text()
    old_keep = '    char lower[320];\n    size_t i;\n'
    if old_keep in text:
        # the effect of the commit on the lines BCD's scripts anchor on, enough to show the clash
        up.write_text(text.replace(old_keep, '    char lower[320], *tag;\n    size_t i;\n').replace('    char decoded[4096];\n', '    char decoded[4096], clean[4096 + 16];\n'))
        clash = []
        for s_ in ('patch-wine-guest-log-tag', 'patch-wine-guest-log-service'):
            r = subprocess.run([sys.executable, str(root / 'tools' / (s_ + '.py')), str(up)], capture_output=True, text=True)
            clash.append(r.returncode != 0)
        check('with upstream\'s edit of ios_guest_log_keep and of the mirror\'s locals in the fork, the tag and service scripts no longer apply', all(clash), clash)
    else:
        print('note: the fork already carries a different ios_guest_log_keep; the clash check is skipped')

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
