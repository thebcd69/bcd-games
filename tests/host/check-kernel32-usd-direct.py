#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The 64-bit kernel32's tick count without a fault per call (build 663; tools/patch-wine-kernel32-usd-direct.py).

Checks:
 (a) the patch on the Wine fork's dlls/kernel32/sync.c: applies once ("already patched" the second time), is refused when the
     line it replaces moved (writing nothing), changes nothing but that line, and leaves the i386 build's code as it was (#else);
 (b) its ARM64 branch, compiled on the host against a stand-in NtQueryInformationProcess and driven: BCD's address is read from
     there and asked for once; a failed question gives 0x7ffe0000 for good; an answer of 0 gives 0x7ffe0000 and is asked again;
     GetTickCount and GetTickCount64 read the page they are given (the high/low/high loop as before);
 (c) build/ntdll-unix/process_ios.c answers class 1011 with the real page's address, for the calling process only, before the
     switch;
 (d) tools/build-wine-extra-dlls.sh builds kernel32-usd.dll from a kernel32 the pin left as it is, checks it with
     tools/verify-kernel32-usd.py and puts sync.c back; the verifier refuses the shipped kernel32.dll as a patched one;
 (e) app/Madeira/WineProcessBridge.m links it as kernel32.dll (sysx64; system32 too in an ARM64EC session) unless
     env.MADEIRA_USD_DIRECT = 0, and the session end removes the variable; the catalog lists the key; .gitignore and the workflow.
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
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)[:1500]) if (detail and not cond) else ''))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location('k32usd', root / 'tools/patch-wine-kernel32-usd-direct.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
script = root / 'tools/patch-wine-kernel32-usd-direct.py'

# (a) the patch on the fork's sync.c
sync = root / 'wine/dlls/kernel32/sync.c'
if sync.is_file():
    original = sync.read_text()
    if mod.MARKER in original:
        original = original.replace(mod.NEW, mod.OLD)
    with tempfile.TemporaryDirectory(prefix='bcd-k32usd-') as t:
        f = Path(t) / 'sync.c'
        f.write_text(original)
        r1 = subprocess.run([sys.executable, str(script), str(f)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script), str(f)], capture_output=True, text=True)
        after = f.read_text()
        check('applies once, then says it is already patched',
              r1.returncode == 0 and 'applied' in r1.stdout and r2.returncode == 0 and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        check('only the user_shared_data line changes (undoing it gives the file back)', after.replace(mod.NEW, mod.OLD) == original and after != original)
        check('the i386 build keeps the constant (the #else branch is the old line)', '#else\n' + mod.OLD + '#endif\n' in after)
        moved = Path(t) / 'moved.c'
        moved.write_text(original.replace(mod.OLD, mod.OLD.replace('0x7ffe0000;', '0x7ffe0000; /* moved */')))
        r = subprocess.run([sys.executable, str(script), str(moved)], capture_output=True, text=True)
        check('a moved line is refused and nothing is written', r.returncode != 0 and mod.MARKER not in moved.read_text(), r.stdout + r.stderr)
        uses = re.findall(r'user_shared_data->(\w+)', original)
        check('sync.c reads nothing of the page but the tick count (the macro covers every use)',
              uses and set(uses) == {'TickCount'}, uses)
else:
    print('skip: no wine sources (check out the submodule) for (a)')

# (b) the ARM64 branch on the host
branch = mod.NEW.split('#if defined(__aarch64__) || defined(__arm64ec__)\n', 1)[1].split('#else\n', 1)[0]
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef unsigned long ULONG; typedef unsigned long long ULONGLONG; typedef uintptr_t ULONG_PTR; typedef long NTSTATUS;
typedef unsigned int DWORD; typedef void *HANDLE; typedef int PROCESSINFOCLASS;
#define WINAPI
#define DECLSPEC_HOTPATCH
struct _KUSER_SHARED_DATA { char pad[0x320]; struct { ULONG LowPart; ULONG High1Time; ULONG High2Time; } TickCount; };
static HANDLE GetCurrentProcess(void) { return (HANDLE)~(ULONG_PTR)0; }
static int mode, asked;
static struct _KUSER_SHARED_DATA page;
static NTSTATUS NtQueryInformationProcess( HANDLE h, PROCESSINFOCLASS c, void *info, ULONG size, ULONG *ret )
{
    asked++;
    if (h != GetCurrentProcess() || c != 1011 || size != sizeof(ULONG_PTR)) return 0xc0000003;
    if (mode == 1) return 0xc0000003;                      /* not BCD: an unknown class */
    *(ULONG_PTR *)info = mode == 2 ? 0 : (ULONG_PTR)&page; /* 2: not mapped yet */
    return 0;
}
''' + branch + r'''
static ULONGLONG GetTickCount64(void)
{
    ULONG high, low;
    do
    {
        high = user_shared_data->TickCount.High1Time;
        low = user_shared_data->TickCount.LowPart;
    }
    while (high != user_shared_data->TickCount.High2Time);
    return (ULONGLONG)high << 32 | low;
}
static DWORD GetTickCount(void) { return user_shared_data->TickCount.LowPart; }
int main( int argc, char **argv )
{
    mode = atoi( argv[1] );
    page.TickCount.LowPart = 0x89abcdef; page.TickCount.High1Time = page.TickCount.High2Time = 0x12;
    if (mode == 0)
    {
        if (GetTickCount() != 0x89abcdef || GetTickCount64() != 0x1289abcdefULL || GetTickCount() != 0x89abcdef) return 2;
        if (asked != 1) return 3;                           /* asked once, then the cached answer */
        printf( "direct\n" );
        return 0;
    }
    if (mode == 1)
    {
        if ((ULONG_PTR)bcd_user_shared_data() != 0x7ffe0000 || (ULONG_PTR)bcd_user_shared_data() != 0x7ffe0000 || asked != 1) return 4;
        printf( "old address for good\n" );
        return 0;
    }
    if ((ULONG_PTR)bcd_user_shared_data() != 0x7ffe0000) return 5;
    mode = 0;
    if (bcd_user_shared_data() != &page || asked != 2 || GetTickCount() != 0x89abcdef || asked != 2) return 6;
    printf( "asked again until known\n" );
    return 0;
}
'''
cc = os.environ.get('CC', 'cc')
if shutil.which(cc):
    with tempfile.TemporaryDirectory(prefix='bcd-k32usd-c-') as t:
        c = Path(t) / 'k.c'
        exe = Path(t) / 'k'
        c.write_text(harness)
        b = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined', str(c), '-o', str(exe)],
                           capture_output=True, text=True)
        check('the ARM64 branch compiles on the host (-Wall -Werror, sanitizers)', b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            for m, said in (('0', 'direct'), ('1', 'old address for good'), ('2', 'asked again until known')):
                r = subprocess.run([str(exe), m], capture_output=True, text=True)
                check(f'case {m}: {said}', r.returncode == 0 and said in r.stdout, f'exit {r.returncode} {r.stdout} {r.stderr[-500:]}')
else:
    print('skip: no C compiler for (b)')

# (c) the unix answer
proc = (root / 'build/ntdll-unix/process_ios.c').read_text()
q = proc[proc.index('NTSTATUS WINAPI NtQueryInformationProcess( HANDLE handle, PROCESSINFOCLASS class, void *info,'):]
q = q[:q.index('    switch (class)')]
check('process_ios.c answers class 1011 before the switch: the real page, the calling process only, the exact size',
      'if (class == (PROCESSINFOCLASS)1011)' in q and 'if (handle != GetCurrentProcess()) return STATUS_INVALID_PARAMETER;' in q
      and 'if (size != sizeof(ULONG_PTR)) return STATUS_INFO_LENGTH_MISMATCH;' in q and '*(ULONG_PTR *)info = (ULONG_PTR)ios_get_real_usd();' in q)
sig = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
check('ios_get_real_usd is the sub-floor service\'s own page (ios_exc_usd)', 'unsigned long long ios_get_real_usd(void)\n{\n    return (unsigned long long)ios_exc_usd;' in sig)

# (d) the build and its check
extras = (root / 'tools/build-wine-extra-dlls.sh').read_text()
block = extras[extras.index('if [ -n "$KUD" ]; then'):]
block = block[:block.index('\nfi\n') + 4]
check('build-wine-extra-dlls.sh: only from a kernel32 the pin left as it is, patched, built, verified, put back',
      'git -C "$R/wine" diff --quiet -- dlls/kernel32' in block and 'tools/patch-wine-kernel32-usd-direct.py' in block
      and 'make -C "$B" -j"$JOBS" dlls/kernel32/arm64ec-windows/kernel32.dll' in block
      and 'tools/verify-kernel32-usd.py" "$SHIP/kernel32-usd.dll.tmp" "$SHIP/kernel32.dll"' in block
      and 'git -C "$R/wine" checkout -- dlls/kernel32/sync.c' in block and '$CMB$KUD' in extras)
tools = os.environ.get('MINGW') or str(root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin')
shipped = root / 'app/Madeira/arm64ec-windows/kernel32.dll'
if shipped.is_file() and (shutil.which('llvm-objdump') or Path(tools, 'llvm-objdump').exists()):
    env = dict(os.environ, PATH=tools + os.pathsep + os.environ.get('PATH', ''))
    r = subprocess.run([sys.executable, str(root / 'tools/verify-kernel32-usd.py'), str(shipped), str(shipped)], capture_output=True, text=True, env=env)
    check('verify-kernel32-usd.py refuses the shipped kernel32.dll as the patched one', r.returncode != 0 and 'still builds 0x7ffe0000' in (r.stdout + r.stderr),
          r.stdout + r.stderr)
    usd = root / 'app/Madeira/arm64ec-windows/kernel32-usd.dll'
    if usd.is_file():
        r = subprocess.run([sys.executable, str(root / 'tools/verify-kernel32-usd.py'), str(usd), str(shipped)], capture_output=True, text=True, env=env)
        check('the built kernel32-usd.dll passes the verifier', r.returncode == 0, r.stdout + r.stderr)
else:
    print('skip: no llvm-objdump or shipped kernel32.dll for the verifier')

# (e) the bridge, the catalog, .gitignore, the workflow
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
b = bridge[bridge.index('const char *usdDirect = getenv("MADEIRA_USD_DIRECT");'):]
b = b[:b.index('this build has no kernel32-usd.dll')]
check('the bridge links kernel32-usd.dll as kernel32.dll (sysx64; system32 in an ARM64EC session) unless MADEIRA_USD_DIRECT=0',
      'if (usdDirect && !strcmp(usdDirect, "0"))' in b and '(use_arm64ec ? @[ sysx64Dir, sys32Dir ] : @[ sysx64Dir ])' in b
      and '[fm createSymbolicLinkAtPath:dst withDestinationPath:usdSrc error:nil];' in b and '@"kernel32-usd.dll"' in b
      and bridge.index('MADEIRA_OPTIN_DLLS lists %s, which this build does not ship') < bridge.index('const char *usdDirect'))
check('the session end removes MADEIRA_USD_DIRECT', 'unsetenv("MADEIRA_USD_DIRECT");' in bridge)
cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the catalog lists env.MADEIRA_USD_DIRECT, on by default', re.search(r'"env\.MADEIRA_USD_DIRECT": \{[^}]*"default": "1"', cat, re.S) is not None)
check('.gitignore keeps the built kernel32-usd.dll out of git', 'app/Madeira/arm64ec-windows/kernel32-usd.dll' in (root / '.gitignore').read_text())
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-kernel32-usd-direct.py' in wf)

print('PASS' if ok else 'FAILED: kernel32 usd-direct')
sys.exit(0 if ok else 1)
