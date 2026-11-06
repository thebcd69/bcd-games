#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_WOW_PRESSURE_RELEASE_MB: idle 32-bit window placeholders go back to the kernel under band pressure; no Wine runs.

Ported from upstream Madeira v0.2.0 (#517, ml2030), off by default in BCD (upstream: 256). Valve's SteamService.exe (GTA V Enhanced
9NNP2Q, 3A27ZN, S59TH0) and Rockstar's vc_redist.x86.exe (1HB5J4) take a 32-bit window; when the program exits its 4 GB stays reserved
and idle until the next 32-bit start (502 archive logs print "released (owner exited); torn down when the next 32-bit process starts"),
while Social Club's browser dies of "out of memory" with a few hundred MB free in the band. With the key set the warmer's tick
(every 5th cycle) reclaims windows whose threads are gone and hands every unadopted placeholder back.

A compiled run of the production tick and free-space walk against a model of the address map and the window registry, one process
per case (the key is read once):
  - key unset, "0", negative or not a number: nothing happens (the map is not even walked);
  - no placeholder, a cycle that is not a multiple of 5, a band with at least the threshold free, a 32-bit main image that has not taken
    its window yet: nothing is released;
  - pressure: dead windows are reclaimed first, then every unadopted placeholder that is not a registry window is released with the
    size it owns (4 GB, plus the guard page when the placeholder owns it), adopted ones and registry windows stay, the registry shrinks;
  - nothing idle: a line says so (at most four times).
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


free_fn = function('static unsigned long long ios_wow_band_free_bytes(')
tick = function('\nvoid ios_wow_pressure_tick( unsigned cycle )\n{')[1:]
check('the warmer calls the tick once per cycle, right after the cycle line, with the cycle number',
      src.count('ios_wow_pressure_tick( cycle );') == 1 and
      src.index('[pool-warmer] cycle=%u touched') < src.index('ios_wow_pressure_tick( cycle );') <
      src.index('/* task #35: report the three pool slots every ~30s'))
check('the key is read once; unset means 0 (never)', 'getenv( "MADEIRA_WOW_PRESSURE_RELEASE_MB" )' in tick and
      '(env && *env) ? strtol( env, NULL, 10 ) : 0;' in tick and src.count('MADEIRA_WOW_PRESSURE_RELEASE_MB" )') == 1)
check('the tick acts only on every 5th cycle, with a placeholder, and not before the 32-bit main image has its window',
      'if (!threshold_mb || !ios_wow_placeholder_count || (cycle % 5)) return;' in tick and
      'if (ios_main_image_i386 && !ios_wow_window_count) return;' in tick)
check('reclaim comes before the release; the release takes both locks one after the other, never nested',
      tick.index('ios_wow_reclaim_dead_windows();') < tick.index('pthread_mutex_lock( &ios_wow_mutex );') <
      tick.index('pthread_mutex_unlock( &ios_wow_mutex );') < tick.index('server_enter_uninterrupted_section( &virtual_mutex, &sigset );'))

harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* the locks are counted, not taken: the tick must never hold both at once (ios_wow_mutex, then virtual_mutex, one after the other) */
#define pthread_mutex_t fake_mutex_t
#define sigset_t fake_sigset_t
typedef int pthread_mutex_t;
typedef int sigset_t;
#define pthread_mutex_lock fake_mutex_lock
#define pthread_mutex_unlock fake_mutex_unlock
#define PTHREAD_MUTEX_INITIALIZER 0
static int locks_held, max_locks_held;
static void pthread_mutex_lock(pthread_mutex_t *m) { (void)m; if (++locks_held > max_locks_held) max_locks_held = locks_held; }
static void pthread_mutex_unlock(pthread_mutex_t *m) { (void)m; locks_held--; }
typedef uintptr_t ULONG_PTR;
typedef uint64_t mach_vm_address_t, mach_vm_size_t;
typedef unsigned mach_msg_type_number_t;
typedef int mach_port_t, kern_return_t;
typedef void *vm_region_info_t;
typedef struct { int protection, max_protection, shared; } vm_region_basic_info_data_64_t;
#define KERN_SUCCESS 0
#define VM_REGION_BASIC_INFO_64 1
#define VM_REGION_BASIC_INFO_COUNT_64 1
#define MACH_PORT_NULL 0
#define IOS_WOW_WINDOW_SIZE ((ULONG_PTR)1 << 32)
#define IOS_WOW_MAX_WINDOWS 8
#define dprintf(fd, ...) do { logs++; fprintf(stderr, __VA_ARGS__); } while (0)
static int logs;
struct ios_wow_window { ULONG_PTR base; };
struct ios_wow_placeholder { ULONG_PTR base; unsigned guard_owned; unsigned adopted; };
static struct ios_wow_window ios_wow_windows[IOS_WOW_MAX_WINDOWS];
static unsigned ios_wow_window_count;
static struct ios_wow_placeholder ios_wow_placeholders[IOS_WOW_MAX_WINDOWS];
static unsigned ios_wow_placeholder_count;
static pthread_mutex_t ios_wow_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t virtual_mutex = PTHREAD_MUTEX_INITIALIZER;
static int ios_main_image_i386;
static ULONG_PTR ios_wow_guard_size(void) { return 0x4000; }
static struct ios_wow_window *ios_wow_slot_at_base(ULONG_PTR base)
{
    for (unsigned i = 0; i < ios_wow_window_count; i++) if (ios_wow_windows[i].base == base && base) return &ios_wow_windows[i];
    return NULL;
}
static void ios_wow_band(ULONG_PTR *floor, ULONG_PTR *ceil) { *floor = 0x7100000000ULL; *ceil = 0x7400000000ULL; }
static int reclaim_calls, region_calls;
static void ios_wow_reclaim_dead_windows(void) { reclaim_calls++; }
static int removed_n;
static struct { void *base; size_t size; } removed[8];
static void remove_reserved_area_check(void);
static void remove_reserved_area(void *addr, size_t size) { remove_reserved_area_check(); removed[removed_n].base = addr; removed[removed_n].size = size; removed_n++; }
static void server_enter_uninterrupted_section(pthread_mutex_t *m, sigset_t *s) { (void)s; pthread_mutex_lock(m); }
static void server_leave_uninterrupted_section(pthread_mutex_t *m, sigset_t *s) { (void)s; pthread_mutex_unlock(m); }
static void remove_reserved_area_check(void) { assert(locks_held == 1); }   /* remove_reserved_area runs under virtual_mutex alone */
/* the address map: sorted mapped regions */
struct reg { mach_vm_address_t a; mach_vm_size_t s; };
static struct reg regs[16];
static int nregs;
static mach_port_t mach_task_self(void) { return 1; }
static kern_return_t mach_vm_region(mach_port_t t, mach_vm_address_t *addr, mach_vm_size_t *size, int flavor, vm_region_info_t out,
                                    mach_msg_type_number_t *cnt, mach_port_t *obj)
{
    (void)t; (void)flavor; (void)out; (void)cnt; (void)obj;
    region_calls++;
    for (int i = 0; i < nregs; i++)
        if (regs[i].a + regs[i].s > *addr)
        {
            *addr = regs[i].a > *addr ? regs[i].a : *addr;
            *size = regs[i].a + regs[i].s - *addr;
            return KERN_SUCCESS;
        }
    return 1;
}
@FREE@
@TICK@
static void add_reg(mach_vm_address_t a, mach_vm_size_t s) { regs[nregs].a = a; regs[nregs].s = s; nregs++; }
static void add_ph(ULONG_PTR base, unsigned guard, unsigned adopted)
{
    ios_wow_placeholders[ios_wow_placeholder_count].base = base; ios_wow_placeholders[ios_wow_placeholder_count].guard_owned = guard;
    ios_wow_placeholders[ios_wow_placeholder_count].adopted = adopted; ios_wow_placeholder_count++;
}
static void add_win(ULONG_PTR base) { ios_wow_windows[ios_wow_window_count++].base = base; }
#define MB(x) ((mach_vm_size_t)(x) << 20)
/* the band [0x7100000000, 0x7400000000) with `free_mb` free at its top */
static void band_with_free(unsigned free_mb) { nregs = 0; add_reg(0x7100000000ULL, 0x300000000ULL - MB(free_mb)); }
#define T(name, cond) do { int _c = !!(cond); printf("%s %s\n", _c ? "ok  " : "FAIL", name); fails += !_c; } while (0)
int main(int argc, char **argv)
{
    int fails = 0;
    const char *what = argc > 1 ? argv[1] : "";

    if (!strcmp(what, "map"))
    {
        nregs = 0; add_reg(0x7100000000ULL, 0x100000000ULL); add_reg(0x7200000000ULL + MB(100), 0x100000000ULL - MB(100)); add_reg(0x7300000000ULL, 0x100000000ULL);
        T("free bytes: the hole between regions is counted once (100 MB)", ios_wow_band_free_bytes(0x7100000000ULL, 0x7400000000ULL, ~0ULL) == MB(100));
        nregs = 0; add_reg(0x7200000000ULL, 0x100000000ULL);
        T("free bytes: free before the first region and after the last one (3 GB)", ios_wow_band_free_bytes(0x7100000000ULL, 0x7400000000ULL, ~0ULL) == 0x200000000ULL);
        region_calls = 0;
        T("free bytes: stops walking at `enough`", ios_wow_band_free_bytes(0x7100000000ULL, 0x7400000000ULL, MB(16)) >= MB(16) && region_calls == 1);
        nregs = 0;
        T("free bytes: an empty map is all free", ios_wow_band_free_bytes(0x7100000000ULL, 0x7400000000ULL, ~0ULL) == 0x300000000ULL);
        nregs = 0; add_reg(0x7000000000ULL, 0x200000000ULL);
        T("free bytes: a region that covers the start of the range is not free", ios_wow_band_free_bytes(0x7100000000ULL, 0x7400000000ULL, ~0ULL) == 0x200000000ULL);
        return fails != 0;
    }
    if (!strcmp(what, "off"))   /* key unset / 0 / negative / junk */
    {
        band_with_free(10); add_ph(0x7100000000ULL, 1, 0); add_ph(0x7300000000ULL, 0, 0);
        for (unsigned c = 0; c < 20; c++) tick(c);
        T("key off: nothing released, the map not walked, no reclaim, nothing logged", removed_n == 0 && ios_wow_placeholder_count == 2 && region_calls == 0 && reclaim_calls == 0 && logs == 0);
        return fails != 0;
    }
    if (!strcmp(what, "idle"))   /* the main case */
    {
        band_with_free(100); add_ph(0x7100000000ULL, 1, 0); add_ph(0x7200000000ULL, 0, 1); add_ph(0x7300000000ULL, 0, 0);
        for (unsigned c = 1; c <= 4; c++) tick(c);
        T("cycles 1-4: nothing (only every 5th cycle)", removed_n == 0 && region_calls == 0 && reclaim_calls == 0);
        tick(5);
        T("cycle 5, 100 MB free under 256: two idle placeholders released, the adopted one kept", removed_n == 2 && ios_wow_placeholder_count == 1 &&
          ios_wow_placeholders[0].base == 0x7200000000ULL && ios_wow_placeholders[0].adopted);
        T("an owned guard page is part of the released size (4 GB + 16 KB), a borrowed one is not", removed_n == 2 &&
          removed[0].base == (void *)0x7100000000ULL && removed[0].size == 0x100004000ULL &&
          removed[1].base == (void *)0x7300000000ULL && removed[1].size == 0x100000000ULL);
        T("dead windows are reclaimed first, once", reclaim_calls == 1);
        T("one line per placeholder", logs == 2);
        T("the two locks are never held together and both are released", max_locks_held == 1 && locks_held == 0);
        tick(10);
        T("the next tick has no unadopted placeholder left to give", removed_n == 2 && ios_wow_placeholder_count == 1);
        return fails != 0;
    }
    if (!strcmp(what, "room"))   /* the band has room */
    {
        band_with_free(300); add_ph(0x7100000000ULL, 1, 0);
        tick(5);
        T("300 MB free, threshold 256: nothing released and no reclaim", removed_n == 0 && reclaim_calls == 0 && ios_wow_placeholder_count == 1 && logs == 0);
        band_with_free(255);
        tick(10);
        T("255 MB free: released", removed_n == 1 && ios_wow_placeholder_count == 0);
        return fails != 0;
    }
    if (!strcmp(what, "threshold1000"))
    {
        band_with_free(500); add_ph(0x7100000000ULL, 0, 0);
        tick(5);
        T("MADEIRA_WOW_PRESSURE_RELEASE_MB=1000, 500 MB free: released", removed_n == 1);
        return fails != 0;
    }
    if (!strcmp(what, "noplaceholder"))
    {
        band_with_free(10);
        for (unsigned c = 0; c < 20; c++) tick(c);
        T("no placeholder at all: nothing, the map is not walked", removed_n == 0 && region_calls == 0);
        return fails != 0;
    }
    if (!strcmp(what, "main32"))
    {
        band_with_free(10); ios_main_image_i386 = 1; add_ph(0x7100000000ULL, 1, 0);
        tick(5);
        T("a 32-bit main image that has not taken its window yet: its placeholder is not released", removed_n == 0 && ios_wow_placeholder_count == 1 && region_calls == 0);
        add_win(0x7200000000ULL);
        tick(10);
        T("once a window exists (the main image has its own), an idle placeholder may go", removed_n == 1 && ios_wow_placeholder_count == 0);
        return fails != 0;
    }
    if (!strcmp(what, "allinuse"))
    {
        band_with_free(10); add_ph(0x7100000000ULL, 1, 1); add_ph(0x7200000000ULL, 0, 0); add_win(0x7200000000ULL);   /* the second is a registry window's base */
        for (unsigned c = 5; c <= 100; c += 5) tick(c);
        T("adopted placeholders and registry windows stay", removed_n == 0 && ios_wow_placeholder_count == 2);
        T("the 'every placeholder is in use' line is printed four times at most", logs == 4);
        return fails != 0;
    }
    return 2;
}
'''
harness = harness.replace('@FREE@', free_fn).replace('@TICK@', tick.replace('void ios_wow_pressure_tick( unsigned cycle )', 'static void tick( unsigned cycle )'))
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-g', '-fsanitize=address,undefined', '-Wall', '-Wno-unused-function',
                         '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production tick and free-space walk compile on the host (ASAN/UBSAN)', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        base = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}

        def run(case, value):
            env = dict(base)
            if value is not None:
                env['MADEIRA_WOW_PRESSURE_RELEASE_MB'] = value
            r = subprocess.run([str(exe), case], capture_output=True, text=True, env=env)
            for line in r.stdout.splitlines():
                check('%s [key=%r]: %s' % (case, value, line[5:]), line.startswith('ok'))
            check('%s [key=%r] ran clean' % (case, value), r.returncode == 0 and 'FAIL' not in r.stdout, 'rc=%d\n%s%s' % (r.returncode, r.stdout, r.stderr[-800:]))
            return r

        run('map', None)
        for value in (None, '0', '-5', 'abc', ''):
            run('off', value)
        run('idle', '256')
        run('room', '256')
        run('threshold1000', '1000')
        run('noplaceholder', '256')
        run('main32', '256')
        run('allinuse', '256')

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-wow-pressure-release.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
