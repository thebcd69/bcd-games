#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Madeira Dock reads the published Steam values back while the game starts; two opt-in experiments (round 624, Steam 113).

The Rockstar Launcher's SteamAPI_Init found no running Steam in 39 of 187 Rockstar Dock sessions (error 113; 5T9WRM, AX6XNN) while
the Dock reported a clean launch. tools/patch-dock-steam-readback.py makes the host report what the Launcher would find: the
three published values read back (launch-discovery-readback), the wait loop's longest gap (launch-loop-gap-ms), the same 5 s after
the game runs; env.MADEIRA_DOCK_PUMP_MS and env.MADEIRA_DOCK_REASSERT are experiments, off unless set.

Checks:
  1. The three Dock patches apply in the workflow's order to a copy of madeira-dock/src/launch.c (this checkout's submodule or
     ~/madeira-bcd's), twice; a moved anchor fails by name and changes nothing.
  2. The patched source: the readback state is declared before the first `goto done`; the loop work is in the LAUNCH loop (the CEG
     loop keeps its own 50 ms sleep); both launch-loop sleeps go through dock_sleep_ms; readbacks after publishing and after the
     request; the writes are counted before launch-discovery-restored.
  3. The helpers compiled on the host against stand-ins for the registry and environment calls: the readback bits, the
     reassertion writes only a changed value of the ones the host published, the sleep is 50 ms without the key or with a bad
     value and the key's value only for 20 s after the request.
  4. The whole host compiles with build/madeira-dock/build.sh's flags (warnings are errors), where llvm-mingw is at hand.
  5. Wiring: the workflow applies the patch in the Dock build step (warning only on failure) and runs this check; the app logs the
     four new fields; the settings catalog knows both keys.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)[:1500]) if (detail and not cond) else ''))
    ok = ok and bool(cond)


script = root / 'tools/patch-dock-steam-readback.py'
chain = [root / 'tools/patch-dock-game-scm.py', root / 'tools/patch-dock-keep-alive.py', script]
dock = next((d for d in (root / 'madeira-dock', Path('~/madeira-bcd/madeira-dock').expanduser()) if (d / 'src/launch.c').is_file()), None)
if dock is None:
    print('skip the Dock source is not at hand (madeira-dock submodule)')
else:
    orig = (dock / 'src/launch.c').read_text()
    pre = 'madeira-bcd: launch-discovery-readback' in orig
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(dock / 'src', t / 'src')
        lc = t / 'src/launch.c'
        outs = []
        for rnd in (1, 2):
            for s in chain:
                r = subprocess.run([sys.executable, str(s), str(lc)], capture_output=True, text=True)
                outs.append((rnd, s.name, r.returncode, (r.stdout + r.stderr).strip()))
        check('the three Dock patches apply in the workflow order, and a second round says "already patched"',
              all(rc == 0 for _, _, rc, _ in outs) and all(o == 'already patched' for rnd, _, _, o in outs if rnd == 2)
              and (pre or [o for rnd, n, _, o in outs if rnd == 1 and n == script.name] == ['patched']), outs)
        p = lc.read_text()
        fn = p[p.index('int sh_launch(HMODULE module, void *engine, void *client_user,'):]
        fn = fn[:fn.index('\n#else\n')]
        decl = fn.index('uint64_t dock_last_turn = 0, dock_max_gap = 0, dock_last_readback = 0, dock_running_at = 0;')
        check('the readback state is declared before the first goto done of sh_launch', decl < fn.index('goto done;'))
        loop = fn[fn.index('    result = 44;\n    while (!InterlockedCompareExchange(&interrupted, 0, 0)) {\n'):fn.index('\ndone:\n')]
        check('the loop work sits at the top of the launch wait loop and reads back only until the game runs (at most 40 times)',
              loop.index('/* madeira-bcd: launch-discovery-readback, the loop gap and the experiments */') < loop.index('for (unsigned batch = 0;')
              and 'if (!seen_running && dock_readbacks < 40 && now - dock_last_readback >= 1000) {' in loop)
        check('both sleeps of the launch loop go through dock_sleep_ms; the CEG loop keeps its plain 50 ms sleep',
              loop.count('o->sleep_ms(dock_sleep_ms(o, begin));') == 2 and 'o->sleep_ms(50)' not in loop and p.count('o->sleep_ms(50);') == 1)
        check('read back after publishing and after the launch request; the writes are counted before launch-discovery-restored',
              'o->event("launch-client-discovery-published", 1);\n    o->event("launch-discovery-readback", dock_readback(values));' in p
              and 'o->event("launch-request-submitted", call != 0);\n    dock_last_readback = o->now_ms();\n    o->event("launch-discovery-readback", dock_readback(values));' in p
              and p.index('o->event("launch-discovery-reasserted", dock_reasserted);') < p.index('o->event("launch-discovery-restored", restored);'))
        check('the experiments do nothing unless set: the reassertion only with MADEIRA_DOCK_REASSERT = 1, before the game runs',
              'if (dock_reassert_on && !seen_running) dock_reasserted += dock_reassert(values);' in loop
              and "return GetEnvironmentVariableW(L\"MADEIRA_DOCK_REASSERT\", text, 4) == 1 && text[0] == L'1';" in p)
        # A moved anchor.
        bad = t / 'bad.c'
        bad.write_text(orig.replace('    result = 44;\n', '    result =  44;\n'))
        before = bad.read_text()
        r = subprocess.run([sys.executable, str(script), str(bad)], capture_output=True, text=True)
        check('a moved anchor fails by name and changes nothing', pre or (r.returncode != 0 and 'anchor not found exactly once (the top of the launch wait loop)'
              in r.stderr and bad.read_text() == before), r.stdout + r.stderr)
        # ---------------------------------------------------------------- 3. the helpers on the host
        helpers = p[p.index('/* madeira-bcd: launch-discovery-readback (round 624'):p.index('static volatile LONG interrupted;')]
        harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
typedef unsigned int DWORD; typedef long LSTATUS; typedef void *HKEY; typedef unsigned char BYTE;
#define NULL_KEY ((HKEY)0)
#define ERROR_SUCCESS 0
#define ERROR_FILE_NOT_FOUND 2
#define REG_DWORD 4
struct saved_value { HKEY key; const wchar_t *name; DWORD previous, written; bool existed, changed; };
struct sh_observer { uint64_t (*now_ms)(void); void (*sleep_ms)(uint32_t); void (*event)(const char *, int32_t); };
/* the registry: three values by name; present[i] = 0 makes a read fail */
static DWORD reg[3]; static int present[3] = {1, 1, 1}; static int writes;
static int slot(const wchar_t *n) { return !wcscmp(n, L"pid") ? 0 : !wcscmp(n, L"ActiveUser") ? 1 : 2; }
static LSTATUS RegQueryValueExW(HKEY k, const wchar_t *n, DWORD *r, DWORD *type, BYTE *data, DWORD *size)
{ (void)k; (void)r; int i = slot(n); if (!present[i]) return ERROR_FILE_NOT_FOUND; *type = REG_DWORD; memcpy(data, &reg[i], 4); *size = 4; return 0; }
static LSTATUS RegSetValueExW(HKEY k, const wchar_t *n, DWORD r, DWORD type, const BYTE *data, DWORD size)
{ (void)k; (void)r; (void)type; (void)size; int i = slot(n); memcpy(&reg[i], data, 4); present[i] = 1; ++writes; return 0; }
static const wchar_t *env_pump, *env_reassert;
static DWORD GetEnvironmentVariableW(const wchar_t *name, wchar_t *buf, DWORD cap)
{
    const wchar_t *v = !wcscmp(name, L"MADEIRA_DOCK_PUMP_MS") ? env_pump : !wcscmp(name, L"MADEIRA_DOCK_REASSERT") ? env_reassert : NULL;
    if (!v) return 0;
    DWORD n = (DWORD)wcslen(v);
    if (n >= cap) return n + 1;
    wcscpy(buf, v); return n;
}
static uint64_t clock_ms; static uint64_t now(void) { return clock_ms; }
''' + helpers + r'''
int main(int argc, char **argv)
{
    struct saved_value v[3] = {{(HKEY)1, L"pid", 0, 52, true, true}, {(HKEY)1, L"ActiveUser", 0, 7, true, true}, {(HKEY)2, L"SteamPID", 0, 52, false, true}};
    struct sh_observer o = { now, NULL, NULL };
    if (argc > 1 && !strcmp(argv[1], "reg"))
    {
        reg[0] = 52; reg[1] = 7; reg[2] = 52;
        printf("intact=%d", dock_readback(v));
        reg[0] = 99; printf(" pid_changed=%d", dock_readback(v));
        reg[0] = 52; present[2] = 0; printf(" steampid_gone=%d", dock_readback(v));
        v[0].key = NULL_KEY; printf(" no_key=%d\n", dock_readback(v)); v[0].key = (HKEY)1;
        writes = 0; reg[0] = 52; reg[1] = 7; reg[2] = 52; present[2] = 1;
        printf("reassert_intact=%d", dock_reassert(v));
        reg[0] = 99; present[2] = 0; printf(" reassert_two=%d pid=%u steampid=%u", dock_reassert(v), reg[0], reg[2]);
        v[1].changed = false; reg[1] = 1234; printf(" not_ours=%d active=%u writes=%d\n", dock_reassert(v), reg[1], writes);
        env_reassert = L"1"; printf("wanted1=%d", dock_reassert_wanted());
        env_reassert = L"0"; printf(" wanted0=%d", dock_reassert_wanted());
        env_reassert = L"11"; printf(" wanted11=%d", dock_reassert_wanted());
        env_reassert = NULL; printf(" wanted_unset=%d\n", dock_reassert_wanted());
        return 0;
    }
    /* sleep: argv[1] is the env value ("-" = unset); prints the sleep at 0, 19999 and 20000 ms after the request */
    if (argc > 1 && strcmp(argv[1], "-"))
    {
        static wchar_t w[16]; size_t i; for (i = 0; argv[1][i] && i < 15; i++) w[i] = (wchar_t)(unsigned char)argv[1][i];
        w[i] = 0; env_pump = w;
    }
    clock_ms = 1000; uint32_t a = dock_sleep_ms(&o, 1000);
    clock_ms = 1000 + 19999; uint32_t b = dock_sleep_ms(&o, 1000);
    clock_ms = 1000 + 20000; uint32_t c = dock_sleep_ms(&o, 1000);
    printf("%u %u %u\n", a, b, c);
    return 0;
}
'''
        cc = shutil.which('cc') or shutil.which('clang')
        if cc:
            (t / 'h.c').write_text(harness)
            b = subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-o', str(t / 'h'), str(t / 'h.c')],
                               capture_output=True, text=True)
            check('the helpers compile on the host (C11, warnings are errors)', b.returncode == 0, b.stderr)
            if b.returncode == 0:
                reg = subprocess.run([str(t / 'h'), 'reg'], capture_output=True, text=True).stdout.splitlines()
                check('readback bits: 7 intact, 6 after the pid changed, 11 when SteamPID cannot be read, 10 with no pid key as well',
                      reg[0] == 'intact=7 pid_changed=6 steampid_gone=11 no_key=10', reg)
                check('the reassertion writes nothing while the values are intact, rewrites exactly the changed ones it published, '
                      'and never a value it did not publish', reg[1] == 'reassert_intact=0 reassert_two=2 pid=52 steampid=52 not_ours=0 active=1234 writes=2', reg)
                check('MADEIRA_DOCK_REASSERT acts on the exact value 1 only', reg[2] == 'wanted1=1 wanted0=0 wanted11=0 wanted_unset=0', reg)
                sl = {v: subprocess.run([str(t / 'h'), v], capture_output=True, text=True).stdout.strip() for v in ('-', '10', '1', '50', '0', '51', 'abc')}
                check('the loop sleeps 50 ms without the key or with a value outside 1..50, and the key\'s value only for the first 20 s',
                      sl == {'-': '50 50 50', '10': '10 10 50', '1': '1 1 50', '50': '50 50 50', '0': '50 50 50', '51': '50 50 50', 'abc': '50 50 50'}, sl)
        else:
            print('skip the host compile: no C compiler')
        # ---------------------------------------------------------------- 4. the whole host
        mingw = next((m for m in (root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin',
                                  Path('~/madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin').expanduser())
                      if (m / 'x86_64-w64-mingw32-clang').exists()), None)
        if mingw:
            b = subprocess.run([str(mingw / 'x86_64-w64-mingw32-clang'), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-cast-function-type',
                                '-static', '-Wl,--strip-all', '-Wl,--no-insert-timestamp', '-o', str(t / 'dockhost.exe')]
                               + sorted(str(f) for f in (t / 'src').glob('*.c')) + ['-ladvapi32'], capture_output=True, text=True)
            check('the patched host compiles with build/madeira-dock/build.sh\'s flags (warnings are errors)', b.returncode == 0, b.stderr[-1500:])
        else:
            print('skip the Dock compile: no llvm-mingw at hand')

# ---------------------------------------------------------------- 5. wiring
wf = (root / '.github/workflows/build-ipa.yml').read_text()
step = wf[wf.index('- name: Build Madeira Dock host'):]
step = step[:step.index('bash build/madeira-dock/build.sh')]
check('the Dock build step applies it after the keep-alive patch, warning only on failure',
      step.index('patch-dock-keep-alive.py') < step.index('python3 tools/patch-dock-steam-readback.py madeira-dock/src/launch.c || echo "::warning::')
      if 'patch-dock-steam-readback.py' in step else False)
check('the CI runs this check', 'python3 tests/host/check-dock-steam-readback.py' in wf)
dk = (root / 'app/Madeira/MadeiraDock.swift').read_text()
allowed = dk[dk.index('static let reportAllowed: Set<String> = ['):]
allowed = allowed[:allowed.index(']')]
check('the app logs the four new numeric fields', all('"%s"' % k in allowed for k in ('launch-discovery-readback', 'launch-loop-gap-ms',
                                                                                    'launch-discovery-readback-running', 'launch-discovery-reasserted')))
cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog knows both keys', '"env.MADEIRA_DOCK_PUMP_MS": {"category": "Steam & Dock"' in cat
      and '"env.MADEIRA_DOCK_REASSERT": {"category": "Steam & Dock"' in cat)

print('PASS: Dock Steam readback' if ok else 'FAIL: Dock Steam readback')
sys.exit(0 if ok else 1)
