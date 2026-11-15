#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""NtQueryInformationToken does not read past its length table (build 660; Wine commit 35f715b4eff by Nikola Kuburovic, made after Wine 11.4
and cherry-picked into BCD's fork); no Wine runs.

The function looks up the fixed length of an information class in a table of 42 entries (classes 0 to 41, TokenProcessTrustLevel) but guarded
the lookup with `class < MaxTokenInfoClass`, and Wine's winnt.h has nine classes after 41 (TokenPrivateNameSpace 42 ... TokenLearningMode 50).
A query for one of those read whatever follows the table in the image as the required length: a caller with a smaller buffer got
STATUS_BUFFER_TOO_SMALL and a meaningless *retlen instead of the function's own answer for an unsupported class. The guard is now the size
of the table.

No BCD log can show this (nothing is logged), so there is no auto-tune rule. Keyless: classes 0 to 41 are untouched; only the nine classes
the table does not have change, from an undefined length to 0.

Checks (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none, or when the one there
lacks the change and is not this checkout's pin): the table and the guard, cut from the function and compiled on the host with the real
enumeration values: the table has 42 entries, MaxTokenInfoClass is larger, every class from 0 to 41 gets the same length from the new guard
as from the old one, and every class from 42 up gets 0. The CI runs this.
Needs python3 and a C compiler.
"""
from pathlib import Path
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
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


pin = ''
tree = git('ls-tree', 'HEAD', 'wine')
if tree:
    pin = tree.split()[2]
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'dlls/ntdll/unix/security.c').is_file():
        wine = Path(candidate)
        break
security_c = (wine / 'dlls/ntdll/unix/security.c').read_text() if wine else ''
has_change = 'if (class < ARRAY_SIZE(info_len)) len = info_len[class];' in security_c
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    f = security_c[security_c.index('NTSTATUS WINAPI NtQueryInformationToken( HANDLE token, TOKEN_INFORMATION_CLASS class,'):]
    table = f[f.index('    static const ULONG info_len [] =\n'):f.index('    };\n') + 7]
    check('the guard is the size of the table, and the old guard is gone',
          'if (class < ARRAY_SIZE(info_len)) len = info_len[class];' in f[:f.index('if (length < len) return STATUS_BUFFER_TOO_SMALL;')]
          and 'class < MaxTokenInfoClass' not in f[:f.index('if (length < len) return STATUS_BUFFER_TOO_SMALL;')])
    winnt = (wine / 'include/winnt.h').read_text()
    enum = winnt[winnt.index('typedef enum _TOKEN_INFORMATION_CLASS'):]
    enum = enum[:enum.index('}')]
    last = int(re.search(r'TokenProcessTrustLevel\s*=\s*(\d+)', enum).group(1))
    values = [int(v) for v in re.findall(r'=\s*(\d+)', enum)]
    max_class = max(values) + 1
    check('winnt.h has classes after the table\'s last one (TokenProcessTrustLevel = %d, MaxTokenInfoClass = %d)' % (last, max_class),
          last == 41 and max_class > 42)
    # the sizes are stand-ins (distinct, non-zero): what is checked is which entry a class reads, not the Windows sizes
    harness = r'''
#include <stdio.h>
typedef unsigned int ULONG; typedef unsigned int DWORD;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
typedef struct { char x[11]; } TOKEN_SOURCE; typedef struct { char x[12]; } TOKEN_TYPE; typedef struct { char x[13]; } SECURITY_IMPERSONATION_LEVEL;
typedef struct { char x[14]; } TOKEN_STATISTICS; typedef struct { char x[15]; } TOKEN_ELEVATION_TYPE; typedef struct { char x[16]; } TOKEN_LINKED_TOKEN;
typedef struct { char x[17]; } TOKEN_ELEVATION; typedef struct { char x[18]; } TOKEN_MANDATORY_LABEL; typedef struct { char x[19]; } SID;
typedef struct { char x[20]; } TOKEN_APPCONTAINER_INFORMATION;
int main(void)
{
TABLE
    unsigned int class, n = ARRAY_SIZE(info_len);
    printf( "%u\n", n );
    for (class = 0; class < MAXCLASS + 8; class++)
    {
        ULONG len = 0;
        if (class < ARRAY_SIZE(info_len)) len = info_len[class];
        printf( "%u %u %u\n", class, len, class < n ? info_len[class] : 0 );
    }
    return 0;
}
'''
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='token-info-bounds-'))
    try:
        (tmp / 't.c').write_text(harness.replace('TABLE', table).replace('MAXCLASS', str(max_class)))
        r = subprocess.run([cc, '-std=gnu11', '-O1', '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('the table compiles on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            lines = subprocess.run([str(tmp / 't')], capture_output=True, text=True).stdout.split('\n')
            n = int(lines[0])
            rows = [tuple(int(x) for x in l.split()) for l in lines[1:] if l]
            check('the table has %d entries, one for each class up to TokenProcessTrustLevel' % n, n == last + 1 == 42)
            check('classes 0 to 41 read their own entry', all(length == own for c, length, own in rows if c < n) and any(length for c, length, own in rows if c < n))
            check('the %d classes from 42 to MaxTokenInfoClass and anything above read nothing: length 0' % (max_class - n),
                  all(length == 0 for c, length, own in rows if c >= n) and len([1 for c, _, _ in rows if c >= n]) == max_class - n + 8)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-token-info-bounds.py' in ci)

print('PASS: token information bounds' if ok else 'FAILED: token information bounds')
sys.exit(0 if ok else 1)
