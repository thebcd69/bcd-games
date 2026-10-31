#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""ILL diag: insn_stream: a SIGILL names the word that raised it (round 624, agent R S14; GTA IV 1WFP0J on an A13; log only).

1WFP0J died twice at the same place in FEX-generated code with `ill_handler ILL at pc=...`; the handler printed registers and the caller's
word but not the word at pc, so nothing said which instruction the A13 lacked (its FEX probe line: FLAGM2=0, FRINTTS=0, CSSC=0).
ill_handler (build/ntdll-unix/signal_arm64_ios.c) now prints PC-12..PC+8 with the faulting word in brackets, its top-level class and, for
an exactly known post-A13 encoding, the feature (build/ntdll-unix/ios_ill_insn_diag.h); the word alone when its neighbours cannot be read.

Checks: the classifier compiled on the host and driven over every listed encoding, its masks (register fields, sf/ftype/Q/sz forms) and
the classes; when llvm-mc is present, llvm-mc names the same mnemonic for every word of the table (guards the table against typos); the
source shape: one include, one print inside ill_handler's `if (ill_report)` after the caller_insn line, fault-safe reads only.
"""
from pathlib import Path
import os, re, shutil, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
hdr_path = root / 'build/ntdll-unix/ios_ill_insn_diag.h'
src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# word -> (expected note or None, mnemonic llvm-mc prints first)
TABLE = {
    0xd500403f: ('XAFLAG (FEAT_FlagM2)', 'xaflag'),
    0xd500405f: ('AXFLAG (FEAT_FlagM2)', 'axflag'),
    0xd500401f: ('CFINV (FEAT_FlagM)', 'cfinv'),
    0xd50330ff: ('SB (FEAT_SB)', 'sb'),
    0xd53b2405: ('MRS RNDR (FEAT_RNG)', 'mrs'),
    0xd53b2420: ('MRS RNDRRS (FEAT_RNG)', 'mrs'),
    0x1e284041: ('FRINT32Z scalar (FEAT_FRINTTS)', 'frint32z'),
    0x1e684000: ('FRINT32Z scalar (FEAT_FRINTTS)', 'frint32z'),
    0x1e28c000: ('FRINT32X scalar (FEAT_FRINTTS)', 'frint32x'),
    0x1e294000: ('FRINT64Z scalar (FEAT_FRINTTS)', 'frint64z'),
    0x1e69c000: ('FRINT64X scalar (FEAT_FRINTTS)', 'frint64x'),
    0x4e21e800: ('FRINT32Z vector (FEAT_FRINTTS)', 'frint32z'),
    0x0e21e800: ('FRINT32Z vector (FEAT_FRINTTS)', 'frint32z'),
    0x6e21e800: ('FRINT32X vector (FEAT_FRINTTS)', 'frint32x'),
    0x4e61f800: ('FRINT64Z vector (FEAT_FRINTTS)', 'frint64z'),
    0x6e21f800: ('FRINT64X vector (FEAT_FRINTTS)', 'frint64x'),
    0x5ac01c41: ('CNT (FEAT_CSSC)', 'cnt'),
    0xdac01c00: ('CNT (FEAT_CSSC)', 'cnt'),
    0x5ac01800: ('CTZ (FEAT_CSSC)', 'ctz'),
    0xdac02000: ('ABS (FEAT_CSSC)', 'abs'),
    0xd503201f: (None, 'nop'),
    0x4ea01c00: (None, 'mov'),
    0x0e61f800: (None, None),   # Q=0 with sz=1: unallocated, not a FRINT64Z
}
CLASSES = {0x00000000: 'all zero (UDF #0: an empty page)', 0xd503201f: 'branch, exception, system',
           0x4ea01c00: 'data processing (scalar FP and SIMD)', 0xf9400000: 'load/store', 0x91000000: 'data processing (immediate)',
           0x8b000000: 'data processing (register)', 0x04000000: 'SVE', 0x02000000: 'unallocated'}

cc = shutil.which(os.environ.get('CC', 'cc'))
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    words = sorted(set(TABLE) | set(CLASSES))
    (t / 'd.c').write_text('#include <stdio.h>\n#include "ios_ill_insn_diag.h"\nint main(void)\n{\n    static const uint32_t w[] = { '
                           + ', '.join('0x%08xu' % x for x in words) + ' };\n'
                           '    for (unsigned i = 0; i < sizeof(w) / sizeof(w[0]); i++)\n'
                           '    {\n        const char *n = ios_ill_insn_note( w[i] );\n'
                           '        printf( "%08x|%s|%s\\n", w[i], n ? n : "-", ios_ill_insn_class( w[i] ) );\n    }\n    return 0;\n}\n')
    r = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-I', str(hdr_path.parent), '-o', str(t / 'd'), str(t / 'd.c')],
                       capture_output=True, text=True)
    check('the classifier compiles on the host (-Wall -Wextra -Werror)', r.returncode == 0, r.stderr[-1500:])
    if r.returncode == 0:
        out = {}
        for line in subprocess.run([str(t / 'd')], capture_output=True, text=True).stdout.splitlines():
            w, n, c = line.split('|')
            out[int(w, 16)] = (None if n == '-' else n, c)
        bad = [('%08x' % w, out.get(w, ('?',))[0], want[0]) for w, want in TABLE.items() if out.get(w, ('?',))[0] != want[0]]
        check('every listed encoding gets its feature, with any registers, W/X, single/double, 2S/4S/2D; nop, a SIMD move and an '
              'unallocated FRINT-shaped word get none', not bad, bad)
        badc = [('%08x' % w, out.get(w, (None, '?'))[1], c) for w, c in CLASSES.items() if out.get(w, (None, '?'))[1] != c]
        check('the classes: zero page, system, SIMD, load/store, immediate, register, SVE, unallocated', not badc, badc)

mc = shutil.which('llvm-mc') or next((p for p in ('/usr/local/opt/llvm/bin/llvm-mc', '/opt/homebrew/opt/llvm/bin/llvm-mc') if Path(p).exists()), None)
if not mc and shutil.which('xcrun'):
    r = subprocess.run(['xcrun', '-f', 'llvm-mc'], capture_output=True, text=True)
    mc = r.stdout.strip() if r.returncode == 0 else None
if mc:
    wrong = []
    for w, (note, mnem) in TABLE.items():
        b = ' '.join('0x%02x' % x for x in w.to_bytes(4, 'little'))
        r = subprocess.run([mc, '--disassemble', '-triple=arm64', '-mattr=+all'], input=b, capture_output=True, text=True)
        text = [l.split() for l in r.stdout.splitlines() if l.strip() and not l.strip().startswith('.')]
        got = text[0][0] if text else None
        if mnem is None:
            if got is not None:
                wrong.append(('%08x' % w, got, 'invalid'))
        elif got != mnem or (note and note.startswith('MRS ') and note.split()[1] not in r.stdout):
            wrong.append(('%08x' % w, r.stdout.strip() or r.stderr.strip()[:60], mnem))
    check('llvm-mc names the same instruction for every word of the table', not wrong, wrong)
else:
    print('skip the llvm-mc cross-check: no llvm-mc')

ill = src[src.index('static void ill_handler( int signal, siginfo_t *siginfo, void *sigcontext )'):]
ill = ill[:ill.index('\n}\n')]
report = ill[ill.index('    if (ill_report)\n    {'):]
check('one include beside the guest diagnostics, one print in ill_handler',
      src.count('#include "ios_ill_insn_diag.h"') == 1 and src.index('#include "ios_ill_insn_diag.h"') < src.index('static void ill_handler(')
      and src.count('ILL diag: insn_stream PC-12..PC+8: %08x %08x %08x [%08x] %08x %08x | %s%s%s') == 2 and src.count('"ILL diag: insn=%08x | %s%s%s') == 2
      and ill.count('ios_ill_insn_note(') == 2)
blk = report[report.index('ERR("ILL diag: caller_insn @lr-4 = 0x%08x\\n", caller_insn);'):report.index('/* Walk back FP chain')]
check('inside `if (ill_report)` (rate-sampled like the other ILL lines), after the caller word, before the frame walk',
      'ios_ill_insn_note( w[3] )' in blk and 'ios_ill_insn_note( insn )' in blk)
check('fault-safe reads only (vm_read_overwrite), no raw dereference of pc; Wine\'s ERR only when Wine logging is safe',
      blk.count('vm_read_overwrite(') == 2 and not re.search(r'\*\s*\(\s*(?:const\s+)?(?:volatile\s+)?uint32_t\s*\*\s*\)\s*pc', blk)
      and blk.count('if (ill_dbg_ok)') == 2 and blk.count('dprintf( 2,') == 2)

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-ill-insn-diag.py' in wf)
print('PASS: ILL insn diag' if ok else 'FAIL: ILL insn diag')
raise SystemExit(0 if ok else 1)
