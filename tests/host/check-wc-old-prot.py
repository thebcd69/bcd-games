#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_PROTECT_WRITECOPY_AS_RW: VirtualProtect reports PAGE_READWRITE for the data page a 32-bit program writes (V8's flag freeze); no Wine runs.

Battle.net (builds 594 and 596, the owner's iPhone18,2): the launcher's window spins for ever because its Chromium 108.0.5359.125 renderers
(`Battle.net.exe --type=renderer`, 32-bit, Blizzard's own libcef.dll) end on an int3 at guest 0x6de800e1 = libcef.dll+0x16d00e1, 9 times in 12.
The public CEF 108.4.13 libcef.dll (the only windows32 build of this Chromium; its PDB is in the release_symbols archive) holds the same V8:
at RVA 0x16a89a0 (0x27700 bytes earlier than Blizzard's 0x16d00a0) is v8::base::OS::SetDataReadOnly, whose second `int3; ud2` is
`CHECK_EQ(PAGE_READWRITE, old_protection)` after `CHECK(VirtualProtect(address, size, PAGE_READONLY, &old_protection))`, called by V8's
FlagList::FreezeFlags() on v8_flags (freeze_flags_after_init, on by default). BCD's NtProtectVirtualMemory returns the page's RECORDED
protection, PAGE_WRITECOPY for a writable image section for ever; Windows reports PAGE_READWRITE once the program has written the page.

What this checks:
  1. The evidence ties the log to the function: the public libcef's own bytes of SetDataReadOnly (68 bytes, embedded below) have a frame of 8 bytes
     (`sub esp,8`: the log's EBP - ESP = 0xcff2ac - 0xcff2a4 = 8), call VirtualProtect with PAGE_READONLY, and put the second int3 exactly 0x1c bytes
     after the instruction following the call (the log's host x21 = 0x6de800c5 and rip - 1 = 0x6de800e1: 0x1c); EAX = 1 is VirtualProtect's TRUE
     (the first CHECK passed); EDX = 0xfff8e440 = ntdll.dll+0x4e440 is the i386 syscall stub the NtProtectVirtualMemory thunk loads. Both builds'
     RVAs differ by the same 0x27700 at both points.
  2. The change (only the value handed back): compiled truth table of ios_wc_old_prot_report over every combination of the old and new
     protections and the flags; only (enabled, 32-bit, image view, old = PAGE_WRITECOPY, new = PAGE_READONLY) returns PAGE_READWRITE and says so.
  3. virtual_ios.c: one include, one local, one assignment under the lock, one guarded report; `*old_prot = old;` stays as it was and the guarded
     block runs only with the switch on; taking the four pieces out leaves no reference.
  4. Opt-in for Battle.net only: the switch is env.MADEIRA_PROTECT_WRITECOPY_AS_RW and only the exact value 1 turns it on; only Battle.net's launcher
     entry sets it (the Rockstar Games Launcher, the EA app, the common lines and the game lists do not; a 64-bit process is excluded in the code).
  5. The auto-tune rule, its fixture, the catalog entry and the CI step exist.
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
virtual_src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
header_path = root / 'build/ntdll-unix/ios_wc_old_prot.h'
header = header_path.read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def strip_comments(t):
    t = re.sub(r'/\*.*?\*/', '', t, flags=re.S)
    return re.sub(r'//[^\n]*', '', t)


code = strip_comments(header)

# ---------------------------------------------------------------- 1. the evidence
# v8::base::OS::SetDataReadOnly from the public CEF 108.4.13 windows32 libcef.dll (RVA 0x16a89a0, size 0x44; V8 10.8.168.22)
FN = bytes.fromhex('5589e583ec088b45088b4d0c8b1524007a1931ea8955fc8d55f8526a025150ff15603f791985c07415837df80475128b4dfc31e9e86daf370283c4085dc3cc0f0bcc0f0b')
check('the embedded function is 0x44 bytes and starts with push ebp; mov ebp,esp; sub esp,8', len(FN) == 0x44 and FN[:6] == bytes.fromhex('5589e583ec08'))
call = FN.index(b'\xff\x15')
ret_after_call = call + 6
check('it calls through the import table with (address, size, 2 = PAGE_READONLY, &old) pushed in that order',
      FN[call - 8:call] == bytes.fromhex('8d55f8526a025150') and FN[call - 8:call - 5] == bytes.fromhex('8d55f8')
      and FN[call - 4:call - 2] == bytes.fromhex('6a02'))
check('then test eax,eax; je int3 (the first CHECK, on VirtualProtect\'s result) and cmp dword [ebp-8],4; jne int3 (the second: old == PAGE_READWRITE)',
      FN[ret_after_call:ret_after_call + 4] == bytes.fromhex('85c07415') and FN[ret_after_call + 4:ret_after_call + 8] == bytes.fromhex('837df804')
      and FN[ret_after_call + 8:ret_after_call + 10] == bytes.fromhex('7512'))
first_int3 = ret_after_call + 2 + 2 + 0x15
second_int3 = ret_after_call + 4 + 4 + 2 + 0x12
check('the jumps land on the two `cc 0f 0b`; the second int3 is exactly 0x1c bytes after the instruction that follows the call',
      FN[first_int3:first_int3 + 3] == b'\xcc\x0f\x0b' and FN[second_int3:second_int3 + 3] == b'\xcc\x0f\x0b' and second_int3 - ret_after_call == 0x1c,
      '%#x %#x' % (first_int3, second_int3))
# the log (build 596, renderer pid 04c0, [int3-guest]): registers and the host x20/x21
RIP, X21, EAX, EBP, ESP, EDX = 0x6de800e2, 0x6de800c5, 1, 0xcff2ac, 0xcff2a4, 0xfff8e440
check('the log\'s int3 is 0x1c bytes after the return address of the call (host x21), as in the function', (RIP - 1) - X21 == 0x1c == second_int3 - ret_after_call)
check('the log\'s frame is the function\'s: EBP - ESP = 8 (sub esp,8)', EBP - ESP == FN[5] == 8)
check('EAX = 1: VirtualProtect returned TRUE, so the int3 is the SECOND check (the first is `test eax,eax; je`)', EAX != 0)
LIBCEF_BASE, NTDLL_BASE = 0x6c7b0000, 0xfff40000
check('EDX = ntdll.dll+0x4e440 (the log\'s ntdll is at 0xfff40000), the i386 `mov edx, ___wine_syscall` stub',
      EDX - NTDLL_BASE == 0x4e440)
check('the two builds differ by the same 0x27700 at the return address and at the int3 (public libcef RVA 0x16a89c5 / 0x16a89e1)',
      (X21 - LIBCEF_BASE) - 0x16a89c5 == 0x27700 and (RIP - 1 - LIBCEF_BASE) - 0x16a89e1 == 0x27700)
THUNK = bytes.fromhex('b850000000ba40e4c47bffd2c2140090')    # NtProtectVirtualMemory in app/Madeira/i386-windows/ntdll.dll (preferred base 0x7bc00000)
check('the i386 NtProtectVirtualMemory thunk loads exactly that stub (0x7bc4e440 - 0x7bc00000 = 0x4e440) and returns the status in EAX',
      THUNK[:5] == bytes.fromhex('b850000000') and int.from_bytes(THUNK[6:10], 'little') - 0x7bc00000 == 0x4e440 and THUNK[10:12] == bytes.fromhex('ffd2'))

# ---------------------------------------------------------------- 2. the change: a compiled truth table
cc = os.environ.get('CC', 'cc')
HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#define PAGE_NOACCESS 0x01
#define PAGE_READONLY 0x02
#define PAGE_READWRITE 0x04
#define PAGE_WRITECOPY 0x08
#define PAGE_EXECUTE 0x10
#define PAGE_EXECUTE_READ 0x20
#define PAGE_EXECUTE_READWRITE 0x40
#define PAGE_EXECUTE_WRITECOPY 0x80
#define PAGE_GUARD 0x100
#define PAGE_NOCACHE 0x200
#define PAGE_WRITECOMBINE 0x400
'''
MAIN = r'''
int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "enabled")) { printf("enabled=%d\n", ios_wc_old_prot_enabled()); return 0; }
    static const unsigned prots[] = { 0, 1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80, 0x102, 0x104, 0x108, 0x202, 0x204, 0x208, 0x402, 0x404, 0x408, 0x110, 0x120, 0x140, 0x180 };
    unsigned n = sizeof(prots) / sizeof(prots[0]), i, j, img, wow, en, changed_total = 0, rows = 0;

    for (i = 0; i < n; i++)
        for (j = 0; j < n; j++)
            for (img = 0; img < 2; img++)
                for (wow = 0; wow < 2; wow++)
                    for (en = 0; en < 2; en++)
                    {
                        int changed = 77;
                        unsigned old = prots[i], nw = prots[j];
                        unsigned r = ios_wc_old_prot_report( old, nw, (int)img, (int)wow, (int)en, &changed );
                        int want = en && wow && img && old == PAGE_WRITECOPY && nw == PAGE_READONLY;

                        rows++;
                        if (changed != want || r != (want ? PAGE_READWRITE : old))
                        {
                            printf("BAD old=%#x new=%#x img=%u wow=%u en=%u -> %#x changed=%d\n", old, nw, img, wow, en, r, changed);
                            return 1;
                        }
                        if (changed) changed_total++;
                    }
    printf("rows=%u changed=%u\n", rows, changed_total);
    return 0;
}
'''
if not shutil.which(cc):
    print('skip the compiled runs: no C compiler')
else:
    with tempfile.TemporaryDirectory(prefix='wcold-') as d:
        d = Path(d)
        (d / 'h.c').write_text(HARNESS + '#include <string.h>\n' + header + '\n' + MAIN)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(d / 'h.c'), '-o', str(d / 'h')], capture_output=True, text=True)
        check('the production header compiles (host cc, -Wall -Wextra -Werror)', c.returncode == 0, c.stderr[-2000:])
        if c.returncode == 0:
            r = subprocess.run([str(d / 'h')], capture_output=True, text=True)
            m = re.match(r'rows=(\d+) changed=(\d+)', r.stdout)
            check('every one of %d combinations returns the old protection untouched, except old = PAGE_WRITECOPY, new = PAGE_READONLY, an image view, a 32-bit '
                  'process and the switch on, which returns PAGE_READWRITE and says so (exactly one old/new pair, 1 combination of the flags)' % (22 * 22 * 8),
                  r.returncode == 0 and m and int(m.group(1)) == 22 * 22 * 8 and int(m.group(2)) == 1, r.stdout + r.stderr)

            def enabled(**env):
                e = {k: v for k, v in os.environ.items() if k != 'MADEIRA_PROTECT_WRITECOPY_AS_RW'}
                e.update(env)
                return subprocess.run([str(d / 'h'), 'enabled'], capture_output=True, text=True, env=e).stdout.strip()
            vals = {v: enabled(MADEIRA_PROTECT_WRITECOPY_AS_RW=v) for v in ('', '0', '11', 'true', ' 1', '1 ', 'yes', '2', '1')}
            vals[None] = enabled()
            check('only the exact value 1 turns the switch on (unset, "", 0, 11, true, " 1", "1 ", yes, 2: off)',
                  vals['1'] == 'enabled=1' and all(vals[v] == 'enabled=0' for v in vals if v != '1'), str(vals))

# ---------------------------------------------------------------- 3. virtual_ios.c: four small pieces, the original assignment kept
inc = '#include "ios_wc_old_prot.h"'
check('the header is included exactly once, under WINE_IOS, right before NtProtectVirtualMemory',
      virtual_src.count(inc) == 1 and virtual_src.index(inc) < virtual_src.index('NTSTATUS WINAPI NtProtectVirtualMemory( HANDLE process, PVOID *addr_ptr, SIZE_T *size_ptr,')
      and virtual_src.index('NTSTATUS WINAPI NtProtectVirtualMemory( HANDLE process, PVOID *addr_ptr, SIZE_T *size_ptr,') - virtual_src.index(inc) < 600
      and virtual_src[virtual_src.rindex('#ifdef WINE_IOS', 0, virtual_src.index(inc)):virtual_src.index(inc)].count('#endif') == 0)
fn_start = virtual_src.index('NTSTATUS WINAPI NtProtectVirtualMemory( HANDLE process, PVOID *addr_ptr, SIZE_T *size_ptr,')
fn = virtual_src[fn_start:virtual_src.index('\n}\n', fn_start)]
check('one local, set under the lock right after old is computed, and one call site',
      fn.count('int ios_wc_image = 0;') == 1 and fn.count('ios_wc_image = (view->protect & SEC_IMAGE) != 0;') == 1
      and fn.index('old = get_win32_prot( vprot, view->protect );') < fn.index('ios_wc_image = (view->protect & SEC_IMAGE) != 0;') < fn.index('status = set_protection( view, base, size, new_prot );')
      and fn.count('ios_wc_old_prot_report(') == 1)
site = fn.index('if (ios_wc_old_prot_enabled())')
check('the report is behind `if (ios_wc_old_prot_enabled())`, right after `*old_prot = old;` in the success branch, and only the value is touched',
      fn.index('*old_prot = old;') < site and fn.count('*old_prot = old;') == 1 and fn[site - 400:site].count('*old_prot = old;') == 1
      and re.search(r'if \(ios_wc_old_prot_enabled\(\)\)\s*\{\s*int wc_changed = 0;\s*\*old_prot = ios_wc_old_prot_report\( old, new_prot, ios_wc_image, ios_wow_base\(\) != 0, 1, &wc_changed \);', fn))
block = fn[site:fn.index('#endif', site)]
forbidden = ('set_protection', 'set_vprot', 'mprotect', 'status =', 'return', 'old =', 'base =', 'size =')
check('the guarded block assigns only *old_prot (no protection call, no status, no return)', not [f for f in forbidden if f in block], str([f for f in forbidden if f in block]))
stripped = virtual_src
stripped = re.sub(r'#ifdef WINE_IOS\n/\* ajan18-bnet-cef \(build 597\): env\.MADEIRA_PROTECT_WRITECOPY_AS_RW.*?#include "ios_wc_old_prot\.h"\n#endif\n\n', '', stripped, flags=re.S)
stripped = re.sub(r'#ifdef WINE_IOS\n    int ios_wc_image = 0;[^\n]*\n#endif\n', '', stripped)
stripped = re.sub(r'#ifdef WINE_IOS\n            ios_wc_image = \(view->protect & SEC_IMAGE\) != 0;\n#endif\n', '', stripped)
stripped = re.sub(r'#ifdef WINE_IOS\n        /\* ajan18-bnet-cef \(build 597\): the value handed back.*?\n#endif\n\n', '', stripped, flags=re.S)
leftovers = re.findall(r'[^\n]*(?:ios_wc_|WRITECOPY_AS_RW|wc_changed|\[wc-old\])[^\n]*', stripped)
check('taking out the four pieces leaves no reference to the fix in virtual_ios.c', not leftovers, str(leftovers[:4]))
check('the 32-bit test is the calling pseudo-process\'s window (ios_wow_base() != 0): a 64-bit process never gets the changed value',
      'ios_wow_base() != 0' in fn and 'ios_wc_image' in fn)
forbidden_h = ('mprotect', 'vm_protect', 'abort(', 'exit(', 'fopen(', 'malloc(', 'free(', 'set_vprot', 'set_protection')
check('the header decides a value and reads one environment variable; nothing else', not [f for f in forbidden_h if f in code] and code.count('getenv(') == 1
      and 'getenv( "MADEIRA_PROTECT_WRITECOPY_AS_RW" )' in code and 'enabled = e && e[0] == \'1\' && !e[1];' in code)

# ---------------------------------------------------------------- 4. opt-in for Battle.net only
launchers = (root / 'app/Madeira/BCDLaunchers.swift').read_text()
bn = launchers[launchers.index('static let battlenet = BCDLauncherSpec('):launchers.index('static let eaApp = BCDLauncherSpec(')]
ea = launchers[launchers.index('static let eaApp = BCDLauncherSpec('):]
common = re.search(r'static let commonSessionConfig = \[(.*?)\]', launchers).group(1)
line = '"env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1"'
check('Battle.net\'s launcher entry sets env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1, once, and nothing else of the file does',
      launchers.count(line) == 1 and line in bn and 'MADEIRA_PROTECT_WRITECOPY_AS_RW' not in ea.split('\n}\n')[0] and 'MADEIRA_PROTECT_WRITECOPY_AS_RW' not in common
      # round 630 (agent A2 S5): the 32-bit fault report follows on the next line of the same list
      and 'sessionConfig: Self.commonSessionConfig + [' + line + ', "env.MADEIRA_INT3_X86_DUMP = 1",\n' in bn
      and '"env.MADEIRA_X86_FAULT_DUMP = 1"],' in bn)


def tracked(rootdir):
    try:
        out = subprocess.run(['git', '-C', str(rootdir), 'ls-files'], capture_output=True, text=True, check=True).stdout.split('\n')
        return [Path(rootdir) / f for f in out if f]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in Path(rootdir).rglob('*') if p.is_file() and '.git' not in p.parts]


allowed = {
    'build/ntdll-unix/ios_wc_old_prot.h', 'build/ntdll-unix/virtual_ios.c', 'app/Madeira/BCDLaunchers.swift', 'build/tools/gen-config-catalog.py',
    'app/Madeira/ConfigCatalog.generated.swift', 'tests/host/check-wc-old-prot.py', 'tests/host/check-int3-x86-diag.py',
    '.github/workflows/build-ipa.yml', 'build/tools/autotune_rules.py', 'app/Madeira/AutoTuneRules.generated.swift', 'tools/int3x86-match.py',
    # round 630 (agent A2 S5): the launcher check pins Battle.net's session lines, this switch among them
    'tests/host/check-bcd-launchers.py',
}
users = []
for f in tracked(root):
    rel = str(f.relative_to(root))
    if rel in allowed or not f.is_file() or f.suffix in ('.png', '.jpg', '.ipa', '.dll', '.exe', '.a', '.dylib', '.zip', '.xz', '.gz', '.bz2', '.pdf', '.icns'):
        continue
    if rel.startswith('docs/') or rel.startswith('tests/fixtures/'):
        continue
    try:
        if 'MADEIRA_PROTECT_WRITECOPY_AS_RW' in f.read_text(errors='ignore'):
            users.append(rel)
    except OSError:
        pass
check('no other source, game list, recommendation or test mentions the switch (docs and fixtures aside)', not users, str(users))
recs = (root / 'recommended/recommendations.json').read_text() if (root / 'recommended/recommendations.json').exists() else ''
check('the bundled game lists (Rockstar Games Launcher and every game among them) do not set it', 'PROTECT_WRITECOPY' not in recs)

# ---------------------------------------------------------------- 5. auto-tune rule, fixture, catalog, CI
sys.path.insert(0, str(root / 'build/tools'))
import importlib.util
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rule = next((r for r in mod.RULES if r['id'] == 'chromium-v8-flags-freeze-writecopy'), None)
check('auto-tune has the rule chromium-v8-flags-freeze-writecopy with the one line', bool(rule) and rule['lines'] == ['env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1'], str(rule and rule['lines']))
if rule:
    check('...it needs the 32-bit Chromium renderer\'s int3 and is silent once the line is in force ([madeira-env] game MADEIRA_PROTECT_WRITECOPY_AS_RW=1)',
          bool(rule.get('all')) and rule.get('none') == [{'has': ['[madeira-env] game MADEIRA_PROTECT_WRITECOPY_AS_RW=1']}] and rule.get('noRetry') is True, str(rule.get('none')))
gen = (root / 'app/Madeira/AutoTuneRules.generated.swift').read_text()
check('AutoTuneRules.generated.swift has it', '"chromium-v8-flags-freeze-writecopy"' in gen and 'env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1' in gen)
fx = root / 'tests/fixtures/autotune/battlenet-renderer-v8-flags-freeze-2026-10-23'
expect = json.loads(Path(str(fx) + '.expect').read_text()) if Path(str(fx) + '.expect').exists() else {}
check('the fixture (cut from the 596 log) proposes the line and names the rule', Path(str(fx) + '.log').exists()
      and expect.get('next') == ['env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1'] and 'chromium-v8-flags-freeze-writecopy' in expect.get('matched', []) and expect.get('note'), str(expect))
gen_cat = (root / 'build/tools/gen-config-catalog.py').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the config catalog (generator and generated Swift) has the entry, default off, with its sources',
      '"env.MADEIRA_PROTECT_WRITECOPY_AS_RW"' in gen_cat and '"sources": ["build/ntdll-unix/ios_wc_old_prot.h", "build/ntdll-unix/virtual_ios.c"]' in gen_cat
      and 'ConfigOption(key: "env.MADEIRA_PROTECT_WRITECOPY_AS_RW", title: "A written data page reports PAGE_READWRITE when write-protected", kind: .bool, defaultValue: "0"' in catalog)
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('build-ipa.yml runs this check', 'python3 tests/host/check-wc-old-prot.py' in ci and ci.count('check-wc-old-prot.py') == 1)

# the real file still compiles for iOS (when the wine headers exist)
wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
config = wine / 'build-macos/include/config.h'
if shutil.which('xcrun') and config.exists():
    try:
        sdk = subprocess.run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ''
    if sdk:
        build = root / 'build/ntdll-unix'
        flags = ['-fsyntax-only', '-arch', 'arm64', '-isysroot', sdk, '-miphoneos-version-min=17.0', '-O2', '-fPIC',
                 '-fno-strict-aliasing', '-Wall', '-Wno-implicit-function-declaration', '-Wno-int-conversion',
                 '-include', str(config), '-include', str(build / 'shims/wine_ios_exit.h'), f'-I{build}', f'-I{build / "shims"}',
                 f'-I{wine / "build-macos/dlls/ntdll"}', f'-I{wine / "dlls/ntdll"}', f'-I{wine / "dlls/ntdll/unix"}',
                 f'-I{wine / "build-macos/include"}', f'-I{wine / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES',
                 '-DLTC_SOURCE', '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB',
                 '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context', '-Dset_thread_context=ntdll_set_thread_context']
        r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(build / 'virtual_ios.c')], capture_output=True, text=True)
        check('virtual_ios.c compiles for iOS (syntax only, pinned wine headers)', r.returncode == 0, r.stderr[-2000:])
        check('...and the new header adds no warning or error', 'ios_wc_old_prot' not in r.stderr and 'ios_wc_' not in r.stderr and 'wc_changed' not in r.stderr, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

print('PASS: MADEIRA_PROTECT_WRITECOPY_AS_RW' if ok else 'FAILED: MADEIRA_PROTECT_WRITECOPY_AS_RW')
sys.exit(0 if ok else 1)
