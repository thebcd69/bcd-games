#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX (32-bit guests) translates 0x82, the 32-bit-only alias of 0x80 (round 624); no FEX build runs here.

Ubisoft Connect (upc.exe, P4KC3V, W749H9, ZWB3JT, build 623), South Park: The Fractured But Whole (S78B5M) and Assassin's Creed 2
(AEKXPC): the protection layer's TLS callback runs `82 /r ib`; FEX's SecondaryALUOp had cases for 0x80, 0x81 and 0x83 only, so the
ADD/OR/AND/SUB/XOR forms of 0x82 failed to decode ("Unknown ALU Op: 0x10/0x11/0x14/0x15/0x16"), the first one executed raised
c000001d and the program ended itself with 0xdeadc0de. tools/patch-fex-ios-op82-alias.py adds the five labels in front of the 0x83
ones (same IR op, same atomic op, same ALUOp call); tools/build-xtajit-wow64.sh applies it to the 32-bit module only.

What this checks:
  1. On a copy of the pinned FEX file (FEX/ of this checkout, $BCD_FEX_SRC or ~/madeira-bcd/FEX): the patch adds exactly the five
     labels, each in front of its 0x83 twin, whose OPD values are the messages' 0x10, 0x11, 0x14, 0x15, 0x16; no label repeats; a
     second run changes nothing; a moved anchor fails by name and changes nothing.
  2. The decode tables agree: the 32-bit slot of 0x82 dispatches to SecondaryALUOp for exactly those five registers (ADC, SBB, CMP
     have their own handlers), the 64-bit slot is TYPE_INVALID for all eight, OpToIndex(0x82) is 2, and 0x82's 32-bit flags equal
     0x80's (8-bit, ModRM, lock, one immediate byte).
  3. The 32-bit build applies it without letting a drifted anchor cost the required patches, restores the file, and the 64-bit
     module's build never touches it.
  4. The device test tests/x86/op82-alias-x86.c (552 functions compare every 0x82 form with its 0x80 twin) compiles for i386 with
     kernel32 only, where the toolchain is at hand; the [int3-guest] block prints the code bytes around any guest trap (8 a session).
  5. Auto-tune names the old stop as fixed in build 624 (rule x86-op82-protection-exit, fixture), and the CI runs this check.
"""
from pathlib import Path
import importlib.util
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
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)[:1500]) if (detail and not cond) else ''))
    ok = ok and bool(cond)


script = root / 'tools/patch-fex-ios-op82-alias.py'
check('the patch script exists', script.is_file())
cands = ([Path(os.environ['BCD_FEX_SRC']).expanduser()] if os.environ.get('BCD_FEX_SRC') else []) + [root / 'FEX', Path('~/madeira-bcd/FEX').expanduser()]
fex = next((c for c in cands if (c / 'FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp').is_file()), None)
if fex is None:
    print("skip FEX's source is not at hand (set BCD_FEX_SRC to a checkout of the FEX fork)")
else:
    core = fex / 'FEXCore/Source/Interface/Core'
    orig = (core / 'OpcodeDispatcher.cpp').read_text()
    if 'OpToIndex(0x82)' in orig[orig.find('void OpDispatchBuilder::SecondaryALUOp('):]:
        print('note the FEX tree already carries the alias (patched, or fixed upstream): checking the patched function only')
    # ---------------------------------------------------------------- 1. the patch on a copy
    with tempfile.TemporaryDirectory() as td:
        t = Path(td) / 'OpcodeDispatcher.cpp'
        t.write_text(orig)
        r1 = subprocess.run([sys.executable, str(script), str(t)], capture_output=True, text=True)
        once = t.read_text()
        r2 = subprocess.run([sys.executable, str(script), str(t)], capture_output=True, text=True)
        check('it applies (or finds the alias already there) and a second run changes nothing',
              r1.returncode == 0 and r2.returncode == 0 and 'nothing to do' in r2.stdout and t.read_text() == once, r1.stdout + r1.stderr + r2.stdout)
        fn = once[once.index('void OpDispatchBuilder::SecondaryALUOp(OpcodeArgs) {'):]
        fn = fn[:fn.index('#undef OPD')]
        labels = re.findall(r'case OPD\(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex\(0x(8[0-3])\), (\d)\):', fn)
        regs82 = sorted(int(r) for op, r in labels if op == '82')
        check('the switch has the five 0x82 labels (ADD, OR, AND, SUB, XOR) and no label twice',
              regs82 == [0, 1, 4, 5, 6] and len(labels) == len(set(labels)), labels)
        check("their values are the logs' messages 0x10, 0x11, 0x14, 0x15, 0x16 (OPD = OpToIndex << 3 | reg, group 1)",
              sorted((2 << 3) | r for r in regs82) == [0x10, 0x11, 0x14, 0x15, 0x16])
        for r in (0, 1, 4, 5, 6):
            a = 'case OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x82), %d):\n' % r
            b = 'case OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x83), %d):\n' % r
            check('reg %d: the 0x82 label sits right before its 0x83 twin (the same IR op and atomic op)' % r,
                  a in fn and b in fn and fn.index(b) - fn.index(a) == len(a) + 2)
        check('nothing else of the function changes (the default still logs and fails the decode)',
              'LogMan::Msg::EFmt("Unknown ALU Op: 0x{:x}", Op->OP);' in fn and 'ALUOp(Op, IROp, AtomicIROp, 1);' in once
              and len(once) - len(orig) in (0, sum(len('  case OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x82), %d):\n' % r) for r in (0, 1, 4, 5, 6))
                                            + len("  // madeira-bcd: 0x82 is 0x80's 32-bit-only alias (tools/patch-fex-ios-op82-alias.py)\n")))
        bad = Path(td) / 'bad.cpp'
        if 'OpToIndex(0x82)' not in orig[orig.find('void OpDispatchBuilder::SecondaryALUOp('):]:
            bad.write_text(orig.replace('OpToIndex(0x83), 5):', 'OpToIndex(0x83), 5) :', 1))
            before = bad.read_text()
            r3 = subprocess.run([sys.executable, str(script), str(bad)], capture_output=True, text=True)
            check('a moved anchor fails by name and changes nothing',
                  r3.returncode != 0 and 'anchor for reg 5 not found once (FEX changed)' in (r3.stdout + r3.stderr) and bad.read_text() == before,
                  r3.stdout + r3.stderr)
    # ---------------------------------------------------------------- 2. the decode tables
    pg = (core / 'X86Tables/PrimaryGroupTables.cpp').read_text()
    lut = pg[pg.index('PrimaryGroup_ArchSelect_LUT = {{'):]
    lut = lut[:lut.index('}};')]
    entries = re.findall(r'\{\s*\{"(\w+)",\s*TYPE_INST,\s*([^,]+?),\s*(\d+),\s*\{ \.OpDispatch = ([^}]+?) \}\},\s*\{"", (TYPE_\w+),', lut)
    check('the ArchSelect table has the eight 0x82 entries (ADD OR ADC SBB AND SUB XOR CMP)', [e[0] for e in entries] ==
          ['ADD', 'OR', 'ADC', 'SBB', 'AND', 'SUB', 'XOR', 'CMP'], [e[0] for e in entries])
    check('its 32-bit slots send exactly ADD, OR, AND, SUB, XOR to SecondaryALUOp', sorted(i for i, e in enumerate(entries)
          if e[3].strip() == '&IR::OpDispatchBuilder::SecondaryALUOp') == [0, 1, 4, 5, 6])
    check('its 64-bit slots are all TYPE_INVALID (0x82 is invalid in 64-bit mode)', len(entries) == 8 and all(e[4] == 'TYPE_INVALID' for e in entries))
    check("the 32-bit slots carry 0x80's operand shape: 8-bit, ModRM, one immediate byte",
          all('GenFlagsSameSize(SIZE_8BIT)' in e[1] and 'FLAGS_MODRM' in e[1] and e[2] == '1' for e in entries))
    for r in range(8):
        check('0x82 reg %d is routed through that table' % r, re.search(
            r'\{OPD\(TYPE_GROUP_1, OpToIndex\(0x82\), %d\), 1, X86InstInfo\{"",\s*TYPE_ARCH_DISPATCHER, FLAGS_NONE, 0, \{ \.Indirect = '
            r'PrimaryGroup_ArchSelect_LUT\[ENTRY_1_82_%d\] \}\}\},' % (r, r), pg) is not None)
    x86h = (core / 'X86Tables/X86Tables.h').read_text()
    check('OpToIndex(0x82) is 2', 'case 0x82: return 2;' in x86h)
    print('  (FEX tree %s)' % fex)

# ---------------------------------------------------------------- 3. the builds
w = (root / 'tools/build-xtajit-wow64.sh').read_text()
check('the 32-bit module build applies it after the required patches, a drifted anchor only warns, and the file is restored',
      'OPDISPF="FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp"' in w
      and w.index('patch-fex-ios-wow-threads-ctor.py') < w.index('patch-fex-ios-op82-alias.py') < w.index('trap restore EXIT')
      and 'python3 "$R/tools/patch-fex-ios-op82-alias.py" "$R/FEX/$OPDISPF" || echo "::warning::the 0x82 alias patch was not applied' in w
      and re.search(r'restore\(\) \{ git -C "\$R/FEX" checkout -- [^}]*"\$OPDISPF"; \}', w) is not None)
x64 = root / 'tools/build-xtajit64.sh'
check('the 64-bit (ARM64EC) module build does not apply it', not x64.is_file() or 'op82' not in x64.read_text())

# ---------------------------------------------------------------- 4. the device test
prog = root / 'tests/x86/op82-alias-x86.c'
check('the device test exists and needs kernel32 only', prog.is_file() and '#include <windows.h>' in prog.read_text()
      and 'MADEIRA-X86-82: ok (' in prog.read_text())
cc = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/i686-w64-mingw32-clang'
if not cc.exists():
    cc = Path('~/madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/i686-w64-mingw32-clang').expanduser()
if cc.exists() and prog.is_file():
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / 'op82.exe'
        b = subprocess.run([str(cc), '-O2', '-Wall', '-Wextra', '-Werror', '-nostdlib', '-Wl,--entry=_start', '-o', str(exe), str(prog), '-lkernel32'],
                           capture_output=True, text=True)
        check('it compiles for i386 with -Wall -Wextra -Werror', b.returncode == 0, b.stderr)
        if b.returncode == 0:
            od = subprocess.run([str(cc.parent / 'i686-w64-mingw32-objdump'), '-p', str(exe)], capture_output=True, text=True).stdout
            dlls = re.findall(r'DLL Name: (\S+)', od)
            check('it imports KERNEL32.dll only', [d.lower() for d in dlls] == ['kernel32.dll'], dlls)
else:
    print('skip the device test compile: no i686 llvm-mingw at hand')

# ---------------------------------------------------------------- 4b. the trap's code bytes in every log
sig = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
blk = sig[sig.index('"[int3-guest] guest_rip=0x%llx x28=0x%llx read=%d rev=ml554'):]
blk = blk[:blk.index('/* Host x0..x30 too')]
check('the [int3-guest] block prints the 32 code bytes around the trap: a fault-safe Mach read, at most 8 lines a session, log only',
      'static int code_said;' in blk and 'code_said < 8 && grip >= 0x10010' in blk
      and 'mach_vm_read_overwrite( mach_task_self(), (mach_vm_address_t)(grip - 16), sizeof(cb),' in blk
      and '"[int3-guest]   CODE rip-16..rip+15: %s(r624)' in blk, blk[-1200:])

# ---------------------------------------------------------------- 5. auto-tune and CI
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rule = next((r for r in mod.RULES if r['id'] == 'x86-op82-protection-exit'), None)
check('auto-tune names the stop as fixed in build 624, with no line', rule is not None and rule['lines'] == [] and rule.get('fixedIn') == 624
      and 'Unknown ALU Op: 0x1' in json.dumps(rule['all']) and 'exit_code=0xdeadc0de' in json.dumps(rule.get('any', [])))
exp = root / 'tests/fixtures/autotune/ubisoft-connect-op82-2026-10-29.expect'
data = json.loads(exp.read_text()) if exp.is_file() else {}
check('its fixture (Ubisoft Connect P4KC3V) matches it', (data.get('matched') or [None])[0] == 'x86-op82-protection-exit', data)
check('the CI runs this check', 'python3 tests/host/check-fex-op82-alias.py' in (root / '.github/workflows/build-ipa.yml').read_text())

print('PASS: FEX 0x82 alias' if ok else 'FAIL: FEX 0x82 alias')
sys.exit(0 if ok else 1)
