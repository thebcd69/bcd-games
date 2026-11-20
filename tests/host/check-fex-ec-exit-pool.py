#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Check tools/patch-fex-ios-ec-exit-pool.py: ExitFunctionECJitPool, the lean JIT exit with a hint for targets that are
already pool addresses (round 670, engine review I10; opt-in MADEIRA_FEX_EC_EXIT_POOL=1).

Needs the FEX submodule (or FEX_SRC) and a host C++ compiler; no Wine, FEX build or game.
1. The input is what the build gives the overlay: the pinned FEX with every overlay tools/build-xtajit64.sh applies before
   it. The overlay is idempotent, refuses a moved anchor and a partial overlay, and the script writes what patch() returns.
2. Module.S: ExitFunctionECJit and everything else is unchanged; the new stub's counting, CPU area and doorbell part is
   ExitFunctionECJit's word for word, its PE walk too; it reads x28 only at the IOS_FRAME_* offsets, writes no register but
   x4, x16, x17 and x23, and branches only inside itself, into ExitFunctionEC's tail or to the suspend point.
3. CoreState.h compiled on the host for iOS ARM64EC: IosAliasPoolHint is at 4104, right after IosAliasHint, every other
   field (x87_exact_int and x87_exact_valid included) keeps its offset and the frame its size; Module.cpp asserts the
   offset; without FEX_IOS_HOST and ARCHITECTURE_arm64ec the field does not exist.
4. Both stubs, ExitFunctionECJit and ExitFunctionECJitPool, run from the same states (unicorn where it is installed, else
   tests/host/a64mini.py): PE targets under every pair of hints, targets inside a pool copy (no hint, the right one, a
   wrong one, a retired one), targets in neither, the first byte of a neighbouring copy, aligned and misaligned stacks,
   exit-thunk returns, the doorbell: same stop, registers (x4 aside where ExitFunctionEC's tail leaves it), NZCV and memory
   apart from the pool hint; the pool hint is set by a pool target only and then skips the walks; the PE hint is never
   taken away; 130 calls are counted alike.
5. The frontend: IosEcExitLean becomes 2 only with the lean exit on and MADEIRA_FEX_EC_EXIT_POOL exactly "1"; ThreadInit
   installs ExitFunctionECJitPool for 2, ExitFunctionECJit for 1, ExitFunctionEC for 0, after the CPU area store and before
   it publishes the frame.
6. Wiring: the build applies it once after the other overlays and restores the files; the workflow refuses a module without
   its marker; the settings catalog documents the key (default off).
"""
from pathlib import Path
import importlib.util
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
fex = Path(os.environ.get('FEX_SRC') or (root / 'FEX'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


overlay = load('ec_exit_pool', root / 'tools/patch-fex-ios-ec-exit-pool.py')
CORE_STATE = 'FEXCore/include/FEXCore/Core/CoreState.h'
ASM = 'Source/Windows/ARM64EC/Module.S'
MODULE = 'Source/Windows/ARM64EC/Module.cpp'
assert set(overlay.EDITS) == {CORE_STATE, ASM, MODULE}
if not (fex / MODULE).exists():
    sys.exit('FAIL: the FEX submodule (or FEX_SRC) has no ' + MODULE)
TREES = ('FEXCore/Source', 'FEXCore/include', 'Source/Windows', 'External/rpmalloc/rpmalloc')

# 1. The overlay on what the build gives it.
build = (root / 'tools/build-xtajit64.sh').read_text()
pin_block_end = build.index('\nfi\n', build.index('if grep -q "ec-ret-direct-v1 madeira-bcd"')) + 4
after_pin = build[pin_block_end:build.index('\nbuild\n', pin_block_end)]
chain = [l.strip() for l in after_pin.splitlines() if l.strip().startswith('python3 "$R/tools/patch-fex-ios-')]
mine = 'python3 "$R/tools/patch-fex-ios-ec-exit-pool.py" "$R/FEX"'
assert chain.count(mine) == 1 and chain[-1] == mine, 'applied once, after every other ARM64EC overlay'
with tempfile.TemporaryDirectory() as tmp:
    tree = Path(tmp) / 'FEX'
    for rel in TREES:
        shutil.copytree(fex / rel, tree / rel)
    applied = []
    for line in chain[:-1]:
        cmd = line.split(' || ')[0]
        args = shlex.split(cmd.replace('$R/FEX', str(tree)).replace('$R', str(root)))
        r = subprocess.run([sys.executable] + args[1:], capture_output=True, text=True)
        assert r.returncode == 0 or ' || echo ' in line, (line, r.stderr)
        applied.append(Path(args[1]).name)
    before = {rel: (tree / rel).read_text() for rel in overlay.EDITS}
    for _ in range(2):
        r = subprocess.run([sys.executable, str(root / 'tools/patch-fex-ios-ec-exit-pool.py'), str(tree)], capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout.strip() == overlay.SUMMARY, r.stderr
    written = {rel: (tree / rel).read_text() for rel in overlay.EDITS}
patched = overlay.patch(before)
assert written == patched, 'the script writes what patch() returns, and a second run changes nothing'
assert all(patched[rel] != before[rel] for rel in overlay.EDITS)
assert overlay.patch(patched) == patched, 'not idempotent'
for rel, edits in overlay.EDITS.items():
    for old, new in edits:
        moved = dict(before)
        moved[rel] = moved[rel].replace(old, old.replace('\n', '\n ', 1) if '\n' in old else old + ' ', 1)
        try:
            overlay.patch(moved)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{rel}: moved anchor accepted')
    if len(edits) > 1:
        partial = dict(patched)
        partial[rel] = partial[rel].replace(edits[0][1], edits[0][0], 1)
        try:
            overlay.patch(partial)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{rel}: partial overlay accepted')
assert overlay.MARKER in patched[MODULE]

# 2. The stub's text.
asm, old_asm = patched[ASM], before[ASM]
assert asm.replace(overlay.STUB, '', 1).replace('#define IOS_FRAME_ALIAS_POOL_HINT 4104\n', '', 1) == old_asm, \
    'only the define and the new stub are added'
DEFINES = dict((m.group(1), int(m.group(2), 0)) for m in re.finditer(r'#define (IOS_FRAME_\w+) (\w+)', asm))
assert DEFINES['IOS_FRAME_ALIAS_POOL_HINT'] == DEFINES['IOS_FRAME_ALIAS_HINT'] + 8


def body(text, label):
    start = text.index(f'\n{label}:\n')
    end = min(i for i in (text.find('\n.global ', start + 1), text.find('#endif\n', start)) if i > 0)
    lines = [l.split('//')[0].strip() for l in text[start:end].split('\n')[2:]]
    return [l for l in lines if l]


jit, pool = body(asm, 'ExitFunctionECJit'), body(asm, 'ExitFunctionECJitPool')
door = 'cbnz w17, ExitFunctionSuspendPoint'
assert pool[:pool.index(door) + 1] == jit[:jit.index(door) + 1], 'counting, CPU area and doorbell as ExitFunctionECJit'
pe_walk = jit[jit.index('ios_ec_jit_xlate_walk:') + 1:]
pool_walk = pool[pool.index('ios_ec_pool_xlate_walk:') + 1:pool.index('ios_ec_pool_jit_walk:')]
assert [l.replace('ios_ec_jit_xlate_loop', 'L').replace('ios_ec_xlate_done', 'END') for l in pe_walk] == \
    [l.replace('ios_ec_pool_xlate_loop', 'L').replace('ios_ec_pool_jit_walk', 'END') for l in pool_walk], \
    'the PE walk is ExitFunctionECJit\'s (its end goes on to the pool walk)'
labels = {l[:-1] for l in pool if l.endswith(':')}
code = [l for l in pool if not l.endswith(':')]
assert not any(re.search(r'\b(fpcr|tpidrro_el0|x30|lr)\b', l, re.I) for l in code)
for l in code:
    op, _, args = l.partition(' ')
    regs = re.findall(r'\b([xw])(\d+)\b', args)
    if 'x28' in args:
        m = re.fullmatch(r'\w+ [xw]\d+, \[x28, #(IOS_FRAME_\w+)\]', l)
        assert m and m.group(1) in DEFINES, f'x28 used other than as the frame base: {l}'
    if op in ('ldr', 'add', 'sub', 'and', 'mov', 'ubfx', 'adrp') and regs:
        assert regs[0][1] in ('4', '16', '17', '23'), f'writes a register outside the clobber set: {l}'
    if op == 'ldadd':
        assert regs[1][1] in ('4', '16', '17', '23'), l
    if op.startswith('b') or op in ('cbz', 'cbnz'):
        target = args.split(',')[-1].strip()
        assert target in labels or target in ('2f', '3f', 'ios_ec_xlate_hit', 'ios_ec_xlate_done', 'ExitFunctionSuspendPoint'), l
writes = [l for l in code if l.startswith('str x16, [x28')]
assert writes == ['str x16, [x28, #IOS_FRAME_XP_COUNT]', 'str x16, [x28, #IOS_FRAME_ALIAS_HINT]',
                  'str x16, [x28, #IOS_FRAME_ALIAS_POOL_HINT]'], writes   # the frame words it writes, nothing else
assert pool.index('str x16, [x28, #IOS_FRAME_ALIAS_POOL_HINT]') > pool.index('ios_ec_pool_jit_loop:'), \
    'the pool hint is written by the pool walk only'

# 3. The frame layout, compiled on the host.
FIELDS = ('State', 'State.rip', 'SuspendDoorbell', 'Pointers', 'Pointers.ExitFunctionEC', 'IosEcCodeBitMap', 'IosCpuArea',
          'IosXpCallCount', 'IosAliasHint', 'x87_exact_int', 'x87_exact_valid')
layout = r'''
#include <FEXCore/Core/CoreState.h>
#include <cstdio>
using Frame = FEXCore::Core::CpuStateFrame;
int main() {
''' + ''.join(f'  std::printf("%zu\\n", offsetof(Frame, {f}));\n' for f in FIELDS) + r'''
  std::printf("%zu\n", sizeof(Frame));
#ifdef NEW_FIELD
  std::printf("%zu\n", offsetof(Frame, IosAliasPoolHint));
#endif
}
'''
results = {}
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    for label, text, extra in (('old', before[CORE_STATE], []), ('new', patched[CORE_STATE], ['-DNEW_FIELD'])):
        inc = tmp / label / 'FEXCore/Core'
        inc.mkdir(parents=True)
        (inc / 'CoreState.h').write_text(text)
        (tmp / f'{label}.cpp').write_text(layout)
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++20', '-w', '-DARCHITECTURE_arm64ec', '-DFEX_IOS_HOST=1', *extra,
                        '-I', str(tmp / label), '-I', str(fex / 'FEXCore/include'), '-I', str(fex / 'FEXHeaderUtils'),
                        '-I', str(fex / 'External/fmt/include'), str(tmp / f'{label}.cpp'), '-o', str(tmp / label / 'run')], check=True)
        results[label] = [int(x) for x in subprocess.run([str(tmp / label / 'run')], capture_output=True, text=True,
                                                         check=True).stdout.split()]
assert results['old'] == results['new'][:len(FIELDS) + 1], 'a field moved or the frame grew'
offsets = dict(zip(FIELDS, results['new']))
pool_hint = results['new'][-1]
assert pool_hint == offsets['IosAliasHint'] + 8 == DEFINES['IOS_FRAME_ALIAS_POOL_HINT'] == 4104, pool_hint
assert 'static_assert(offsetof(FEXCore::Core::CpuStateFrame, IosAliasPoolHint) == 4104, "IOS_FRAME_ALIAS_POOL_HINT in Module.S");' \
    in patched[MODULE]
ev = load('fexpatch_eval', root / 'tests/host/fexpatch_eval.py')
for macros in ({'FEX_IOS_HOST': 1}, {'ARCHITECTURE_arm64ec': 1}, {}):
    assert 'IosAliasPoolHint' not in ev.preprocess(ev.strip_comments(patched[CORE_STATE]), macros)

# 4. Both stubs from the same states.
emu = load('fexstub_emu', root / 'tests/host/fexstub_emu.py')
engine = None if emu.Uc is not None else 'mini'
TEXT, DATA, FRAME, CPUAREA, TEB, TSD, STACK, JIT, NATIVE = (emu.TEXT, emu.DATA, emu.FRAME, emu.CPUAREA, emu.TEB, emu.TSD,
                                                            emu.STACK, emu.JIT, emu.NATIVE)
if not shutil.which(os.environ.get('CLANG', 'clang')):
    sys.exit('FAIL: no clang to assemble Module.S for AArch64')
code, _, symbols, data_at = emu.link(emu.assemble(asm))
for name in ('IosXpFex', 'IosAliasEntries', 'IosAliasCount', 'IosAliasLast', 'X64ReturnInstr'):
    assert name in data_at, name
D = DEFINES
ENTRIES = [(0x7ff000000000, JIT, 0x10000), (0x7ff100000000, JIT + 0x100000, 0x10000), (0x7ff200000000, JIT + 0x200000, 0x10000)]
POOL_HINT = D['IOS_FRAME_ALIAS_POOL_HINT']
emulated = 0


def entry_addr(i):
    return data_at['IosAliasEntries'] + 32 * i


def make_state(target, sp, entries=ENTRIES, pe_hint=None, pool_hint=None, doorbell=0, kind='entry'):
    mem = {DATA: bytearray(0x100000), FRAME: bytearray(0x2000), CPUAREA: bytearray(0x1000), TEB: bytearray(0x2000),
           TSD: bytearray(0x1000), STACK: bytearray(0x10000), JIT: bytearray(0x400000), NATIVE: bytearray(0x10000)}
    put = lambda base, off, fmt, value: struct.pack_into(fmt, mem[base], off, value)
    put(DATA, data_at['X64ReturnInstr'] - DATA, '<Q', 0x14000beef)
    for i, (pe, jit_base, size) in enumerate(entries):
        struct.pack_into('<QQQQ', mem[DATA], data_at['IosAliasEntries'] - DATA + 32 * i, pe, jit_base, size, 0)
    put(DATA, data_at['IosAliasCount'] - DATA, '<I', len(entries))
    if pe_hint is not None:
        put(FRAME, D['IOS_FRAME_ALIAS_HINT'], '<Q', entry_addr(pe_hint))
    if pool_hint is not None:
        put(FRAME, POOL_HINT, '<Q', entry_addr(pool_hint))
    put(FRAME, D['IOS_FRAME_CPU_AREA'], '<Q', CPUAREA)
    put(FRAME, D['IOS_FRAME_SUSPEND_DOORBELL'], '<I', doorbell)
    put(CPUAREA, 0, '<B', 1)
    put(CPUAREA, 0x20, '<Q', FRAME + D['IOS_FRAME_SUSPEND_DOORBELL'])
    put(TEB, 0x1788, '<Q', CPUAREA)
    put(TSD, 0x898, '<Q', TEB)
    put(STACK, sp - STACK, '<Q', 0x140001234)
    native = next((jb + target - pe for pe, jb, size in entries if size and pe <= target < pe + size), target)
    base = NATIVE if native >= NATIVE else JIT
    word = 0xd63f0200 if kind == 'exit-thunk' else 0x800
    struct.pack_into('<I', mem[base], native - 4 - base, word)
    regs = [0x5000 + r for r in range(31)]
    regs[28], regs[9] = FRAME, target
    stop = native if kind == 'exit-thunk' else native + 0x800
    return {'mem': mem, 'regs': regs, 'sp': sp}, {stop, symbols['ExitFunctionSuspendPoint']}


def run(entry, state, stops):
    return emu.run(code, b'', entry, state, stops, engine=engine)


def frame_word(result, off):
    return struct.unpack_from('<Q', result[3][FRAME], off)[0]


def compare(target, sp, **kw):
    """ExitFunctionECJit and ExitFunctionECJitPool from one state: the same end but for the pool hint (and x4 where the tail
    leaves it). Returns (lean, pool) results."""
    global emulated
    state, stops = make_state(target, sp, **kw)
    a = run(symbols['ExitFunctionECJit'], state, stops)
    b = run(symbols['ExitFunctionECJitPool'], state, stops)
    pc_a, regs_a, nzcv_a, mem_a, _ = a
    pc_b, regs_b, nzcv_b, mem_b, _ = b
    assert pc_a == pc_b, (hex(pc_a), hex(pc_b))
    skip = {4} if kw.get('kind') == 'exit-thunk' else set()
    if pc_a == symbols['ExitFunctionSuspendPoint']:
        skip = {4, 16, 17, 23}
    else:
        assert nzcv_a == nzcv_b
    assert [r for i, r in enumerate(regs_a) if i not in skip] == [r for i, r in enumerate(regs_b) if i not in skip], \
        [(i, hex(x), hex(y)) for i, (x, y) in enumerate(zip(regs_a, regs_b)) if x != y and i not in skip]
    for base in mem_a:
        x, y = bytearray(mem_a[base]), bytearray(mem_b[base])
        if base == FRAME:
            x[POOL_HINT:POOL_HINT + 8] = y[POOL_HINT:POOL_HINT + 8] = bytes(8)
        assert x == y, f'memory at {base:#x} differs'
    assert mem_b[CPUAREA][0] == 0, 'InSimulation must be cleared'
    emulated += 1
    return a, b


ALIGNED, MISALIGNED = STACK + 0x8008, STACK + 0x8000
HINTS = [None, 0, 1, 2]
# PE targets: the pool stub translates exactly as ExitFunctionECJit, whatever the hints, and never writes the pool hint.
for target in (0x7ff000001000, 0x7ff100004560, 0x7ff20000fff0):
    hit = next(i for i, (pe, jb, size) in enumerate(ENTRIES) if pe <= target < pe + size)
    for sp in (ALIGNED, MISALIGNED):
        for pe_hint in HINTS:
            for pool_hint in HINTS:
                a, b = compare(target, sp, pe_hint=pe_hint, pool_hint=pool_hint)
                assert b[1][9] == ENTRIES[hit][1] + target - ENTRIES[hit][0], 'translated target'
                assert frame_word(b, D['IOS_FRAME_ALIAS_HINT']) == entry_addr(hit), 'the PE hit is recorded'
                assert frame_word(b, POOL_HINT) == (0 if pool_hint is None else entry_addr(pool_hint)), 'pool hint untouched'
        compare(target, sp, kind='exit-thunk', pe_hint=1, pool_hint=0)
# Targets already inside a pool copy: no translation; the pool walk records the entry; with it the walks are skipped.
for i, (pe, jb, size) in enumerate(ENTRIES):
    for off in (0x40, 0x804, size - 0x10):   # (the word before the target is read: not the first byte of the region)
        target = jb + off
        for sp in (ALIGNED, MISALIGNED):
            for pe_hint in HINTS:
                a, b = compare(target, sp, pe_hint=pe_hint)
                assert b[1][9] == target, 'a pool address is not translated'
                assert frame_word(b, POOL_HINT) == entry_addr(i), 'the pool walk records the entry'
                assert frame_word(b, D['IOS_FRAME_ALIAS_HINT']) == (0 if pe_hint is None else entry_addr(pe_hint)), \
                    'the PE hint is never taken away'
                for pool_hint in HINTS[1:]:
                    a2, b2 = compare(target, sp, pe_hint=pe_hint, pool_hint=pool_hint)
                    assert frame_word(b2, POOL_HINT) == entry_addr(i)
                    if pool_hint == i:
                        assert b2[4] < a2[4] and b2[4] < b[4], ('the pool hint skips the walks', a2[4], b[4], b2[4])
            compare(target, sp, kind='exit-thunk', pool_hint=i)
# A retired entry under the pool hint misses; the walk finds the live copy.
retired = [(0x7ff000000000, JIT, 0), ENTRIES[1], ENTRIES[2]]
a, b = compare(JIT + 0x100040, ALIGNED, entries=retired, pool_hint=0)
assert frame_word(b, POOL_HINT) == entry_addr(1)
a, b = compare(JIT + 0x40, ALIGNED, entries=retired, pool_hint=0)
assert b[1][9] == JIT + 0x40 and frame_word(b, POOL_HINT) == entry_addr(0), 'a retired copy is no pool hit'
# The first byte past a copy belongs to the copy right after it.
adjacent = ENTRIES + [(0x7ff300000000, JIT + 0x110000, 0x10000)]
for hint in (None, 1):
    a, b = compare(JIT + 0x110000, ALIGNED, entries=adjacent, pool_hint=hint)
    assert frame_word(b, POOL_HINT) == entry_addr(3), hex(frame_word(b, POOL_HINT))
# The same with the neighbour registered first (older): the walk meets the copy that ENDS at the target before the one that
# starts there, so a walk that took the end as inside would pick the wrong entry.
older = [(0x7ff300000000, JIT + 0x110000, 0x10000)] + ENTRIES
for hint in (None, 2):
    a, b = compare(JIT + 0x110000, ALIGNED, entries=older, pool_hint=hint)
    assert frame_word(b, POOL_HINT) == entry_addr(0), hex(frame_word(b, POOL_HINT))
# A target in neither kind of range goes on unchanged, as before, and leaves the pool hint as it was.
for hint in (None, 2):
    a, b = compare(NATIVE + 0x1000, ALIGNED, pool_hint=hint)
    assert b[1][9] == NATIVE + 0x1000 and frame_word(b, POOL_HINT) == (0 if hint is None else entry_addr(2))
compare(NATIVE + 0x1000, ALIGNED, entries=[], kind='exit-thunk')
# The doorbell: the suspend point, the target untouched.
a, b = compare(JIT + 0x40, ALIGNED, doorbell=1, pool_hint=0)
assert b[0] == symbols['ExitFunctionSuspendPoint'] and b[1][9] == JIT + 0x40


# Counting: 130 calls, alike.
def calls(entry, n):
    state, stops = make_state(JIT + 0x100040, ALIGNED)
    mem = state['mem']
    for i in range(n):
        target = JIT + 0x100040 + 16 * i
        struct.pack_into('<I', mem[JIT], 0x100000 + 0x40 + 16 * i - 4, 0x800)
        regs = list(state['regs'])
        regs[9] = target
        _, _, _, mem, _ = emu.run(code, b'', entry, {'mem': mem, 'regs': regs, 'sp': ALIGNED}, {target + 0x800}, engine=engine)
        struct.pack_into('<Q', mem[STACK], ALIGNED - STACK, 0x140001234)
        mem[CPUAREA][0] = 1
    fex_counters = mem[DATA][data_at['IosXpFex'] - DATA:data_at['IosXpFex'] - DATA + 4496 * 8]
    return bytes(fex_counters), struct.unpack_from('<Q', mem[FRAME], D['IOS_FRAME_XP_COUNT'])[0]


assert calls(symbols['ExitFunctionECJit'], 130) == calls(symbols['ExitFunctionECJitPool'], 130)

# 5. The frontend.
module = patched[MODULE]
block = module[module.index('    const char* LeanOpt = getenv("MADEIRA_FEX_EC_EXIT_LEAN");'):]
block = block[:block.index('    LogMan::Msg::IFmt("FEX: ec-exit-pool-v1 madeira-bcd')]
block = block.replace('    LogMan::Msg::IFmt("FEX: ec-exit-lean-v1 madeira-bcd enabled={} afp={}", IosEcExitLean != 0, HostFeatures.SupportsAFP);\n', '')
init = module.index('NTSTATUS ProcessInit() {')
assert init < module.index('    const char* PoolOpt = getenv("MADEIRA_FEX_EC_EXIT_POOL");') < module.index('  CTX->InitCore();', init)
thread = module[module.index('NTSTATUS ThreadInit() {'):]
area = '  Thread->CurrentFrame->IosCpuArea = reinterpret_cast<uint64_t>(CPUArea.Area);\n'
pick = ('  Thread->CurrentFrame->Pointers.ExitFunctionEC = reinterpret_cast<uintptr_t>(IosEcExitLean == 2 ? &ExitFunctionECJitPool\n'
        '                                                                               : IosEcExitLean ? &ExitFunctionECJit : &ExitFunctionEC);\n')
assert thread.index(area) < thread.index(pick) < thread.index('  CPUArea.StateFrame() = Thread->CurrentFrame;')
uses = [l.strip() for l in ev.strip_comments(module).splitlines() if 'IosEcExitLean' in l]
assert uses == ['static uint32_t IosEcExitLean = 0;',
                'IosEcExitLean = !HostFeatures.SupportsAFP && !(LeanOpt && strcmp(LeanOpt, "0") == 0);',
                'LogMan::Msg::IFmt("FEX: ec-exit-lean-v1 madeira-bcd enabled={} afp={}", IosEcExitLean != 0, HostFeatures.SupportsAFP);',
                'if (IosEcExitLean && PoolOpt && strcmp(PoolOpt, "1") == 0) {', 'IosEcExitLean = 2;',
                'LogMan::Msg::IFmt("FEX: ec-exit-pool-v1 madeira-bcd enabled={}", IosEcExitLean == 2);',
                'Thread->CurrentFrame->Pointers.ExitFunctionEC = reinterpret_cast<uintptr_t>(IosEcExitLean == 2 ? &ExitFunctionECJitPool',
                ': IosEcExitLean ? &ExitFunctionECJit : &ExitFunctionEC);'], uses   # no other reader of the flag
switch = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
struct { bool SupportsAFP; } HostFeatures;
static uint32_t IosEcExitLean = 7;
int main(int argc, char** argv) {
  HostFeatures.SupportsAFP = argv[1][0] == '1';
''' + block + r'''
  std::printf("%u\n", IosEcExitLean);
}
'''
with tempfile.TemporaryDirectory() as tmp:
    src, exe = Path(tmp) / 'switch.cpp', Path(tmp) / 'switch'
    src.write_text(switch)
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++20', '-O1', '-Wall', '-Werror', str(src), '-o', str(exe)], check=True)
    for afp in ('0', '1'):
        for lean in (None, '0', '1'):
            for value in (None, '1', '0', '', '11', '2', ' 1'):
                env = {k: v for k, v in os.environ.items() if k not in ('MADEIRA_FEX_EC_EXIT_LEAN', 'MADEIRA_FEX_EC_EXIT_POOL')}
                if lean is not None:
                    env['MADEIRA_FEX_EC_EXIT_LEAN'] = lean
                if value is not None:
                    env['MADEIRA_FEX_EC_EXIT_POOL'] = value
                got = subprocess.run([str(exe), afp], env=env, capture_output=True, text=True, check=True).stdout.strip()
                lean_on = afp == '0' and lean != '0'
                expect = '0' if not lean_on else ('2' if value == '1' else '1')
                assert got == expect, (afp, lean, value, got)

# 6. Wiring.
restore = [line for line in build.splitlines() if line.startswith('git -C FEX checkout --')]
assert len(restore) == 1 and all(f' {rel}' in restore[0] for rel in overlay.EDITS), 'every patched file is restored'
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
assert "b'ec-exit-pool-v1 madeira-bcd' not in module" in workflow and 'python3 tests/host/check-fex-ec-exit-pool.py' in workflow
catalog = (root / 'build/tools/gen-config-catalog.py').read_text()
assert '"env.MADEIRA_FEX_EC_EXIT_POOL"' in catalog and '"tools/patch-fex-ios-ec-exit-pool.py"' in catalog
assert 'key: "env.MADEIRA_FEX_EC_EXIT_POOL"' in (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()

print(f'pool-target exit hint: overlay idempotent after {len(applied)} build overlays, drift refused; stub text, frame offset '
      f'4104 (no field moved) checked; {emulated} states run through ExitFunctionECJit and ExitFunctionECJitPool agree '
      f'({"unicorn" if engine is None else "a64mini"}), 130 calls counted alike; only with the lean exit and '
      f'MADEIRA_FEX_EC_EXIT_POOL=1')
