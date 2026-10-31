#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_WOW_UNITY_PLAIN = 1: a 32-bit Unity game's own Mono counts as Mono for plain RWX memory (round 624, agent D2 S4b; opt-in).

From the Darkness (PA7PQB) and 8 archive logs of 32-bit Unity games ran at about 2 FPS with 92,000 emulated stores a second: Unity's own
Mono (mono-2.0-bdwgc.dll, mono.dll) is not Wine Mono, so the default "plain once Mono is mapped" never applied. With the key, those two
names count as well. Unset: only libmono-2.0-x86.dll, exactly as before. The [perf-hint] line (signal_arm64_ios.c) names the switch once a
session when the store faults stay at 20,000 a second.

Checks: the name match (last path component, any case, not a longer name ending the same) compiled and run; the note function takes the
Unity names only with the key; the key is read once and only "1" turns it on; the list policy accepts 0/1; the catalog generator has it;
the [perf-hint] needs three hot reports in a row and prints once.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


virt = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
named = virt[virt.index('static int ios_wow_image_named( const UNICODE_STRING *nt_name, const char *want )\n{'):]
named = named[:named.index('\n}\n') + 3]
unity = virt[virt.index('static int ios_wow_unity_plain(void)\n{'):]
unity = unity[:unity.index('\n}\n') + 3]
note = virt[virt.index('static void ios_wow_note_image( const void *base, const UNICODE_STRING *nt_name )\n{'):]
note = note[:note.index('\n}\n') + 3]
check('Wine Mono is noticed as before; Unity\'s Mono only with the key',
      'if (ios_wow_image_named( nt_name, "libmono-2.0-x86.dll" )) what = "Wine Mono";' in note
      and 'else if (ios_wow_unity_plain() && (ios_wow_image_named( nt_name, "mono-2.0-bdwgc.dll" ) || ios_wow_image_named( nt_name, "mono.dll" )))' in note
      and 'else return;' in note)
check('the key is read once, and only "1" turns it on', 'getenv( "MADEIRA_WOW_UNITY_PLAIN" )' in unity and "cached = s && s[0] == '1' && !s[1];" in unity
      and virt.count('getenv( "MADEIRA_WOW_UNITY_PLAIN" )') == 1)

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned short WCHAR;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
''' + named + unity + r'''
static int is(const char *path, const char *want)
{
    static WCHAR buf[256]; UNICODE_STRING u; size_t i;
    for (i = 0; path[i]; i++) buf[i] = (unsigned char)path[i];
    u.Buffer = buf; u.Length = (unsigned short)(i * 2); u.MaximumLength = u.Length;
    return ios_wow_image_named(&u, want);
}
int main(int argc, char **argv)
{
    printf("a=%d b=%d c=%d d=%d e=%d f=%d\n",
           is("\\??\\C:\\Games\\From The Darkness\\MonoBleedingEdge\\EmbedRuntime\\MONO-2.0-BDWGC.DLL", "mono-2.0-bdwgc.dll"),
           is("C:/x/mono.dll", "mono.dll"), is("C:\\x\\notmono.dll", "mono.dll"), is("mono.dll", "mono.dll"),
           is("C:\\windows\\mono\\libmono-2.0-x86.dll", "libmono-2.0-x86.dll"), is("C:\\x\\mono.dl", "mono.dll"));
    if (argc > 1) setenv("MADEIRA_WOW_UNITY_PLAIN", argv[1], 1);
    printf("key=%d\n", ios_wow_unity_plain());
    setenv("MADEIRA_WOW_UNITY_PLAIN", "1", 1);
    printf("again=%d\n", ios_wow_unity_plain());
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the name match compiles on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-1500:])
    if cc.returncode == 0:
        out = subprocess.run([str(exe)], capture_output=True, text=True, env={}).stdout
        check('the last path component, any case; a longer name ending the same and a shorter one do not match',
              'a=1 b=1 c=0 d=1 e=1 f=0' in out, out)
        check('unset: off (and read once: setting it later changes nothing)', 'key=0' in out and 'again=0' in out, out)
        on = subprocess.run([str(exe), '1'], capture_output=True, text=True, env={}).stdout
        two = subprocess.run([str(exe), '2'], capture_output=True, text=True, env={}).stdout
        check('"1" turns it on, any other value does not', 'key=1' in on and 'key=0' in two, on + two)

sig = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
hint = sig[sig.index('round 624, agent D2 S4d'):]
hint = hint[:hint.index('\n        }\n')]
check('[perf-hint]: 20,000 store faults a second for three reports in a row, printed once a session, log only',
      '>= 20000) hint_hot++;' in hint and 'if (hint_hot >= 3 && !hint_done)' in hint and 'hint_done = 1;' in hint
      and '"[perf-hint] r624 emulated stores keep faulting at 20000 a second or more "' in hint)
pol = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('the list policy accepts MADEIRA_WOW_UNITY_PLAIN = 0 or 1', '"MADEIRA_WOW_UNITY_PLAIN",' in pol
      and 'case "MADEIRA_WOW_UNITY_PLAIN":' in pol and 'MADEIRA_WOW_UNITY_PLAIN is 0 or 1' in pol)
check('the catalog generator describes it (default off, read in virtual_ios.c)',
      '"env.MADEIRA_WOW_UNITY_PLAIN": {"category": "Memory & JIT pool"' in (root / 'build/tools/gen-config-catalog.py').read_text())
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-wow-unity-plain.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
