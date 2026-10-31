#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-msvcrt-initdiag.py: which step of msvcrt's DLL_PROCESS_ATTACH failed (round 624, agent E2 S13; log only).

Guacamelee Gold (0P65SX), Hitman: Contracts (KBBQJK) and Guacamelee STCE (DDFJM7, DNWRXA) end with '"msvcrt.dll" failed to initialize'.
Checks: the two new ERR lines sit right before the FALSE returns they explain and change nothing else (the patched text minus the lines and
the braces is the original); on the pinned Wine source (when checked out) it applies once, the arm64ec data-export patch still applies after
it, and the file compiles for i686; the workflow applies it before the i386 farm and puts the file back before the arm64ec extra DLLs.
"""
from pathlib import Path
import importlib.util
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location('initdiag', root / 'tools/patch-wine-msvcrt-initdiag.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
check('HeapCreate: the ERR line comes before the same return FALSE',
      'ERR("msvcrt: HeapCreate failed, error %lu\\n", GetLastError());' in p.HEAP_NEW
      and p.HEAP_NEW.index('HeapCreate failed') < p.HEAP_NEW.index('return FALSE;')
      and p.HEAP_NEW.replace('    if(!msvcrt_init_heap()) {\n', '    if(!msvcrt_init_heap())\n')
               .replace('        ERR("msvcrt: HeapCreate failed, error %lu\\n", GetLastError());   /* madeira-bcd: msvcrt init diag */\n', '')
               .replace('        return FALSE;\n    }\n', '        return FALSE;\n') == p.HEAP_OLD)
check('the "C" locale: the ERR line is the first line of the existing failure branch, which is otherwise unchanged',
      p.LOCALE_NEW.replace('        ERR("msvcrt: _create_locale(\\"C\\") failed, ACP %u OEMCP %u error %lu\\n", GetACP(), GetOEMCP(), GetLastError());'
                           '   /* madeira-bcd: msvcrt init diag */\n', '') == p.LOCALE_OLD)

wine = root / 'wine'
main_c = wine / 'dlls/msvcrt/main.c'
if main_c.is_file():
    r = subprocess.run(['git', '-C', str(wine), 'show', 'HEAD:dlls/msvcrt/main.c'], capture_output=True, text=True)
    committed = r.stdout if r.returncode == 0 and r.stdout else main_c.read_text()
    with tempfile.TemporaryDirectory() as t:
        w = Path(t) / 'wine'
        (w / 'dlls/msvcrt').mkdir(parents=True)
        (w / 'dlls/msvcrt/main.c').write_text(committed)
        tool = root / 'tools/patch-wine-msvcrt-initdiag.py'
        r1 = subprocess.run([sys.executable, str(tool), str(w)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(tool), str(w)], capture_output=True, text=True)
        check('applies once', r1.returncode == 0 and 'msvcrt init diag: applied' in r1.stdout and r2.returncode == 0
              and 'already applied' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        patched = (w / 'dlls/msvcrt/main.c').read_text()
        (Path(t) / 'ds.c').write_text(patched)
        ds = subprocess.run([sys.executable, str(root / 'tools/patch-wine-msvcrt-datasync.py'), str(Path(t) / 'ds.c')],
                            capture_output=True, text=True)
        check('the arm64ec data-export patch still applies on top (should the file ever reach it)', ds.returncode == 0 and 'patched' in ds.stdout,
              ds.stdout + ds.stderr)
        tc = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/i686-w64-mingw32-clang'
        if tc.exists():
            cc = subprocess.run([str(tc), '-fsyntax-only', '-O2', '-Wall', '-D__WINESRC__', '-D_CRTIMP=', '-D_MSVCR_VER=0',
                                 '-I' + str(wine / 'dlls/msvcrt'), '-I' + str(wine / 'build-macos/include'), '-I' + str(wine / 'include'),
                                 '-I' + str(wine / 'include/msvcrt'), str(w / 'dlls/msvcrt/main.c')], capture_output=True, text=True)
            check('the patched file compiles for i686 (-Wall)', cc.returncode == 0 and ' warning' not in cc.stderr, cc.stderr[-1500:])
        else:
            print('note: no llvm-mingw in this tree; the compile runs in the full tree')
else:
    print('SKIP: no wine sources (check out the submodule)')

wf = (root / '.github/workflows/build-ipa.yml').read_text()
apply_step = 'python3 tools/patch-wine-msvcrt-initdiag.py wine'
restore = 'dlls/kernelbase/debug.c dlls/msvcrt/main.c\n'
check('the workflow checks and applies it, and puts the file back', apply_step in wf and restore in wf
      and 'python3 tests/host/check-msvcrt-initdiag.py' in wf)
if apply_step in wf and restore in wf:
    farm = wf.index('- name: Build i386 farm (WoW64)')
    extra = wf.index('- name: Build extra Wine DLLs for arm64ec')
    check('applied before the i386 farm is built, put back before the arm64ec extra DLLs (msvcr* share the file)',
          wf.index(apply_step) < farm < wf.index(restore) < extra)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
