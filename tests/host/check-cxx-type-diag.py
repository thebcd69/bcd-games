#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[cxx-type]: the type name of a C++ exception as it is thrown (build 591, log only); no Wine runs.

Sekiro (ZGH9MT, licensed Dock copy; A7V9T2 on build 576 is the same site) dies 24 s after the launch: a C++ throw (code 0xE06D7363,
4 parameters) whose four handler frames decline, then __fastfail(7). The PE ntdll's line names the code and the parameters but not
the thrown type. thread_ios.c's NtRaiseException now reads, safely, ThrowInfo -> CatchableTypeArray -> CatchableType -> type_info and
prints the decorated names (the thrown type first, then what it converts to), with the exception parameters.

The production functions are cut out of thread_ios.c and compiled on the host against a constructed image: an x64 throw of std::runtime_error
(RVAs from the image base, name at type_info+16), a 32-bit throw in a WoW window (absolute guest pointers, name at +8), an unreadable ThrowInfo,
names with non-printable bytes, the same ThrowInfo thrown twice (reported once) and the cap of twelve throws. Textually: it is called from
NtRaiseException after the existing [cxx-throw] fix-up, for 3 or 4 parameters only, and reads only.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/thread_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


f0 = src.index('static int ios_cxx_read( ULONG_PTR addr, void *buf, SIZE_T len, int guest32 )')
f1 = src.index('\n}\n', src.index('static void ios_cxx_throw_diag')) + 3
fn = src[f0:f1]
nre = src[src.index('NTSTATUS WINAPI NtRaiseException('):]
nre = nre[:nre.index('\n}\n')]
check('it is called from NtRaiseException after the [cxx-throw] fix-up, for 3 or 4 parameters, before send_debug_event',
      nre.index('[cxx-throw] ok ThrowInfo') < nre.index('ios_cxx_throw_diag( rec );') < nre.index('status = send_debug_event(')
      and 'rec->ExceptionCode == 0xE06D7363 && (rec->NumberParameters == 4 || rec->NumberParameters == 3))\n        ios_cxx_throw_diag( rec );' in nre)
check('it reads and prints only', not [t for t in ('NtWriteVirtualMemory', 'NtProtectVirtualMemory', 'NtTerminate', 'write(', 'NtSet') if t in fn])
check('the only reader is NtReadVirtualMemory', fn.count('NtReadVirtualMemory') == 1)

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int NTSTATUS; typedef unsigned int ULONG; typedef unsigned long long SIZE_T, ULONG_PTR; typedef long LONG; typedef void *HANDLE;
typedef struct { ULONG ExceptionCode, ExceptionFlags; void *ExceptionRecord, *ExceptionAddress; ULONG NumberParameters; ULONG_PTR ExceptionInformation[15]; } EXCEPTION_RECORD;
#define NtCurrentProcess() ((HANDLE)(long long)-1)
#define InterlockedIncrement(p) (__sync_add_and_fetch((p), 1))
#define ERR_(ch) dbg
#define seh 0
static void dbg(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); }
static unsigned char *img; static size_t img_size; static ULONG_PTR wow_base;
static ULONG_PTR ios_wow_base(void) { return wow_base; }
static void *ios_wow_host_ptr(ULONG a) { return a ? (void *)(wow_base + a) : NULL; }
/* round 624 (X1): the 32-bit stack dump lives in process_ios.c; here it only says it was asked */
void ios_wow32_stack_dump(const char *tag) { printf("[stub] wow32 stack dump %s\n", tag); }
static NTSTATUS NtReadVirtualMemory(HANDLE p, const void *addr, void *buf, SIZE_T n, SIZE_T *got)
{
    (void)p;
    if ((const unsigned char *)addr < img || (const unsigned char *)addr + n > img + img_size) return 0xc0000005;
    memcpy(buf, addr, n); *got = n; return 0;
}
''' + fn + r'''
/* image layout (offsets from img): ThrowInfo of std::runtime_error at 0x1000; its type array at 0x1100; catchable types at 0x1200/0x1240;
 * type_infos at 0x1300 / 0x1400 (name at +16 for x64, +8 for 32-bit) */
static void put32(size_t off, uint32_t v) { memcpy(img + off, &v, 4); }
static void build64(size_t ti_off, size_t cta_off, size_t ct0, size_t ct1, size_t tinfo0, size_t tinfo1, const char *n0, const char *n1)
{
    put32(ti_off, 0); put32(ti_off + 4, 0x2000); put32(ti_off + 8, 0); put32(ti_off + 12, (uint32_t)cta_off);
    put32(cta_off, 2); put32(cta_off + 4, (uint32_t)ct0); put32(cta_off + 8, (uint32_t)ct1);
    put32(ct0, 0); put32(ct0 + 4, (uint32_t)tinfo0); put32(ct1, 0); put32(ct1 + 4, (uint32_t)tinfo1);
    strcpy((char *)img + tinfo0 + 16, n0); strcpy((char *)img + tinfo1 + 16, n1);
}
int main(void)
{
    EXCEPTION_RECORD rec; int i;
    img_size = 0x10000; img = calloc(1, img_size);

    /* x64 */
    build64(0x1000, 0x1100, 0x1200, 0x1240, 0x1300, 0x1400, ".?AVruntime_error@std@@", ".?AVexception@std@@");
    memset(&rec, 0, sizeof rec); rec.ExceptionCode = 0xE06D7363; rec.NumberParameters = 4; rec.ExceptionAddress = (void *)0x14023a6cc4;
    rec.ExceptionInformation[0] = 0x19930520; rec.ExceptionInformation[1] = 0x7000001234ull;
    rec.ExceptionInformation[2] = (ULONG_PTR)img + 0x1000; rec.ExceptionInformation[3] = (ULONG_PTR)img;
    printf("--- x64\n"); ios_cxx_throw_diag(&rec);
    printf("--- again\n"); ios_cxx_throw_diag(&rec);

    /* unreadable ThrowInfo */
    rec.ExceptionInformation[2] = 0x1234;
    printf("--- unreadable\n"); ios_cxx_throw_diag(&rec);

    /* a name with non-printable bytes and one that is empty */
    build64(0x2000, 0x2100, 0x2200, 0x2240, 0x2300, 0x2400, ".?AV\x01\x02odd@@", "");
    rec.ExceptionInformation[2] = (ULONG_PTR)img + 0x2000;
    printf("--- odd\n"); ios_cxx_throw_diag(&rec);

    /* round 624 (X1): a standard library exception's message: { vtable; const char *_What; bool _DoFree } at the thrown object */
    build64(0x9000, 0x9100, 0x9200, 0x9240, 0x9300, 0x9400, ".?AVlength_error@std@@", ".?AVlogic_error@std@@");
    { ULONG_PTR w = (ULONG_PTR)img + 0x9900; memcpy(img + 0x9800 + 8, &w, 8); strcpy((char *)img + 0x9900, "string too long\x01!"); }
    rec.ExceptionInformation[1] = (ULONG_PTR)img + 0x9800; rec.ExceptionInformation[2] = (ULONG_PTR)img + 0x9000;
    printf("--- what\n"); ios_cxx_throw_diag(&rec);

    /* 32-bit in a WoW window: absolute guest pointers (offsets from the window base), name at +8 */
    wow_base = (ULONG_PTR)img;
    put32(0x3000 + 0, 0); put32(0x3000 + 12, 0x3100);
    put32(0x3100, 1); put32(0x3104, 0x3200);
    put32(0x3200 + 4, 0x3300); strcpy((char *)img + 0x3300 + 8, ".?AVCMyError@@");
    memset(&rec, 0, sizeof rec); rec.ExceptionCode = 0xE06D7363; rec.NumberParameters = 3; rec.ExceptionAddress = (void *)0x4405a9;
    rec.ExceptionInformation[0] = 0x19930520; rec.ExceptionInformation[1] = 0x12ff00; rec.ExceptionInformation[2] = 0x3000;
    printf("--- 32-bit\n"); ios_cxx_throw_diag(&rec);

    /* the cap: twelve throws are reported, the rest are not */
    wow_base = 0;
    {
        int reported = 0;
        for (i = 0; i < 20; i++)
        {
            size_t o = 0x4000 + (size_t)i * 0x300;
            build64(o, o + 0x40, o + 0x80, o + 0xc0, o + 0x100, o + 0x180, ".?AVa@@", ".?AVb@@");
            memset(&rec, 0, sizeof rec); rec.ExceptionCode = 0xE06D7363; rec.NumberParameters = 4;
            rec.ExceptionInformation[2] = (ULONG_PTR)img + o; rec.ExceptionInformation[3] = (ULONG_PTR)img;
            ios_cxx_throw_diag(&rec);
        }
        (void)reported;
    }
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text('#include <stdarg.h>\n' + harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wno-implicit-function-declaration', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production functions compile on the host', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
        parts = {}
        cur = None
        for line in out.splitlines():
            if line.startswith('--- '):
                cur = line[4:]
                parts[cur] = []
            elif cur:
                parts[cur].append(line)
        x64 = '\n'.join(parts.get('x64', []))
        check('x64: the thrown type, what it converts to, the object, the count and the parameters',
              'thrown .?AVruntime_error@std@@; also catchable as: .?AVexception@std@@ (2 catchable type(s)' in x64 and '64-bit throw at 0x14023a6cc4' in x64
              and 'p0=0x19930520 p1=0x7000001234' in x64 and 'exception parameters (4)' in x64, x64)
        check('the same ThrowInfo thrown again prints nothing', parts.get('again') == [])
        wh = '\n'.join(parts.get('what', []))
        check('round 624 (X1): a std:: exception prints its message (printable ASCII only), and a 64-bit throw asks for no 32-bit stack',
              'thrown .?AVlength_error@std@@' in wh and '[cxx-type]   what="string too long?!"' in wh and '[stub]' not in wh
              and 'what=' not in x64, wh)
        un = '\n'.join(parts.get('unreadable', []))
        check('an unreadable ThrowInfo says so and still prints the parameters', '? (ThrowInfo or its type list not readable)' in un and 'exception parameters (4)' in un, un)
        odd = '\n'.join(parts.get('odd', []))
        check('non-printable bytes become ? and an empty name is a ?', 'thrown .?AV??odd@@; also catchable as: ?' in odd, odd)
        w32 = '\n'.join(parts.get('32-bit', []))
        check('32-bit in a WoW window: guest pointers resolved, name at +8, three parameters',
              '32-bit throw at 0x4405a9: thrown .?AVCMyError@@; also catchable as: nothing (1 catchable type(s)' in w32 and 'exception parameters (3)' in w32, w32)
        check('round 624 (X1): a 32-bit throw asks for the 32-bit stack dump once, after its two lines; the capped run asks for no more',
              parts.get('32-bit', [''] * 3)[2:3] == ['[stub] wow32 stack dump throw'] and out.count('[stub] wow32 stack dump') == 1, w32)
        cap = '\n'.join(parts.get('32-bit', [])[2:])
        check('at most twelve throws are reported (two lines each), 13 of 20 distinct ThrowInfos are left out',
              out.count('[cxx-type] build591') == 1 + 1 + 1 + 1 + 1 + (12 - 5), str(out.count('[cxx-type] build591')))

# madeira-bcd (lead, 2026-10-23): the HRESULT of an ATL or COM exception (the EA installer's C7-2F79), read from the thrown object, log only
_src = (Path(__file__).resolve().parents[2] / 'build/ntdll-unix/thread_ios.c').read_text()
check('an ATL::CAtlException prints its hr (first field), a _com_error its m_hresult (after the vtable)',
      'strstr( first, "CAtlException@ATL" ) && ios_cxx_read( obj, &hr, 4, guest32 )' in _src
      and 'strstr( first, "_com_error@@" ) && ios_cxx_read( obj + ptr, &hr, 4, guest32 )' in _src
      and '[cxx-type]   ATL::CAtlException hr=%#lx' in _src)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
