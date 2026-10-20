#!/usr/bin/env python3
"""Exercise the production optional-probe recovery; not an emulator test."""
from pathlib import Path
import argparse
import os
import re
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'wine/dlls/ntdll/signal_arm64ec.c').read_text()
start = source.index('static BOOL recover_arm64x_code_probe(')
helper = source[start:source.index('\n}', start) + 2]
program = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef int BOOL;
typedef uintptr_t ULONG_PTR;
#define TRUE 1
#define FALSE 0
#define STATUS_ACCESS_VIOLATION 0xc0000005u
typedef struct { uint32_t ExceptionCode, NumberParameters; uintptr_t ExceptionInformation[2]; } EXCEPTION_RECORD;
typedef struct { uint64_t Pc, Lr, X9, X10, X11, preserved[36]; } ARM64_NT_CONTEXT;
static void *p_ios_jit_reverse_translate_addr = (void *)1, *pBeginSimulation = (void *)1;
static struct { int InSimulation; } area;
static int have_area = 1;
#define get_arm64ec_cpu_area() (have_area ? &area : NULL)
#define arm64x_check_call_probe_begin ((void *)0x5635c)
#define arm64x_check_call_probe_end ((void *)0x563fc)
static void *xlate_ios_jit_rev(void *p)
{
    uintptr_t a = (uintptr_t)p;
    if (a >= 0x10000000 && a < 0x10060000) a -= 0x10000000;
    return (void *)a;
}
''' + helper + r'''
static EXCEPTION_RECORD av = {STATUS_ACCESS_VIOLATION, 2, {0, 0x12688f3d0}};
static ARM64_NT_CONTEXT initial(void)
{
    ARM64_NT_CONTEXT c;
    memset(&c, 0xa5, sizeof(c));
    c.Pc = 0x1005635c; c.Lr = 0x15483231c; c.X10 = 0x154833000;
    c.X11 = av.ExceptionInformation[1]; c.X9 = 0x77;
    return c;
}
static void rejected(EXCEPTION_RECORD rec, ARM64_NT_CONTEXT c)
{
    ARM64_NT_CONTEXT before = c;
    assert(!recover_arm64x_code_probe(&rec, &c));
    assert(!memcmp(&before, &c, sizeof(c)));
}
int main(void)
{
    ARM64_NT_CONTEXT c = initial(), expected = c;
    expected.Pc = c.Lr; expected.X9 = c.X11; expected.X11 = c.X10;
    assert(recover_arm64x_code_probe(&av, &c));
    assert(!memcmp(&c, &expected, sizeof(c)));
    /* Every aligned PC within the optional probe may fall back. Both PE
     * and RX aliases normalise, with no data/page-permission changes. */
    for (uintptr_t pc = 0x5635c; pc < 0x563fc; pc += 4)
    {
        c = initial(); c.Pc = pc;
        assert(recover_arm64x_code_probe(&av, &c));
        assert(c.Pc == expected.Pc && c.X9 == expected.X9 && c.X11 == expected.X11);
    }
    puts("PASS: probe resumes through the existing exit thunk, preserving all other context");
    uintptr_t unrelated[] = {0, 0x56358, 0x563fc, 0x56400, 0x71f3780000, 0x5635e};
    for (unsigned i=0; i<sizeof(unrelated)/sizeof(*unrelated); i++)
    { c = initial(); c.Pc = unrelated[i]; rejected(av, c); }
    EXCEPTION_RECORD rec = av;
    rec.ExceptionCode = 0x80000001; rejected(rec, initial()); /* guard fault */
    rec = av; rec.ExceptionCode = 0xc000001d; rejected(rec, initial());
    rec = av; rec.NumberParameters = 1; rejected(rec, initial());
    rec = av; rec.ExceptionInformation[0] = 1; rejected(rec, initial()); /* write */
    rec.ExceptionInformation[0] = 8; rejected(rec, initial()); /* execute */
    c = initial(); c.Lr = 0; rejected(av, c);
    c = initial(); c.Lr += 2; rejected(av, c);
    c = initial(); c.X10 = 0; rejected(av, c);
    c = initial(); c.X10 += 2; rejected(av, c);
    puts("PASS: unrelated native/guest PCs, non-read faults, bad continuations remain exceptions");
    area.InSimulation = 1; rejected(av, initial()); area.InSimulation = 0;
    have_area = 0; rejected(av, initial()); have_area = 1;
    pBeginSimulation = NULL; rejected(av, initial()); pBeginSimulation = (void *)1;
    p_ios_jit_reverse_translate_addr = NULL; rejected(av, initial());
    puts("PASS: emulated execution, pre-initialisation and non-iOS paths are excluded");
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 'probe.c'
    exe = Path(tmp) / 'probe'
    c.write_text(program)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime', action='store_true', help='also verify rebuilt ntdll probe/exit instructions')
if parser.parse_args().runtime:
    tools = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
    unstripped = root / 'wine/build-arm64ec/dlls/ntdll/arm64ec-windows/ntdll.dll'
    image = (root / 'app/Madeira/arm64ec-windows/ntdll.dll').read_bytes()
    symbols = subprocess.check_output([str(tools / 'llvm-nm'), '-n', str(unstripped)], text=True)
    pe, = struct.unpack_from('<I', image, 0x3c)
    image_base, = struct.unpack_from('<Q', image, pe + 24 + 24)
    section_count, = struct.unpack_from('<H', image, pe + 6)
    optional_size, = struct.unpack_from('<H', image, pe + 20)

    def address(name):
        match = re.search(r'^([0-9a-fA-F]+) T ' + name + '$', symbols, re.M)
        if not match:
            raise SystemExit('Missing native probe label: ' + name)
        return int(match[1], 16)

    def instructions(va, size):
        for i in range(section_count):
            at = pe + 24 + optional_size + i * 40
            _, rva, raw_size, raw = struct.unpack_from('<IIII', image, at + 8)
            offset = va - image_base - rva
            if 0 <= offset and offset + size <= raw_size:
                return image[raw + offset:raw + offset + size]
        raise SystemExit('Probe instruction range is outside the bundled ntdll')

    begin = address('arm64x_check_call_probe_begin')
    end = address('arm64x_check_call_probe_end')
    if end <= begin or begin % 4 or end % 4:
        raise SystemExit('Invalid native probe bounds')
    if instructions(begin, 4) != struct.pack('<I', 0xf9400170):
        raise SystemExit('Optional probe no longer begins with ldr x16,[x11]')
    # Recovery must match the real .Lexit contract, not a guessed symbol offset.
    if instructions(end, 12) != struct.pack('<III', 0xaa0b03e9, 0xaa0a03eb, 0xd65f03c0):
        raise SystemExit('Exit-thunk selection no longer matches native probe recovery')
    if b'[ec-probe-fallback]' not in image:
        raise SystemExit('Bundled ntdll is missing native probe recovery')
    print(f'PASS: rebuilt ntdll probe RVA {begin-image_base:#x}..{end-image_base:#x} and exit-thunk ABI')
