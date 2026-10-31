#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Name a .NET start-up death (round 624, agent E2 S2c; Slay the Spire 2 BMVD7C; log only).

BMVD7C's .NET runtime failed fast (c0000602) right after it started; the text it wrote just before with OutputDebugStringW was printed
with the wrong parameter layout by the PE side ([exc] ml811: "C" and the ANSI copy read as UTF-16), and the termination unwinder stopped
at coreclr.dll's first frame ("<no module -- STOP>") because an image without a JIT-pool copy (MADEIRA_X64_IMAGE_NOCOPY) is in no pool
table. Now (unix side, nothing in the shipped PE ntdll changes):
  - build/ntdll-unix/thread_ios.c records each OutputDebugString raise's text address, length, process and thread (no read), and when
    the process ends on an unhandled exception prints the record's parameters ("[unhandled]") and that process's last texts, read
    fault-safe and decoded with the right layout ("[dbgprint-last]");
  - build/ntdll-unix/process_ios.c's unwinder finds such an image's PE header on a 64 KB boundary below pc (fault-safe, 1 GB at most).

Checks: both helpers compiled on the host with the real Mach read and driven (wide and ANSI texts, another process's texts left out, a
freed text skipped, the ring keeping the last 16; an image found and bounded by its SizeOfImage, nothing without a header); the call
sites (the record before anything else, the dump only on the unhandled path, the unwinder's fallback only when the pool tables miss).
"""
from pathlib import Path
import re, shutil, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


th = (root / 'build/ntdll-unix/thread_ios.c').read_text()
pr = (root / 'build/ntdll-unix/process_ios.c').read_text()
ring = th[th.index('#define IOS_DBGPRINT_RING 16'):th.index('#endif\n\nNTSTATUS WINAPI NtRaiseException(')]
img = pr[pr.index('static uint64_t ios_unw_image_below( uint64_t pc, unsigned long long *size_out )'):]
img = img[:img.index('\n}\n') + 3]
unw_read = pr[pr.index('static int ios_unw_read( uint64_t addr, void *buf, unsigned int len )'):]
_rd32 = '\nstatic int ios_unw_rd32( uint64_t a, unsigned int *v ) { return ios_unw_read( a, v, 4 ); }\n'
unw_read = unw_read[:unw_read.index(_rd32) + len(_rd32)]

raise_fn = th[th.index('NTSTATUS WINAPI NtRaiseException( EXCEPTION_RECORD *rec, CONTEXT *context, BOOL first_chance )'):]
raise_fn = raise_fn[:raise_fn.index('\n}\n')]
check('every OutputDebugString raise is recorded first (address only), before the dispatch',
      raise_fn.index('ios_dbgprint_keep( rec );') < raise_fn.index('send_debug_event(')
      and 'rec->ExceptionCode == DBG_PRINTEXCEPTION_WIDE_C || rec->ExceptionCode == DBG_PRINTEXCEPTION_C' in raise_fn[:raise_fn.index('ios_dbgprint_keep( rec );')])
check('the parameters and the last texts are printed only on the unhandled path, right before the process ends',
      raise_fn.count('ios_dbgprint_dump();') == 1 and raise_fn.index('ERR_(seh)("Unhandled exception code %x flags %x addr %p\\n"') < raise_fn.index('ios_dbgprint_dump();')
      < raise_fn.index('NtTerminateProcess( NtCurrentProcess(), rec->ExceptionCode );')
      and raise_fn.index('[dbgprint] swallowing') < raise_fn.index('ios_dbgprint_dump();'))
dump_fn = pr[pr.index('static void ios_unwind_dump( uint64_t rip, const uint64_t *gregs, const char *tag )'):]
check('the unwinder falls back to the image below pc only when the pool tables do not know pc',
      'if (!mbase) mbase = ios_unw_image_below( pc, &msz );' in dump_fn
      and dump_fn.index('ios_jit_module_base_for_va( pc, &msz );') < dump_fn.index('ios_unw_image_below( pc, &msz )'))
check('the helpers read only through mach_vm_read_overwrite (no raw dereference of a guest address)',
      'mach_vm_read_overwrite(' in ring and not re.search(r'\*\s*\(\s*(?:const\s+)?(?:unsigned\s+)?(?:char|short|int|uint\d+_t|WCHAR)\s*\*\s*\)\s*e\.text', ring)
      and 'ios_unw_read(' in img)

HARNESS = r'''
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
typedef unsigned long ULONG_PTR; typedef long LONG; typedef unsigned int DWORD;
#define DBG_PRINTEXCEPTION_C 0x40010006
#define DBG_PRINTEXCEPTION_WIDE_C 0x4001000a
typedef struct { DWORD ExceptionCode, ExceptionFlags; void *rec; void *ExceptionAddress; DWORD NumberParameters; ULONG_PTR ExceptionInformation[15]; } EXCEPTION_RECORD;
typedef struct { void *UniqueProcess, *UniqueThread; } CLIENT_ID;
typedef struct { void *Peb; CLIENT_ID ClientId; } TEB;
static TEB g_teb;
static TEB *NtCurrentTeb(void) { return &g_teb; }
#define HandleToULong(h) ((unsigned int)(uintptr_t)(h))
static LONG InterlockedIncrement( LONG *p ) { return __atomic_add_fetch( p, 1, __ATOMIC_SEQ_CST ); }
@RING@
@UNWREAD@
@IMG@
static void raise_w( const wchar_t *unused, const unsigned short *w, unsigned len ) {
  EXCEPTION_RECORD r; memset( &r, 0, sizeof r ); r.ExceptionCode = DBG_PRINTEXCEPTION_WIDE_C; r.NumberParameters = 4;
  r.ExceptionInformation[0] = len; r.ExceptionInformation[1] = (ULONG_PTR)w; ios_dbgprint_keep( &r );
}
static void raise_a( const char *s ) {
  EXCEPTION_RECORD r; memset( &r, 0, sizeof r ); r.ExceptionCode = DBG_PRINTEXCEPTION_C; r.NumberParameters = 2;
  r.ExceptionInformation[0] = strlen( s ) + 1; r.ExceptionInformation[1] = (ULONG_PTR)s; ios_dbgprint_keep( &r );
}
static unsigned short g_w[64];
int main( int argc, char **argv ) {
  const char *msg = "C:\\Program Files (x86)\\Steam\\coreclr failed\n";
  unsigned i;
  for (i = 0; msg[i]; i++) g_w[i] = (unsigned char)msg[i];
  g_w[i] = 0;
  g_teb.Peb = (void *)0x1000; g_teb.ClientId.UniqueThread = (void *)0xb0;
  if (argc > 1 && !strcmp( argv[1], "ring" )) {
    raise_a( "first ansi line" );
    g_teb.Peb = (void *)0x2000; raise_a( "another process" ); g_teb.Peb = (void *)0x1000;
    raise_w( 0, g_w, i + 1 );
    { EXCEPTION_RECORD r; memset( &r, 0, sizeof r ); r.ExceptionCode = DBG_PRINTEXCEPTION_C; r.NumberParameters = 2;
      r.ExceptionInformation[0] = 20; r.ExceptionInformation[1] = 0x10; ios_dbgprint_keep( &r ); }   /* unreadable: skipped */
    ios_dbgprint_dump();
    return 0;
  }
  if (argc > 1 && !strcmp( argv[1], "wrap" )) {
    static char texts[20][16];
    for (i = 0; i < 20; i++) { snprintf( texts[i], 16, "line %u", i ); raise_a( texts[i] ); }
    ios_dbgprint_dump();
    return 0;
  }
  if (argc > 1 && !strcmp( argv[1], "image" )) {
    unsigned char *m = mmap( NULL, 8 << 20, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0 );
    uint64_t base = ((uint64_t)m + 0x1ffff) & ~0xffffULL;
    unsigned long long sz = 0;
    printf( "none %llx\n", (unsigned long long)ios_unw_image_below( base + 0x30000, &sz ) );
    unsigned char *b = (unsigned char *)base;
    b[0] = 'M'; b[1] = 'Z'; *(uint32_t *)(b + 0x3c) = 0x80; *(uint32_t *)(b + 0x80) = 0x4550; *(uint32_t *)(b + 0x80 + 0x50) = 0x200000;
    printf( "inside %d %llx\n", ios_unw_image_below( base + 0x12345, &sz ) == base, sz );
    printf( "far %d\n", ios_unw_image_below( base + 0x123456, &sz ) == base );
    printf( "beyond %llx\n", (unsigned long long)ios_unw_image_below( base + 0x200010, &sz ) );
    return 0;
  }
  return 2;
}
'''
cc = shutil.which('cc')
if cc:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        src = HARNESS.replace('@RING@', ring.replace('dprintf( 2, ', 'printf( ')).replace('@UNWREAD@', unw_read).replace('@IMG@', img)
        (t / 'h.c').write_text(src)
        b = subprocess.run([cc, '-std=gnu11', '-w', '-o', str(t / 'h'), str(t / 'h.c')], capture_output=True, text=True)
        check('the ring and the image search compile on the host (real mach_vm_read_overwrite)', b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            out = subprocess.run([str(t / 'h'), 'ring'], capture_output=True, text=True).stdout.splitlines()
            check('at the end: this process\'s texts in order, the wide one decoded with the right layout, a newline as a space; '
                  'another process\'s text and an unreadable one left out',
                  out == ['[dbgprint-last] madeira-bcd #1 tid=00b0 OutputDebugStringA: first ansi line',
                          '[dbgprint-last] madeira-bcd #2 tid=00b0 OutputDebugStringW: C:\\Program Files (x86)\\Steam\\coreclr failed '], out)
            out = subprocess.run([str(t / 'h'), 'wrap'], capture_output=True, text=True).stdout.splitlines()
            check('the ring keeps the last 16 texts', len(out) == 16 and out[0].endswith('line 4') and out[-1].endswith('line 19'), out[:3])
            out = dict(l.split(' ', 1) for l in subprocess.run([str(t / 'h'), 'image'], capture_output=True, text=True).stdout.splitlines())
            check('the image below pc is found and bounded by its SizeOfImage; without a header, or past the image, nothing',
                  out.get('none') == '0' and out.get('inside') == '1 200000' and out.get('far') == '1' and out.get('beyond') == '0', out)

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-clr-death-diag.py' in wf)
print('PASS: CLR death diagnostics' if ok else 'FAIL: CLR death diagnostics')
raise SystemExit(0 if ok else 1)
