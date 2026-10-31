#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 624's log-only lines in the unix ntdll: where they are printed and what bounds them (no Wine runs; text checks).

Each line answers a question a round-624 log could not:
  [sub-floor-image] ml1300 (virtual_ios.c): which program file the game loaded at a fixed address below 4 GB before "Disable Mandatory
      ASLR" (The First Berserker: Khazan SD2T3N/GWFN34, Demon Slayer 2): native images only, 16 lines a session at most, inside the ml938
      sub-floor block (so only an image whose preferred base is below 4 GB);
  [x86_obj] @RBX-0x30..-8 (signal_arm64_ios.c): the 48 bytes before RBX next to the existing @RBX+0..+0x30 row (the KINGDOM HEARTS launcher's
      NULL allocator, 8X1X2V/7EHABT/S5EPGT), through the same fault-safe Mach read, only for an RBX at or above 0x10030;
  [va-owners] (virtual_ios.c): whose big reservations fill the map when a big reserve is refused (GTA V Legacy S59TH0: Social Club's Chromium
      helper; 7 more logs of the class): every [bigres-use] range carries the PEB that reserved it; per owner, ranges, reserved and committed
      MB and whether that process still runs; the five largest ranges with their owner; 3 blocks a session at most (compiled and run here);
  [footprint] rev=r624 90% of the limit (virtual_ios.c): once a session, with the thread count, and the next full cycle walks [phys-map]
      (Counter-Strike: Source CB02MM, killed at the 2.3 GB limit of a 4 GB phone): from the fast sampler and the slow cycle both;
  [reg-miss] (wineserver request_ios.c): with env.MADEIRA_REG_TRACE = 1 only, each failed registry request's request and reply trace, 400
      at most (Far Cry 3's updater: "Cannot find essential information in the registry"); the filter is compiled and run here.
A line that changes behaviour does not belong here; these only print.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


virt = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
blk = virt[virt.index('    /* ml938: this image wanted a base iOS will never give us'):]
blk = blk[:blk.index('ios_register_subfloor_image( (unsigned long long)image_info->base,')]
check('[sub-floor-image] ml1300 is printed in the ml938 sub-floor block (preferred base below 4 GB), before the window is registered',
      'image_info->base && image_info->base < 0x100000000ull' in blk and '"[sub-floor-image] ml1300 %s: preferred %#llx mapped at %p (delta %#llx), %s, DYNAMIC_BASE=%d\\n"' in blk)
check('... for the program\'s own images only (not Wine\'s builtins), 16 lines a session at most',
      'if (!image_info->wine_builtin)' in blk and 'if (InterlockedIncrement( &subfloor_told ) <= 16)' in blk)
check('... reading DllCharacteristics from the header of the image\'s own kind (PE32 or PE32+)',
      '((IMAGE_NT_HEADERS64 *)nt)->OptionalHeader.DllCharacteristics' in blk and '((IMAGE_NT_HEADERS32 *)nt)->OptionalHeader.DllCharacteristics' in blk)
check('... and it prints only', virt.count('[sub-floor-image]') == 1 and 'subfloor_told' not in virt.replace(blk, ''))

sig = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
row = sig[sig.index('"[x86_obj] @RBX+0..+0x30:'):]
row = row[:row.index('/* Also dump [RCX..RCX+0x30]')]
check('[x86_obj] @RBX-0x30..-8 follows the @RBX+0..+0x30 row, only for RBX >= 0x10030, through mach_vm_read_overwrite',
      'if (live_rbx >= 0x10030)' in row and 'mach_vm_read_overwrite(mach_task_self(), (mach_vm_address_t)(live_rbx - 0x30), sizeof(before),' in row
      and '"[x86_obj] @RBX-0x30..-8: %016llx %016llx %016llx %016llx %016llx %016llx\\n"' in row and sig.count('@RBX-0x30') == 1)

# [va-owners]: the table carries the owner, the report runs at the three refusals, and its output on a fake table
note = virt[virt.index('static void ios_bigres_note( void *base, size_t size )\n{'):]
note = note[:note.index('\n}\n') + 3]
check('every big reservation records the PEB that made it, and a reused slot starts its counts at zero',
      'static struct { uint64_t base, size, committed; unsigned commits; void *owner; } ios_bigres_tab[IOS_BIGRES_MAX];' in virt
      and '.owner = ios_jit_current_peb();' in note and '.committed = 0;' in note and '.commits = 0;' in note)
check('the owner report runs at a complete "NO GAP FITS" walk and at the two refused jumbo reserves, nowhere else',
      virt.count('ios_va_owners_report( "') == 3 and 'if (!truncated) ios_va_owners_report( "no gap fits" );' in virt
      and 'ios_bigres_report( "jumbo reserve failed" ); ios_va_owners_report( "jumbo reserve failed" );' in virt
      and 'ios_bigres_report( "hinted jumbo retry exhausted" ); ios_va_owners_report( "hinted jumbo retry exhausted" );' in virt)
rep = virt[virt.index('static void ios_va_owners_report( const char *why )\n{'):]
rep = rep[:rep.index('\n}\n') + 3]
harness = r'''
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#define IOS_BIGRES_MAX 48
static struct { uint64_t base, size, committed; unsigned commits; void *owner; } ios_bigres_tab[IOS_BIGRES_MAX];
static unsigned ios_bigres_cnt;
static void *cur_peb;
static void *ios_jit_current_peb(void) { return cur_peb; }
static char live_a, dead_b, first_c;
int ios_peb_is_live_child( const void *p ) { return p == &live_a; }
int ios_peb_is_dead( const void *p ) { return p == &dead_b; }
''' + note + rep + r'''
int main(void)
{
    cur_peb = &dead_b; ios_bigres_note((void *)0x7000000000ull, 32ull << 30); ios_bigres_note((void *)0x7800000000ull, 16ull << 30);
    ios_bigres_tab[0].committed = 64ull << 20; ios_bigres_tab[0].commits = 3;
    cur_peb = &live_a; ios_bigres_note((void *)0x8000000000ull, 1ull << 30);
    cur_peb = &first_c; ios_bigres_note((void *)0x8100000000ull, 4ull << 30);
    cur_peb = 0; ios_bigres_note((void *)0x8200000000ull, 512ull << 20);
    cur_peb = &live_a;
    ios_va_owners_report("no gap fits");
    ios_va_owners_report("jumbo reserve failed"); ios_va_owners_report("hinted jumbo retry exhausted"); ios_va_owners_report("fourth");
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('[va-owners] compiles on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-2000:])
    if cc.returncode == 0:
        out = subprocess.run([str(exe)], capture_output=True, text=True).stderr
        first = out.split('[va-owners] rev=r624 why=jumbo reserve failed')[0]
        check('the headline: why, how many ranges and owners, who asked', '[va-owners] rev=r624 why=no gap fits: 5 big reservation(s) held by 4 owner(s); the asking process peb=0x' in out, out[:300])
        check('per owner: ranges, reserved and committed MB, and whether that process runs, has exited, or is the first process / not known',
              '2 range(s), 49152 MB reserved, 64 MB committed; THAT PROCESS HAS EXITED' in first
              and '1 range(s), 1024 MB reserved, 0 MB committed; a running child process' in first
              and "1 range(s), 4096 MB reserved, 0 MB committed; the session's first process, or one that is not registered" in first
              and 'owner peb=0x0: 1 range(s), 512 MB reserved, 0 MB committed; owner not known (reserved during boot)' in first, first)
        rows = [l for l in first.splitlines() if '[va-owners]   range ' in l]
        check('the five largest ranges, largest first, with committed MB, calls and owner',
              len(rows) == 5 and rows[0].startswith('[va-owners]   range 0x7000000000 +32768 MB, 64 MB committed in 3 call(s), owner peb=0x')
              and ' +16384 MB' in rows[1] and ' +4096 MB' in rows[2] and ' +1024 MB' in rows[3] and ' +512 MB' in rows[4], '\n'.join(rows))
        check('at most 3 blocks a session', out.count('[va-owners] rev=r624') == 3 and 'why=fourth' not in out)

# the 90 % one-shot
at90 = virt[virt.index('static int ios_footprint_at90( unsigned long long mb )\n{'):]
at90 = at90[:at90.index('\n}\n') + 3]
check('[footprint] rev=r624 90% of the limit: once a session, limit = what is left before jetsam + what is used, the thread count',
      'static int said;' in at90 and 'if (said) return 0;' in at90 and 'limit = mb + left;' in at90 and 'mb * 10 < limit * 9' in at90
      and 'task_threads( mach_task_self(), &acts, &nacts )' in at90 and 'mach_port_deallocate( mach_task_self(), acts[ti] );' in at90
      and 'vm_deallocate( mach_task_self(), (vm_address_t)acts, nacts * sizeof(*acts) );' in at90)
check('... asked from the fast sampler and from the slow cycle (a 2.3 GB-limit phone never reaches the fast cadence)',
      virt.count('if (ios_footprint_at90( mb ) && !physmap_at90) physmap_at90 = 1;') == 1
      and virt.count('if (ios_footprint_at90( fp_mb ) && !physmap_at90) physmap_at90 = 1;') == 1)
check('... and the walk runs once more for it, then never again for it',
      'if (cycle == 2 || (cycle % ios_warmer_sweep_cycles()) == 0 || physmap_at90 == 1)' in virt and 'if (physmap_at90 == 1) physmap_at90 = 2;' in virt)

# [reg-miss]: keyed, failed registry requests only, 400 at most, both request paths, never when Wine's own debug trace is on
req = (root / 'build/wineserver/request_ios.c').read_text()
want = req[req.index('static int ios_reg_trace_want( enum request req, unsigned int error )\n{'):]
want = want[:want.index('\n}\n') + 3]
check('[reg-miss] is read from env.MADEIRA_REG_TRACE once ("1" only) and stops at 400 requests',
      'getenv( "MADEIRA_REG_TRACE" )' in want and "mode = e && e[0] == '1' && !e[1];" in want and '<= 400' in want
      and req.count('MADEIRA_REG_TRACE') == 2)
check("... both request paths ask it after the handler, only when Wine's own trace is off, and print the request and the reply",
      req.count('else if (ios_reg_trace_want( req, current->error )) ios_reg_trace( req, &reply );') == 1
      and req.count('else if (ios_reg_trace_want( req, current->error )) ios_reg_trace( req, reply );') == 1
      and 'fputs( "[reg-miss] ", stderr );\n    trace_request();\n    fputs( "[reg-miss] ", stderr );\n    trace_reply( req, reply );' in req)
REG_HEAD = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
enum request { REQ_open_key, REQ_create_key, REQ_get_key_value, REQ_enum_key, REQ_enum_key_value, REQ_set_key_value, REQ_read_file };
'''
REG_MAIN = r'''
int main(int argc, char **argv)
{
    int n = 0, i;
    if (argc > 1 && !strcmp(argv[1], "late"))
    {
        printf("off=%d\n", ios_reg_trace_want(REQ_open_key, 0xc0000034));
        setenv("MADEIRA_REG_TRACE", "1", 1);
        printf("still-off=%d\n", ios_reg_trace_want(REQ_open_key, 0xc0000034));
        return 0;
    }
    setenv("MADEIRA_REG_TRACE", "1", 1);
    printf("ok=%d set=%d other=%d enum=%d\n", ios_reg_trace_want(REQ_open_key, 0), ios_reg_trace_want(REQ_set_key_value, 5),
           ios_reg_trace_want(REQ_read_file, 5), ios_reg_trace_want(REQ_enum_key_value, 0x8000001a));
    for (i = 0; i < 500; i++) n += ios_reg_trace_want(i & 1 ? REQ_get_key_value : REQ_create_key, 0xc0000034);
    printf("in-500=%d\n", n);
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 'reg.c'
    c.write_text(REG_HEAD + want + REG_MAIN)
    exe = Path(tmp) / 'reg'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('[reg-miss] filter compiles on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-1500:])
    if cc.returncode == 0:
        late = subprocess.run([str(exe), 'late'], capture_output=True, text=True, env={}).stdout
        check('[reg-miss] is off without the key, and the key is read once', 'off=0' in late and 'still-off=0' in late, late)
        on = subprocess.run([str(exe)], capture_output=True, text=True, env={}).stdout
        check('[reg-miss] with the key: a success, a write and other requests are not traced; a failed enumeration is',
              'ok=0 set=0 other=0 enum=1' in on, on)
        check('[reg-miss] stops at 400 (399 more after the one above)', 'in-500=399' in on, on)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
