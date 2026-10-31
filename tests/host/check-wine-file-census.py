#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-file-census.py: [file-census], ten-second windows of Wine's file lookups (round 624, agent D2 S19; log only).

Counter-Strike: Source (QJKJZJ, RQ8CSF) stalls in a CreateFileA storm and the log could not say whether the lookups hit, miss or scan. The
patch wraps find_file_in_dir (timing, a miss count) and counts lookup_unix_name's whole-path shortcut, the exact-name shortcut, directory
scans and the entries they read.

Checks: every lookup returns what the original returns (the wrapper passes the inner result through, the counters sit beside the original
statements, the case-sensitivity call keeps its short-circuit); the census helper compiled on the host with a fake clock: a window of 250
lookups prints one line with the counts and the slowest path, a window of 100 prints nothing, the off switch skips everything, 60 lines at
most; on the pinned Wine source (when present) the patch applies after the CI's earlier file.c patches and once only; the workflow runs it
right after the file trace.
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location('census', root / 'tools/patch-wine-file-census.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

check('the wrapper returns what the original returns, and calls it directly when the census is off',
      'status = madeira_fc_find_inner( root_fd, unix_name, pos, name, length, check_case );\n    madeira_fc_note( status, t0, unix_name );\n    return status;' in p.HELPER
      and 'if (!madeira_fc_on()) return madeira_fc_find_inner( root_fd, unix_name, pos, name, length, check_case );' in p.HELPER)
check('the original function becomes the inner one through a macro around its own definition only',
      p.HELPER.rstrip().endswith('#define find_file_in_dir madeira_fc_find_inner\n#endif'.rstrip())
      and p.FIND_NEW.endswith('#ifdef WINE_IOS\n#undef find_file_in_dir\n#endif'))
for name, old, new in (('the shortcut', p.SHORTCUT_OLD, p.SHORTCUT_NEW), ('the scan', p.SCAN_OLD, p.SCAN_NEW),
                       ('the readdir loop', p.ENTRY_OLD, p.ENTRY_NEW), ('the whole-path shortcut', p.FULL_OLD, p.FULL_NEW)):
    stripped = '\n'.join(l for l in new.split('\n') if 'madeira-bcd: [file-census]' not in l and l.strip() not in ('#ifdef WINE_IOS', '#endif'))
    check(f'{name}: only counters added (the original statements are unchanged)',
          stripped.replace('        if (!fstatat( root_fd, unix_name, &st, 0 ))\n        {\n            return STATUS_SUCCESS;\n        }',
                           '        if (!fstatat( root_fd, unix_name, &st, 0 )) return STATUS_SUCCESS;') == old, stripped)
check('the case-sensitivity call keeps its short-circuit (called only for a name that is not 8.3)',
      '!is_name_8_dot_3 && !(madeira_fc_case = get_dir_case_sensitivity( root_fd, unix_name ))' in p.CASE_NEW)

helper = p.HELPER.replace('#include <mach/mach_time.h>\n', '').replace('#ifdef WINE_IOS\n', '', 1)
helper = helper[:helper.rindex('#define find_file_in_dir madeira_fc_find_inner')]
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int NTSTATUS; typedef unsigned short WCHAR; typedef unsigned char BOOLEAN;
typedef struct { unsigned int numer, denom; } mach_timebase_info_data_t;
static unsigned long long now_ns;
static unsigned long long mach_absolute_time(void) { return now_ns; }
static void mach_timebase_info(mach_timebase_info_data_t *t) { t->numer = 1; t->denom = 1; }
static int inner_calls;
''' + helper + r'''
static NTSTATUS madeira_fc_find_inner( int root_fd, char *unix_name, int pos, const WCHAR *name, int length, BOOLEAN check_case )
{
    inner_calls++; now_ns += 1000;                      /* 1 us per lookup */
    if (length == 7) now_ns += 4000000;                 /* one slow lookup: 4 ms */
    return length == 3 ? 0xc0000034 : 0;                /* length 3: a miss */
}
int main(int argc, char **argv)
{
    char path[] = "/var/mobile/Containers/Data/Application/X/Documents/wine/drive_c/Steam/steamapps/common/cstrike/maps/de_dust2.bsp";
    int i, w;
    if (argc > 1) setenv("MADEIRA_FILE_CENSUS", "0", 1);
    now_ns = 1;
    for (w = 0; w < 64; w++)
    {
        int n = w == 1 ? 100 : 250;
        for (i = 0; i < n; i++) find_file_in_dir(0, path, 0, 0, i == 5 ? 7 : (i % 10 == 0 ? 3 : 4), 0);
        madeira_fc.full_tries += 2; madeira_fc.scans += 1; madeira_fc.entries += 40;
        now_ns += 10000000000ULL;                          /* the window is over */
        find_file_in_dir(0, path, 0, 0, 4, 0);             /* the next lookup prints the window */
    }
    fprintf(stderr, "inner=%d\n", inner_calls);
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the census helper compiles on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-2000:])
    if cc.returncode == 0:
        out = subprocess.run([str(exe)], capture_output=True, text=True).stderr
        lines = [l for l in out.splitlines() if l.startswith('[file-census]')]
        # the lookup that ends a window is counted in it (250 + 1)
        check('a window of 250 lookups prints one line with the counts, the share of the window and the slowest path',
              lines and 'component-lookups=251' in lines[0] and 'misses=25' in lines[0] and 'scans=1' in lines[0] and 'entries-scanned=40' in lines[0]
              and 'full-path-tries=2' in lines[0] and 'slowest=4.' in lines[0] and lines[0].endswith('maps/de_dust2.bsp') and 'case-sensitive=-1' in lines[0],
              '\n'.join(lines[:2]))
        check('a window of 100 lookups prints nothing, and 60 lines at most are printed in all', len(lines) == 60, str(len(lines)))
        off = subprocess.run([str(exe), 'off'], capture_output=True, text=True).stderr
        check('MADEIRA_FILE_CENSUS = 0: every lookup still runs, nothing is printed',
              '[file-census]' not in off and 'inner=' in off and off.split('inner=')[1].strip() == out.split('inner=')[1].strip(), off[-200:])

file_c = root / 'wine/dlls/ntdll/unix/file.c'
if file_c.is_file():
    r = subprocess.run(['git', '-C', str(root / 'wine'), 'show', 'HEAD:dlls/ntdll/unix/file.c'], capture_output=True, text=True)
    base = r.stdout if r.returncode == 0 and r.stdout else file_c.read_text()
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / 'file.c'
        f.write_text(base)
        for prior in ('patch-wine-guest-log', 'patch-wine-guest-log-tag', 'patch-wine-guest-log-service', 'patch-wine-guest-log-renpy', 'patch-wine-file-trace'):
            subprocess.run([sys.executable, str(root / 'tools' / (prior + '.py')), str(f)], capture_output=True, text=True)
        r1 = subprocess.run([sys.executable, str(root / 'tools/patch-wine-file-census.py'), str(f)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(root / 'tools/patch-wine-file-census.py'), str(f)], capture_output=True, text=True)
        check('on the pinned Wine source it applies after the CI\'s earlier file.c patches, once',
              r1.returncode == 0 and 'patched' in r1.stdout and r2.returncode == 0 and 'already patched' in r2.stdout, r1.stdout + r1.stderr)
else:
    print('SKIP: no wine sources (check out the submodule)')

wf = (root / '.github/workflows/build-ipa.yml').read_text()
step = 'python3 tools/patch-wine-file-census.py wine/dlls/ntdll/unix/file.c'
check('the workflow checks it and applies it right after the file trace',
      step in wf and 'python3 tests/host/check-wine-file-census.py' in wf
      and wf.index('python3 tools/patch-wine-file-trace.py wine/dlls/ntdll/unix/file.c') < wf.index(step))
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
