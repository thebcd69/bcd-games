#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[early-fault]: a fatal signal before BCD's own fault handlers exist names where it happened (round 624, agent D1 S8; log only).

lucieneslife (C8G6JC, build 622): the log ended with Wine's fixed sentence "wineserver crashed, please enable coredumps ..." right after the
JIT-pool copy of the game's image, with nothing about where. build/wineserver/early_fault_ios.h is installed right after the server's
init_signals() and only while BCD's signal_init_process() has not run (it never replaces BCD's own handlers). On SIGSEGV, SIGBUS or SIGILL it
first puts back exactly what was installed before, prints the signal, the address, pc/lr/sp/fp, the thread, the loader step
(ios_loader_phase, ten IOS_PHASE stores in the loader) and the frame chain, and returns: the instruction faults again into the old handler,
so the process ends as it did (Wine's sentence and abort, or the default action).

Checks: the header's calls are async-signal-safe ones only; the install site and its guard; the phase stores; compiled on the host and run
in child processes with real faults: a SIGSEGV with the default action (the line, then death by SIGSEGV), a SIGSEGV with a previous handler
(the line, then that handler's sentence and exit 77), a real SIGBUS and a real SIGILL (the line, then death by that signal), a reporter that
was replaced afterwards (no line), and an install after BCD's handlers exist (nothing installed: only the old handler runs).
"""
from pathlib import Path
import os, re, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
hdr = (root / 'build/wineserver/early_fault_ios.h').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


code = re.sub(r'/\*.*?\*/', '', hdr, flags=re.S)
calls = set(re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', code)) - {'if', 'for', 'while', 'sizeof', 'return', 'defined'}
allowed = {'write', 'sigaction', 'sigemptyset', 'memset', 'mach_thread_self', 'mach_task_self', 'mach_vm_read_overwrite',
           'ios_early_index', 'ios_early_cat', 'ios_early_num', 'ios_early_fault', 'ios_early_fault_install',
           '__darwin_arm_thread_state64_get_pc', '__darwin_arm_thread_state64_get_lr', '__darwin_arm_thread_state64_get_sp',
           '__darwin_arm_thread_state64_get_fp'}
check('the reporter calls only async-signal-safe functions (write, sigaction, fault-safe Mach reads), no stdio, allocation or exit',
      calls <= allowed and not [x for x in ('printf', 'malloc', 'abort', 'exit(', 'pthread_', 'getenv', 'signal(') if x in code], sorted(calls - allowed))
check('it puts back the previous disposition first, then reports and returns (no SA_RESETHAND, no direct call of the old handler)',
      code.index('sigaction( sig, &ios_early_old[ios_early_index( sig )], NULL );') < code.index('"[early-fault] sig="')
      and 'SA_RESETHAND' not in code and 'sa_handler(' not in code and 'sa_sigaction(' not in code)
check('never installed once BCD\'s own handlers exist', 'if (ios_signal_init_done) return;' in code
      and 'for (i = 0; i < 3; i++) sigaction( sigs[i], &sa, &ios_early_old[i] );' in code)
main_c = (root / 'build/wineserver/main_ios.c').read_text()
check('the server installs it right after init_signals()',
      '#include "early_fault_ios.h"' in main_c and '    init_signals();\n    ios_early_fault_install();' in main_c)
sig_c = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
init = sig_c[sig_c.index('void signal_init_process(void)\n{'):]
check('signal_init_process() marks BCD\'s handlers as installed before it installs them',
      'volatile int ios_signal_init_done;' in sig_c and init.index('ios_signal_init_done = 1;') < init.index('sigaction(')
      and sig_c.count('ios_signal_init_done = 1;') == 1)
loader = (root / 'build/ntdll-unix/loader_ios.c').read_text()
virt = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
shim = (root / 'build/ntdll-unix/shims/wine_ios_exit.h').read_text()
phases = ['load-main-exe', 'jit-pool-copy', 'copy-verified', 'pe-sections', 'relocations', 'x18-gate', 'prot-ending', 'exec-fixup', 'eager-copy',
          'map-image-done']
check('the loader step: defined once (loader_ios.c), declared in the shared shim, ten stores with their names',
      loader.count('const char *volatile ios_loader_phase = "boot";') == 1 and 'extern const char *volatile ios_loader_phase;' in shim
      and '#define IOS_PHASE(s) (ios_loader_phase = (s))' in shim
      and all((loader + virt).count('IOS_PHASE( "%s" )' % p) == 1 for p in phases)
      and (loader + virt).count('IOS_PHASE(') == len(phases))

harness = r'''
#include "early_fault_ios.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
const char *volatile ios_loader_phase = "test-step";
volatile int ios_signal_init_done;
static void old_handler( int sig )
{
    static const char m[] = "wineserver crashed, please enable coredumps (ulimit -c unlimited) and restart.\n";
    (void)sig;
    (void)!write( 2, m, sizeof(m) - 1 );
    _exit( 77 );
}
static void segv(void) { *(volatile int *)0 = 1; }
int main( int argc, char **argv )
{
    const char *c = argc > 1 ? argv[1] : "";
    if (!strcmp( c, "segv" )) { ios_early_fault_install(); segv(); return 0; }
    if (!strcmp( c, "chain" )) { signal( SIGSEGV, old_handler ); ios_early_fault_install(); segv(); return 0; }
    if (!strcmp( c, "replaced" )) { ios_early_fault_install(); signal( SIGSEGV, SIG_DFL ); segv(); return 0; }
    if (!strcmp( c, "after-init" )) { ios_signal_init_done = 1; signal( SIGSEGV, old_handler ); ios_early_fault_install(); segv(); return 0; }
    if (!strcmp( c, "bus" ))
    {
        char path[] = "/tmp/early-fault-XXXXXX";
        int fd = mkstemp( path );
        volatile char *p;
        unlink( path );
        p = mmap( NULL, 4096, PROT_READ, MAP_SHARED, fd, 0 );   /* an empty file: reading the page is a real SIGBUS */
        ios_early_fault_install();
        return p[0];
    }
    if (!strcmp( c, "ill" ))
    {
        ios_early_fault_install();
#if defined(__x86_64__)
        __asm__ volatile( "ud2" );
#else
        __asm__ volatile( ".inst 0x00000000" );
#endif
        return 0;
    }
    return 2;
}
'''
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / 'h.c'
    c.write_text(harness)
    exe = Path(t) / 'h'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-I', str(root / 'build/wineserver'),
                         '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the reporter compiles on the host (-Wall -Wextra -Werror)', cc.returncode == 0, cc.stderr[-2000:])
    if cc.returncode == 0:
        def run(case):
            return subprocess.run([str(exe), case], capture_output=True, text=True, timeout=60)

        r = run('segv')
        lines = r.stderr.splitlines()
        check('a SIGSEGV: one line with the signal, the address, the loader step, and the frames; then the default action (death by SIGSEGV)',
              lines and lines[0].startswith('[early-fault] sig=11 code=') and ' addr=0x0 ' in lines[0] and ' phase=test-step rev=r624' in lines[0]
              and len(lines) >= 2 and lines[1].startswith('[early-fault] frames:') and r.returncode == -11, (r.returncode, r.stderr[-800:]))
        r = run('chain')
        check('with a previous handler: the line first, then that handler (Wine\'s sentence) and its exit',
              r.stderr.startswith('[early-fault] sig=11') and 'wineserver crashed, please enable coredumps' in r.stderr
              and r.stderr.index('[early-fault]') < r.stderr.index('wineserver crashed') and r.returncode == 77, (r.returncode, r.stderr[-800:]))
        r = run('bus')
        check('a real SIGBUS: the line, then death by SIGBUS', r.stderr.startswith('[early-fault] sig=10 ') and r.returncode == -10,
              (r.returncode, r.stderr[-800:]))
        r = run('ill')
        check('a real SIGILL: the line, then death by SIGILL', r.stderr.startswith('[early-fault] sig=4 ') and r.returncode == -4,
              (r.returncode, r.stderr[-800:]))
        r = run('replaced')
        check('a reporter replaced afterwards (as signal_init_process does) prints nothing', '[early-fault]' not in r.stderr and r.returncode == -11,
              (r.returncode, r.stderr[-800:]))
        r = run('after-init')
        check('installed after BCD\'s handlers exist: nothing installed, only the old handler runs',
              '[early-fault]' not in r.stderr and 'wineserver crashed' in r.stderr and r.returncode == 77, (r.returncode, r.stderr[-800:]))

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-early-fault.py' in wf)
print('PASS: [early-fault]' if ok else 'FAIL: [early-fault]')
raise SystemExit(0 if ok else 1)
