#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-bit program's class ATOM stays an atom between wow64win and win32u; no Wine runs.

Bioshock Remastered (BioshockHD.exe; Send Log DW1G97) and LEGO Batman 2 (XJ7SDS), both on build 592, ended 1.5 s after the start, before any
window existed, with exit code 0x696d696c: the host faulted in wine_dbgstr_wn (`ldrh w9,[x8],#2`, NtUserCreateWindowEx+0x100) at guest 0x8002, on
the unix stack of a system call, where the SEH dispatcher refuses to deliver ("Exception frame is not in stack limits"). Need For Speed The Run
(0WJTA3, build 579) did the same at 0xC023. All three create their first window with a class ATOM. dlls/wow64win converts a pointer by adding
the guest window base (guest_ptr32), so the atom arrived as 0x7100008002; win32u tells a string from an atom with IS_INTRESOURCE(), saw a string
and read it.

Where it is fixed, and what ships:
  * `app/Madeira/aarch64-windows/wow64win.dll` is a PREBUILT binary (commit 9e8291eb) that no build step compiles, so a change in
    dlls/wow64win/ reaches no device until someone rebuilds and recommits that DLL (which would also carry every wow64win change since
    2026-09-29). The Wine commit still makes the source right (wow64_NtUserCreateWindowEx takes `class` with get_str_or_atom; UNICODE_STRING
    Buffer and CREATESTRUCT lpszClass pass a value below 64 KB through): checked below when a Wine source is at hand.
  * What ships: libwin32u_unix.a is compiled from dlls/win32u and build/win32u-unix/class_ios.c in every build. class_ios.c now has
    ios_atom_arg(): a value inside [B + 1, B + 0xffff] (B = the 32-bit process's guest window base, ios_wow_base()) is given back as the atom;
    every other value, and every 64-bit process (B = 0), is returned as it came. get_int_atom_value (class_ios.c, every class lookup by name) and
    dlls/win32u/window.c (NtUserCreateWindowEx's `class`, NtUserGetProp, NtUserSetProp, NtUserRemoveProp) call it first. MADEIRA_ATOM_ARG=0 turns
    it off. Only paths that end in a host fault today change (no mapping exists in the first 64 KB of the guest window).

Held here:
  1. class_ios.c: the helper's text; the cut `ios_atom_unrebase` compiled on the host and run over a probe set (32-bit values, no base, the base
     itself, B+1, B+0x8002, B+0xC023, B+0xFFFF, B+0x10000, B+0x400000, other windows' bases, a host stack address); the call in get_int_atom_value;
     the env switch default on; the log capped at 8 lines; build.sh compiles class_ios.c as class;
  2. The Wine source (wine/ of this checkout, $BCD_WINE_SRC, or ~/madeira-bcd/wine; skipped when none carries the change and none is the commit this
     checkout pins): window.c declares ios_atom_arg under WINE_IOS (identity otherwise) and calls it in exactly the four places; the wow64win source
     helpers compiled next to the originals as before;
  3. Auto-tune: the rule wow64-atom-class-rebased says "fixed in 598" for the log (fixture bioshock-remastered-createwindow-atom-2026-10-23);
     CI runs this check.
Needs python3 and a C compiler for the compiled parts.
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
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 3]


cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

# ---------------------------------------------------------------- 1. this repository: class_ios.c
cls = (root / 'build/win32u-unix/class_ios.c').read_text()
build_sh = (root / 'build/win32u-unix/build.sh').read_text()
check('build.sh compiles class_ios.c as the class module (libwin32u_unix.a, every build)', 'compile_one "$BUILD_DIR/class_ios.c" "class"' in build_sh)
check('ios_atom_arg: a 32-bit value is returned at once, the rest goes through ios_atom_unrebase(value, ios_wow_base()), the switch can turn it off, the log is capped',
      'if (!(value >> 32)) return ptr;' in cls and 'atom = ios_atom_unrebase( value, ios_wow_base() );' in cls
      and 'if (atom == value || !ios_atom_arg_enabled()) return ptr;' in cls
      and '< 8 &&' in cls and 'fprintf( stderr, "[atom-arg] ml1361 guest=%#lx atom=%#lx\\n"' in cls)
check('the switch is MADEIRA_ATOM_ARG, on unless it is exactly 0', 'getenv( "MADEIRA_ATOM_ARG" )' in cls and 'enabled = !env || strcmp( env, "0" );' in cls)
check('get_int_atom_value reads the Buffer through ios_atom_arg (every class lookup by name)',
      'const WCHAR *ptr = ios_atom_arg( name->Buffer );' in function(cls, 'static ATOM get_int_atom_value( UNICODE_STRING *name )'))
b = cls.index('/* atom-test:begin */')
e = cls.index('/* atom-test:end */')
if cc:
    HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
typedef uintptr_t ULONG_PTR;
''' + cls[b:e] + r'''
#define T(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void) {
    const ULONG_PTR B = 0x7100000000ull, B2 = 0x7300000000ull;
    /* a 32-bit value and a 64-bit process (no base) are never touched */
    T(ios_atom_unrebase(0x8002, B) == 0x8002); T(ios_atom_unrebase(0xC023, 0) == 0xC023);
    T(ios_atom_unrebase(B + 0x8002, 0) == B + 0x8002);
    /* the base itself is not an atom (a rebased NULL is NULL); B + 1 .. B + 0xffff is */
    T(ios_atom_unrebase(B, B) == B); T(ios_atom_unrebase(B + 1, B) == 1);
    T(ios_atom_unrebase(B + 0x8002, B) == 0x8002); T(ios_atom_unrebase(B + 0xC023, B) == 0xC023); T(ios_atom_unrebase(B + 0xFFFF, B) == 0xFFFF);
    /* from 64 KB up it is a pointer into the guest window: untouched */
    T(ios_atom_unrebase(B + 0x10000, B) == B + 0x10000); T(ios_atom_unrebase(B + 0x400000, B) == B + 0x400000);
    T(ios_atom_unrebase(B + 0xFFFFFFFFull, B) == B + 0xFFFFFFFFull);
    /* below the base, a host address (stack, pool) and another process's window are untouched */
    T(ios_atom_unrebase(0x16f2f0000ull, B) == 0x16f2f0000ull); T(ios_atom_unrebase(0x703891e790ull, B) == 0x703891e790ull);
    T(ios_atom_unrebase(B2 + 0x8002, B) == B2 + 0x8002); T(ios_atom_unrebase(B2 + 0x8002, B2) == 0x8002);
    puts("helper: ok");
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / 't.c'
        c.write_text(HARNESS)
        exe = Path(tmp) / 't'
        r = subprocess.run([cc, '-O1', '-Wall', '-Werror', '-o', str(exe), str(c)], capture_output=True, text=True)
        check('the un-rebase helper compiles on the host', r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print(run.stdout.rstrip())
            check('an atom inside [B+1, B+0xffff] comes back as the atom; everything else is returned as it came', run.returncode == 0)
else:
    print('note: no host C compiler; the helper was not run')

# ---------------------------------------------------------------- where the Wine source is
pin = ''
tree = git('ls-tree', 'HEAD', 'wine')
if tree:
    pin = tree.split()[2]
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'dlls/wow64win/wow64win_private.h').is_file():
        wine = Path(candidate)
        break
header = (wine / 'dlls/wow64win/wow64win_private.h').read_text() if wine else ''
window_c = (wine / 'dlls/win32u/window.c').read_text() if wine else ''
has_change = 'guest_ptr_or_intres32' in header and 'ios_atom_arg' in window_c
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    user = (wine / 'dlls/wow64win/user.c').read_text()
    # ------------------------------------------------------------ 2a. win32u's window.c (compiled into every build)
    check('window.c declares ios_atom_arg for WINE_IOS (extern) and as the identity for any other build',
          '#ifdef WINE_IOS' in window_c and 'extern const WCHAR *ios_atom_arg( const WCHAR *ptr );' in window_c
          and 'static inline const WCHAR *ios_atom_arg( const WCHAR *ptr ) { return ptr; }' in window_c)
    for name, sig in (('NtUserGetProp', 'HANDLE WINAPI NtUserGetProp( HWND hwnd, const WCHAR *str )'),
                      ('NtUserSetProp', 'BOOL WINAPI NtUserSetProp( HWND hwnd, const WCHAR *str, HANDLE handle )'),
                      ('NtUserRemoveProp', 'HANDLE WINAPI NtUserRemoveProp( HWND hwnd, const WCHAR *str )')):
        body = function(window_c, sig)
        check('%s takes its property name through ios_atom_arg before it looks at it' % name,
              'str = ios_atom_arg( str );' in body and body.index('str = ios_atom_arg( str );') < body.index('IS_INTRESOURCE(str)'))
    cw = window_c[window_c.index('HWND WINAPI NtUserCreateWindowEx('):]
    cw = cw[:cw.index('fix_cs_coordinates( &cs, &sw );')]
    check('NtUserCreateWindowEx takes `class` through ios_atom_arg before its TRACE (which reads it) and before IS_INTRESOURCE(class)',
          'class = ios_atom_arg( class );' in cw and cw.index('class = ios_atom_arg( class );') < cw.index('TRACE( "ex_style'))
    check('...and nowhere else in window.c', window_c.count('ios_atom_arg(') == 4 + 2, str(window_c.count('ios_atom_arg(')))
    # ------------------------------------------------------------ 2b. the wow64win source (a prebuilt DLL: not shipped until rebuilt)
    check('get_str_or_atom (NtUserSetProp, GetProp, RemoveProp\'s helper) lets a value below 64 KB through unchanged',
          'if (!v || (v >> 16)) return guest_ptr32( v );\n    return (void *)(ULONG_PTR)v;' in header)
    check('guest_ptr_or_intres32: 0 and anything of 64 KB or more is guest_ptr32\'s, anything else the value itself',
          'if (!value || (value >> 16)) return guest_ptr32( value );\n    return (void *)(ULONG_PTR)value;' in header)
    check('host_ptr_or_intres32: anything of 64 KB or more is host_ptr32\'s, anything else the value itself',
          'if ((ULONG_PTR)value >> 16) return host_ptr32( value );\n    return (ULONG)(ULONG_PTR)value;' in header)
    ucs = function(header, 'static inline UNICODE_STRING *unicode_str_32to64( UNICODE_STRING *str, const UNICODE_STRING32 *str32 )')
    check('unicode_str_32to64 converts the Buffer with guest_ptr_or_intres32 and still copies Length and MaximumLength',
          'str->Buffer = guest_ptr_or_intres32( str32->Buffer );' in ucs and 'str->Length = str32->Length;' in ucs
          and 'str->MaximumLength = str32->MaximumLength;' in ucs and 'if (!str32) return NULL;' in ucs)
    cwe = function(user, 'NTSTATUS WINAPI wow64_NtUserCreateWindowEx( UINT *args )')
    check('wow64_NtUserCreateWindowEx takes `class` (the 16th argument) with get_str_or_atom, every other pointer as before',
          'const WCHAR *class = get_str_or_atom( &args );' in cwe and 'HINSTANCE instance = get_ptr( &args );' in cwe
          and 'void *params = get_ptr( &args );' in cwe and 'HINSTANCE client_instance = get_ptr( &args );' in cwe
          and cwe.count('get_str_or_atom') == 1)
    check('CREATESTRUCT: lpszClass goes through unchanged both ways, lpszName and the rest are converted as before',
          'to->lpszClass      = guest_ptr_or_intres32( from->lpszClass );' in user and 'cs.lpszClass      = host_ptr_or_intres32( from->lpszClass );' in user
          and 'to->lpszName       = guest_ptr32( from->lpszName );' in user and 'cs.lpszName       = host_ptr32( from->lpszName );' in user)
    check('the new helpers are used exactly where intended (two definitions, the string conversion, the two CREATESTRUCT copies; the thunk\'s class is the fourth get_str_or_atom)',
          len(re.findall(r'guest_ptr_or_intres32|host_ptr_or_intres32', user + header)) == 5 and user.count('get_str_or_atom') == 4)
    if cc:
        guest_real = function(header, 'static inline void *guest_ptr32( ULONG addr )')
        host_real = function(header, 'static inline ULONG host_ptr32( const void *addr )')
        check('guest_ptr32 and host_ptr32 are the base-adding conversions the harness stands in for',
              'return addr ? (void *)(wow64win_guest_base() + addr) : NULL;' in guest_real
              and 'return addr ? (ULONG)((ULONG_PTR)addr - wow64win_guest_base()) : 0;' in host_real)
        new_g = function(header, 'static inline void *guest_ptr_or_intres32( ULONG value )')
        new_h = function(header, 'static inline ULONG host_ptr_or_intres32( const void *value )')
        str_real = function(header, 'static inline void *get_str_or_atom( UINT **args )')
        ucs_new = ucs
        ucs_old = ucs.replace('guest_ptr_or_intres32( str32->Buffer )', 'guest_ptr32( str32->Buffer )').replace('unicode_str_32to64', 'unicode_str_32to64_original')
        HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef unsigned int ULONG; typedef unsigned int UINT; typedef unsigned short USHORT; typedef uintptr_t ULONG_PTR; typedef unsigned short WCHAR;
#define WINAPI
typedef struct { USHORT Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { USHORT Length, MaximumLength; ULONG Buffer; } UNICODE_STRING32;
#define BASE 0x7100000000ull
static ULONG_PTR wow64win_guest_base(void) { return (ULONG_PTR)BASE; }
''' + guest_real + '\n' + host_real + '\n' + new_g + '\n' + new_h + '\n' + str_real + '\n' + ucs_new + '\n' + ucs_old + r'''
#define T(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void) {
    static const ULONG probe[] = { 0, 1, 0x8002, 0xC01A, 0xFFFF, 0x10000, 0x10001, 0x400000, 0x7FFE0000, 0xFFFFFFFFu };
    unsigned i;
    for (i = 0; i < sizeof probe / sizeof *probe; i++) {
        ULONG v = probe[i];
        void *old = guest_ptr32(v), *now = guest_ptr_or_intres32(v);
        if (v == 0 || v >= 0x10000) { T(now == old); } else { T(now == (void *)(ULONG_PTR)v); T(now != old); }
        T(host_ptr_or_intres32(now) == v);
        if (v >= 0x10000 || v == 0) T(host_ptr_or_intres32(now) == host_ptr32(old));
        { UINT args[2] = { v, 0x1234 }; UINT *p = args; void *got = get_str_or_atom(&p); T(p == args + 1); T(got == now); }
        { UNICODE_STRING32 s32 = { 0, 0, v }; UNICODE_STRING a, b;
          T(unicode_str_32to64(&a, &s32) == &a); T(unicode_str_32to64_original(&b, &s32) == &b);
          T(a.Length == b.Length && a.MaximumLength == b.MaximumLength);
          T((void *)a.Buffer == now); if (v == 0 || v >= 0x10000) T(a.Buffer == b.Buffer); }
        { UNICODE_STRING32 s32 = { 10, 12, v }; UNICODE_STRING a, b;
          unicode_str_32to64(&a, &s32); unicode_str_32to64_original(&b, &s32);
          T(a.Length == 10 && a.MaximumLength == 12); if (v == 0 || v >= 0x10000) T(a.Buffer == b.Buffer); }
    }
    T(unicode_str_32to64(NULL, NULL) == NULL);
    { void *p = (void *)(ULONG_PTR)(BASE + 0x400000); T(host_ptr_or_intres32(p) == host_ptr32(p) && host_ptr_or_intres32(p) == 0x400000); }
    T((ULONG_PTR)guest_ptr_or_intres32(0xFFFF) == 0xFFFF && (ULONG_PTR)guest_ptr_or_intres32(0x10000) == BASE + 0x10000);
    puts("wow64win helpers: ok");
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / 't.c'
            c.write_text(HARNESS)
            exe = Path(tmp) / 't'
            r = subprocess.run([cc, '-O1', '-Wall', '-Werror', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
            check('the wow64win helpers compile on the host', r.returncode == 0, r.stderr[-2000:])
            if r.returncode == 0:
                run = subprocess.run([str(exe)], capture_output=True, text=True)
                print(run.stdout.rstrip())
                check('atoms stay atoms both ways; every other value converts exactly as before', run.returncode == 0)

# ---------------------------------------------------------------- 3. auto-tune and CI
rules = (root / 'build/tools/autotune_rules.py').read_text()
check('auto-tune: the rule wow64-atom-class-rebased says fixed in 598, with no line, on the host fault of wine_dbgstr_wn and the refused dispatch',
      '"id": "wow64-atom-class-rebased"' in rules and '"fixedIn": 598' in rules
      and 's("[av-detail]", "p0=READ", "hinsn=0x78402509")' in rules
      and 's("Exception frame is not in stack limits => unable to dispatch exception")' in rules)
expect = root / 'tests/fixtures/autotune/bioshock-remastered-createwindow-atom-2026-10-23.expect'
check('the fixture is there and expects "known"', expect.is_file() and json.loads(expect.read_text())['status'] == 'known'
      and json.loads(expect.read_text())['matched'][:1] == ['wow64-atom-class-rebased'])   # night round 2026-11-11: a diagnostic rule may follow
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'run: python3 tests/host/check-wow64win-atoms.py' in workflow)
sys.exit(0 if ok else 1)
