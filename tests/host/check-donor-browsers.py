#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_DONOR_BROWSERS = 0: a Chromium browser's ranges never take helper memory (upstream Madeira v0.2.0 #516); no Wine runs.

Upstream's log 99 (an 8 GB phone): the 4 GB PartitionAlloc range of Social Club's helper, of which it touches a few MB, took the whole donor
pool (a whole reservation takes the pool by its size), the game never had a byte of it and iOS wrote its hot pages to flash. BCD is
pool-first for every process, and the swap-mb 9216 rows (RDR2, Rift Apart, HFW) let a helper's 4-8 GB ranges take the pool whenever the guard
is open; no archive log shows it happening (DFRKWK/RS64VG have the helper's ranges on the file with the guard closed), so it is latent and
keyed. Unset or anything but "0": today's behaviour, byte for byte (the process test is not even run). "0": SocialClubHelper.exe and
steamwebhelper.exe (ios_avail_phys_chromium) skip the pool in ios_swap_map and go on to the file (or stay anonymous when the tier has no
file / is pool-only).

Source shape: the only pool take is in ios_swap_map and carries the new condition, nothing else about the pool changed. A compiled run of the
production switch against a model process test: key unset/other values never ask the process; key 0 asks it, answers per process, prints one line.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(sig):
    a = src.index(sig)
    return src[a:src.index('\n}\n', a) + 3]


fn = function('static int ios_swap_browser_file_only( void )')
swap_map = function('static int ios_swap_map( void *base, size_t size, int unix_prot, int committed )')
check('ios_swap_map takes from the pool only through the new test, in the one place it did before',
      swap_map.count('ios_pool_take( len )') == 1 and
      'off = ios_swap_browser_file_only() ? (uint64_t)-1 : ios_pool_take( len );' in swap_map and
      src.count('ios_pool_take( len )') == 1,   # the definitions (pool build and stub) have a different parameter list: this call is the only use
      str(src.count('ios_pool_take( len )')))
check('"no offset" is what the pool answers when it has nothing, so the code after it (the pool-only exit, the disk cap, the file) is unchanged',
      swap_map.index('ios_swap_browser_file_only()') < swap_map.index('if (off != (uint64_t)-1)') < swap_map.index('if (ios_swap_pool_only) return IOS_SW_SMALL;') <
      swap_map.index('off = ios_swap_take( len );'))
check('the key is read once; only "0" turns the behaviour on', 'getenv( "MADEIRA_DONOR_BROWSERS" )' in fn and "off = (e && e[0] == '0' && !e[1]);" in fn and
      src.count('MADEIRA_DONOR_BROWSERS" )') == 1)
check('the process test runs only with the key at 0 (short circuit)', 'return off && ios_avail_phys_chromium();' in fn)
check('the helper names the browsers it means: Social Club\'s helper and Valve\'s web helper',
      '"socialclubhelper.exe", "steamwebhelper.exe"' in function('static int ios_avail_phys_chromium( void )\n{'))

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#define dprintf(fd, ...) fprintf(stderr, __VA_ARGS__)
static int process_is_browser, asked;
static int ios_avail_phys_chromium(void) { asked++; return process_is_browser; }
@FN@
int main(void)
{
    int a, b;
    asked = 0;
    process_is_browser = 1; a = ios_swap_browser_file_only();
    process_is_browser = 0; b = ios_swap_browser_file_only();
    printf("%d %d %d\n", a, b, asked);
    return 0;
}
'''
harness = harness.replace('@FN@', fn)
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-g', '-fsanitize=address,undefined', '-Wall', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production switch compiles on the host (ASAN/UBSAN)', cc.returncode == 0, cc.stderr[-1500:])
    if cc.returncode == 0:
        base = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
        for value, expect, lines in ((None, '0 0 0', 0), ('1', '0 0 0', 0), ('', '0 0 0', 0), ('00', '0 0 0', 0), ('off', '0 0 0', 0), ('0', '1 0 2', 1)):
            env = dict(base)
            if value is not None:
                env['MADEIRA_DONOR_BROWSERS'] = value
            r = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
            check('MADEIRA_DONOR_BROWSERS=%r: browser/other/process tests = %s, %d log line(s)' % (value, expect, lines),
                  r.returncode == 0 and r.stdout.strip() == expect and r.stderr.count('[swap] Chromium browsers') == lines, r.stdout + r.stderr[-500:])

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-donor-browsers.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
