#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tests/host/a64mini.py, the AArch64 interpreter the FEX stub checks use where unicorn is missing (round 670): every
instruction form it runs, assembled by the host clang for AArch64 and run from a known state, against results worked out by
hand (registers, memory, NZCV, the branch taken). A word it does not know raises; it never guesses.

Needs a clang that targets AArch64 (Xcode's does); no FEX, Wine or game.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mini = load('a64mini', root / 'tests/host/a64mini.py')
emu = load('fexstub_emu', root / 'tests/host/fexstub_emu.py')
if not shutil.which(os.environ.get('CLANG', 'clang')):
    sys.exit('FAIL: no clang to assemble AArch64')

TEXT, DATA, MEM = emu.TEXT, emu.DATA, 0x70000000
M64 = (1 << 64) - 1
N, Z, C, V = 1 << 31, 1 << 30, 1 << 29, 1 << 28
failures = []


def program(body, data=''):
    asm = '.arch armv8.2-a\n.arch_extension lse\n.text\n.global start\nstart:\n' + body + '\n.global end\nend:\n  brk #1\n'
    if data:
        asm += '.data\n' + data + '\n'
    code, blob, symbols, data_at = emu.link(emu.assemble(asm))
    return code, blob, symbols, data_at


def run(body, regs=None, sp=MEM + 0x8000, nzcv=0, mem=None, data='', fpcr=0, tpidrro=0):
    code, blob, symbols, data_at = program(body, data)
    x = [0] * 31
    for r, v in (regs or {}).items():
        x[r] = v & M64
    memory = bytearray(0x10000)
    for off, raw in (mem or {}).items():
        memory[off:off + len(raw)] = raw
    regions = [(TEXT, bytearray(code) + bytearray(64)), (DATA, bytearray(blob) + bytearray(0x1000)), (MEM, memory)]
    pc, out, flags, fpcr_out, steps = mini.run(regions, symbols['start'], x, sp, {symbols['end']}, nzcv=nzcv, fpcr=fpcr,
                                               tpidrro=tpidrro)
    return {'x': out[:31], 'sp': out[31], 'nzcv': flags, 'mem': memory, 'fpcr': fpcr_out, 'symbols': symbols,
            'data_at': data_at, 'steps': steps}


def check(name, cond, detail=''):
    if not cond:
        failures.append(f'{name} {detail}')


def q(mem, off):
    return struct.unpack_from('<Q', mem, off)[0]


# Add/subtract (immediate) and flags.
r = run('subs x0, x1, #5', {1: 3})
check('subs x: 3-5', r['x'][0] == (-2) & M64 and r['nzcv'] == N, hex(r['nzcv']))
r = run('adds w2, w3, #1', {3: 0x7fffffff})
check('adds w: signed overflow', r['x'][2] == 0x80000000 and r['nzcv'] == N | V, hex(r['nzcv']))
r = run('adds x2, x3, #1', {3: M64})
check('adds x: carry, zero', r['x'][2] == 0 and r['nzcv'] == Z | C, hex(r['nzcv']))
r = run('cmp x1, #0x10, lsl #12', {1: 0x10000})
check('cmp imm lsl 12: equal', r['nzcv'] == Z | C)
r = run('add x4, sp, #16\n  mov x5, sp\n  sub sp, sp, #32', sp=MEM + 0x100)
check('add from sp, mov from sp, sub sp', r['x'][4] == MEM + 0x110 and r['x'][5] == MEM + 0x100 and r['sp'] == MEM + 0xe0)
r = run('sub w6, w7, #1', {7: 0x100000000})
check('sub w zero-extends', r['x'][6] == 0xffffffff)

# Add/subtract (shifted register).
r = run('add x0, x1, x2, lsl #6\n  sub w3, w1, w2, lsr #2\n  cmp x1, x2\n  b.hs end', {1: 0x1000, 2: 0x40})
check('add/sub shifted', r['x'][0] == 0x2000 and r['x'][3] == 0xff0 and r['nzcv'] == C)
r = run('cmp w23, w16', {23: 0x1d63f0200, 16: 0xd63f0200})
check('cmp w: equal low words', r['nzcv'] & Z)
r = run('subs x0, x1, x2, asr #1', {1: 0, 2: (-4) & M64})
check('subs asr', r['x'][0] == 2 and r['nzcv'] == 0, hex(r['nzcv']))

# Logical (immediate, shifted register).
r = run('and x0, x1, #0xff00\n  orr w2, wzr, #0x80000001\n  eor x3, x1, #0x5555555555555555\n  and x4, x1, #-4\n  and x5, x1, #4095',
        {1: 0x123456789abcdeff})
check('logical immediates', r['x'][0] == 0xde00 and r['x'][2] == 0x80000001 and r['x'][3] == 0x123456789abcdeff ^ 0x5555555555555555
      and r['x'][4] == 0x123456789abcdefc and r['x'][5] == 0xeff, [hex(v) for v in r['x'][:6]])
r = run('tst x1, #63', {1: 64})
check('tst imm: zero', r['nzcv'] == Z)
r = run('tst x1, #6', {1: 0x8000000000000002})
check('tst imm: not zero, N from the result', r['nzcv'] == 0)
r = run('ands x0, x1, #0x8000000000000000', {1: M64})
check('ands sets N', r['nzcv'] == N and r['x'][0] == 1 << 63)
r = run('mov x17, x9\n  mvn x0, x1\n  orr x2, x1, x3, lsl #8\n  bic x4, x1, x3\n  eon w5, w1, w3', {9: 0xabc, 1: 0xf0f0, 3: 0xff})
check('logical shifted', r['x'][17] == 0xabc and r['x'][0] == ~0xf0f0 & M64 and r['x'][2] == 0xfff0 and r['x'][4] == 0xf000
      and r['x'][5] == (0xf0f0 ^ 0xffffff00) & 0xffffffff, [hex(v) for v in r['x'][:6]])

# Move wide.
r = run('mov x0, #0x1234\n  movk x0, #0xabcd, lsl #16\n  mov x1, #-2\n  mov w2, #0x200\n  movk w2, #0xd63f, lsl #16\n  movz x3, #1, lsl #48\n'
        '  mov w4, #-1', {4: M64})
check('move wide', r['x'][0] == 0xabcd1234 and r['x'][1] == (-2) & M64 and r['x'][2] == 0xd63f0200 and r['x'][3] == 1 << 48
      and r['x'][4] == 0xffffffff, [hex(v) for v in r['x'][:5]])

# Bitfield.
r = run('ubfx x23, x1, #16, #4\n  sbfx x2, x1, #4, #8\n  lsl x3, x1, #3\n  lsr w4, w1, #1\n  asr x5, x6, #63\n  sxtw x7, w8\n  uxtb w9, w1',
        {1: 0x00000000000af0f0, 6: 1 << 63, 8: 0x80000000})
check('bitfield', r['x'][23] == 0xa and r['x'][2] == (0x0f - 0x100 + 0x100) and r['x'][3] == 0xaf0f0 << 3 and r['x'][4] == 0x57878
      and r['x'][5] == M64 and r['x'][7] == 0xffffffff80000000 and r['x'][9] == 0xf0, [hex(v) for v in r['x'][:10]])
r = run('sbfx x2, x1, #4, #8', {1: 0xf80})
check('sbfx negative', r['x'][2] == (-8) & M64, hex(r['x'][2]))

# Variable shifts.
r = run('lsl x0, x1, x2\n  lsr x3, x1, x2\n  asr x4, x5, x2\n  ror w6, w1, w2', {1: 0x80000001, 2: 68, 5: 1 << 63})
check('variable shifts (amount mod 64 / 32)', r['x'][0] == 0x800000010 and r['x'][3] == 0x8000000 and r['x'][4] == 0xf800000000000000
      and r['x'][6] == 0x18000000, [hex(v) for v in r['x'][:7]])

# Loads and stores.
r = run('str x1, [x10, #8]\n  ldr x0, [x10, #8]\n  ldr w2, [x10, #8]\n  strb w1, [x10, #1]\n  ldrb w3, [x10, #1]\n'
        '  ldursw x4, [x10, #-4]\n  ldr x5, [x10], #16\n  str x1, [x10, #-8]!\n  str x2, [x10, x11, lsl #3]\n  ldrsh x6, [x12]\n'
        '  ldrh w7, [x12]\n  strh w1, [x12, #2]\n  ldr w8, [x13, x14]\n  ldrsb w9, [x12, #3]',
        {1: 0x1122334455667788, 10: MEM + 0x100, 11: 3, 12: MEM + 0x200, 13: MEM + 0x300, 14: 4},
        mem={0xfc: struct.pack('<i', -7), 0x200: struct.pack('<H', 0x8001), 0x304: struct.pack('<I', 0xdeadbeef)})
m = r['mem']
check('ldr/str forms', r['x'][0] == 0x1122334455667788 and r['x'][2] == 0x55667788 and r['x'][3] == 0x88
      and r['x'][4] == (-7) & M64 and r['x'][5] == q(m, 0x100) and r['x'][10] == MEM + 0x108 and q(m, 0x108) == 0x1122334455667788
      and q(m, 0x108 + 24) == 0x55667788 and r['x'][6] == 0xffffffffffff8001 and r['x'][7] == 0x8001
      and struct.unpack_from('<H', m, 0x202)[0] == 0x7788 and r['x'][8] == 0xdeadbeef and r['x'][9] == 0x77,
      [hex(v) for v in r['x'][:11]])
r = run('ldr lr, [x4], #0x8\n  mov sp, x4', {4: MEM + 0x400}, mem={0x400: struct.pack('<Q', 0x140001234)})
check('ldr lr post-index, mov sp', r['x'][30] == 0x140001234 and r['x'][4] == MEM + 0x408 and r['sp'] == MEM + 0x408)
r = run('strb wzr, [x17, #0x0]\n  str xzr, [x17, #8]', {17: MEM + 0x500}, mem={0x500: b'\x01', 0x508: b'\xff' * 8})
check('zero register stores', r['mem'][0x500] == 0 and q(r['mem'], 0x508) == 0)

# LSE and ordered accesses.
r = run('ldadd x1, x0, [x10]\n  stadd x1, [x10]\n  ldset x2, x3, [x11]\n  ldclr x2, x4, [x12]\n  ldeor w2, w5, [x13]\n'
        '  ldar x6, [x10]\n  stlr x2, [x14]\n  ldaddal x1, x7, [x10]',
        {1: 5, 2: 0xf0, 10: MEM + 0x600, 11: MEM + 0x608, 12: MEM + 0x610, 13: MEM + 0x618, 14: MEM + 0x620},
        mem={0x600: struct.pack('<Q', 10), 0x608: struct.pack('<Q', 0x0f), 0x610: struct.pack('<Q', 0xff), 0x618: struct.pack('<I', 0xff)})
m = r['mem']
check('LSE fetch-and-op, LDAR/STLR', r['x'][0] == 10 and r['x'][6] == 20 and r['x'][7] == 20 and q(m, 0x600) == 25
      and r['x'][3] == 0x0f and q(m, 0x608) == 0xff and r['x'][4] == 0xff and q(m, 0x610) == 0x0f and r['x'][5] == 0xff
      and struct.unpack_from('<I', m, 0x618)[0] == 0x0f and q(m, 0x620) == 0xf0, [hex(v) for v in r['x'][:8]])

# Branches.
for cond, nzcv, taken in (('eq', Z, 1), ('ne', Z, 0), ('hs', C, 1), ('lo', C, 0), ('mi', N, 1), ('pl', N, 0), ('vs', V, 1),
                          ('vc', V, 0), ('hi', C, 1), ('hi', C | Z, 0), ('ls', C | Z, 1), ('ge', N | V, 1), ('lt', N, 1),
                          ('gt', 0, 1), ('gt', Z, 0), ('le', Z, 1), ('al', 0, 1)):
    r = run(f'mov x0, #1\n  b.{cond} 1f\n  mov x0, #2\n1:', nzcv=nzcv)
    check(f'b.{cond} with {nzcv:#x}', r['x'][0] == (1 if taken else 2) and r['nzcv'] == nzcv)
r = run('mov x0, #0\n  cbz w1, 1f\n  mov x0, #1\n1:\n  cbnz x2, 2f\n  add x0, x0, #10\n2:\n  tbz x3, #40, 3f\n  add x0, x0, #100\n3:\n'
        '  tbnz w4, #3, 4f\n  add x0, x0, #1000\n4:', {1: 1 << 32, 2: 0, 3: 1 << 40, 4: 8})
check('cbz/cbnz/tbz/tbnz', r['x'][0] == 110, r['x'][0])
r = run('bl 1f\n  b end\n1:\n  mov x5, x30\n  adr x6, 2f\n  br x6\n  brk #2\n2:\n  adr x7, 3f\n  blr x7\n  b end\n3:\n  ret')
check('bl, adr, br, blr, ret', r['x'][5] == r['symbols']['start'] + 4 and r['x'][30] == r['x'][7] - 4)

# System registers, hints and barriers.
r = run('mrs x17, fpcr\n  and x17, x17, #~6\n  msr fpcr, x17\n  mrs x1, tpidrro_el0\n  msr nzcv, x2\n  mrs x3, nzcv\n  nop\n  dmb ish\n  isb',
        {2: Z | 0x123}, fpcr=0x3000006, tpidrro=0x33000000)
check('mrs/msr, hints, barriers', r['fpcr'] == 0x3000000 and r['x'][1] == 0x33000000 and r['x'][3] == Z and r['nzcv'] == Z)

# PC-relative: adrp/add/ldr against a data symbol.
r = run('adrp x16, value\n  add x16, x16, #:lo12:value\n  adrp x17, value\n  ldr x17, [x17, #:lo12:value]\n  adrp x18, words\n'
        '  ldr w18, [x18, #:lo12:words]', data='.balign 8\nvalue: .quad 0x1122334455667788\nwords: .word 0x99')
check('adrp + lo12', r['x'][16] == r['data_at']['value'] and r['x'][17] == 0x1122334455667788 and r['x'][18] == 0x99)

# An unknown word raises (here a floating-point add), and a run without a stop ends.
for body, what in (('fadd d0, d1, d2', 'an FP instruction'), ('1:\n  b 1b', 'a loop with no stop')):
    try:
        run(body)
    except mini.Unsupported:
        pass
    else:
        failures.append(f'{what} did not raise')

if failures:
    print('FAIL: a64mini\n  ' + '\n  '.join(failures))
    sys.exit(1)
print('a64mini: every instruction form the FEX stub checks run gives the hand-computed result; unknown words raise')
