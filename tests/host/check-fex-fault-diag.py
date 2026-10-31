#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[fex-fault]: a fatal SEGV inside FEX's own runtime names where and which lock (round 624, agent C S4; log only).

Minecraft Dungeons (MJB5SK) and KovaaK's (7PXKCS): FEX's InvalidationTracker faulted at `ldr x20,[x20]` in its own loop with a callee's
values in x19/x20, and the game then waited for 3 minutes; the log had no module+RVA of the pc, no x21..x28, no stack and no lock owner.
segv_handler (build/ntdll-unix/signal_arm64_ios.c) now prints, only when the faulting pc maps into a FEX module's pool copy (libarm64ecfex,
libwow64fex, xtajit) and at most 4 times a run: pc and lr as module+RVA, x19..x28, the frame chain, 48 stack words, and any stack word that
points at a FEX WritePriorityMutex write-locked with this thread's TEB.

Checks: the block sits after [emit-dump] and before the ml270 block; it reads memory only through mach_vm_read_overwrite and writes,
stops or ends nothing; the FEX module test and the cap; the mutex layout it assumes is still FEX's (when the FEX tree is present); the
file still compiles for iOS (when a configured wine tree is given in WINE_SRC).
"""
from pathlib import Path
import os, re, shutil, subprocess

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


a = src.index('/* Round 624 (agent C S4; Minecraft Dungeons MJB5SK')
b = src.index('/* ml270 (#48): WHERE does the corrupt guest RSP come from?')
block = src[a:b]
check('one block, right after the [emit-dump] block and before the ml270 block',
      src.count('[fex-fault] pc=%s+0x%llx') == 1 and src.rindex('[emit-dump]   +%-4d', 0, a) > a - 600 and a < b)
check('only for a pc inside FEX\'s own runtime (a FEX module\'s pool copy), at most 4 times a run',
      'if (xname && (strstr( xname, "fex" ) || strstr( xname, "xtajit" )) && __sync_add_and_fetch( &fex_faults, 1 ) <= 4)' in block
      and 'const char *xname = (xva && xva != xpc) ? ios_pe_module_name( xmod ) : NULL;' in block)
check('every memory read is fault-safe (mach_vm_read_overwrite); no raw dereference',
      block.count('mach_vm_read_overwrite(') == 3
      and not re.search(r'\*\s*\(\s*(?:const\s+)?(?:volatile\s+)?(?:u?int\d+_t|void|char|uintptr_t)\s*\*\s*\)', block))
forbidden = ('thread_set_state', 'abort_process', 'exit(', 'mprotect', 'vm_protect', 'mach_vm_write', 'munmap', 'raise(', 'kill(', 'thread_suspend')
check('it writes, stops and ends nothing', not [f for f in forbidden if f in block], [f for f in forbidden if f in block])
check('the lines: pc/lr with module+RVA, x19..x28, the frame chain (12 at most), 48 stack words, a held FEX mutex',
      '"  [fex-fault]   x19=%p x20=%p x21=%p x22=%p x23=%p x24=%p x25=%p x26=%p x27=%p x28=%p\\n"' in block
      and 'for (n = 0; n < 12 && fp > 0x100000 && !(fp & 7); n++)' in block and 'uint64_t stk[48]' in block
      and 'a FEX mutex write-locked by this thread: mutex=%p futex=0x%08x OwnerTid=0x%x OwnerDepth=%u' in block
      and 'm32[1] == teb && ((uint32_t)m32[0] & 0x80000000u)' in block)

mutex = Path(os.environ.get('FEX_SRC', Path.home() / 'madeira-bcd/FEX')) / 'FEXCore/include/FEXCore/Utils/WritePriorityMutex.h'
if mutex.exists():
    m = mutex.read_text()
    order = [m.find('  uint32_t Futex {};'), m.find('  uint64_t OwnerTeb {};'), m.find('  uint32_t OwnerTid {};'), m.find('  uint32_t OwnerDepth {};')]
    check('FEX\'s WritePriorityMutex still starts Futex, OwnerTeb, OwnerTid, OwnerDepth (the offsets the dump reads)',
          all(x >= 0 for x in order) and order == sorted(order) and 'virtual' not in m[m.index('class Mutex final {'):order[0]], order)
else:
    print('skip the mutex layout pin: no FEX tree (set FEX_SRC)')

wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
config = wine / 'build-macos/include/config.h'
if shutil.which('xcrun') and config.exists():
    sdk = subprocess.run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], capture_output=True, text=True).stdout.strip()
    build = root / 'build/ntdll-unix'
    flags = ['-fsyntax-only', '-arch', 'arm64', '-isysroot', sdk, '-miphoneos-version-min=17.0', '-O2', '-fPIC', '-fno-strict-aliasing', '-Wall',
             '-Wno-implicit-function-declaration', '-Wno-int-conversion', '-include', str(config), '-include', str(build / 'shims/wine_ios_exit.h'),
             f'-I{build}', f'-I{build / "shims"}', f'-I{wine / "build-macos/dlls/ntdll"}', f'-I{wine / "dlls/ntdll"}', f'-I{wine / "dlls/ntdll/unix"}',
             f'-I{wine / "build-macos/include"}', f'-I{wine / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES', '-DLTC_SOURCE', '-D_NTSYSTEM_',
             '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"', '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"',
             '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB', '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context',
             '-Dset_thread_context=ntdll_set_thread_context']
    r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(build / 'signal_arm64_ios.c')], capture_output=True, text=True)
    check('signal_arm64_ios.c compiles for iOS (syntax only) with no warning from the block',
          r.returncode == 0 and 'fex_faults' not in r.stderr and 'm32' not in r.stderr and 'fex-fault' not in r.stderr, r.stderr[-2000:])
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-fex-fault-diag.py' in wf)
print('PASS: [fex-fault]' if ok else 'FAIL: [fex-fault]')
raise SystemExit(0 if ok else 1)
