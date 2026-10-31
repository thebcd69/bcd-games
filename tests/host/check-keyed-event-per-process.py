#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_KEYED_EVENT_PER_PROCESS: a child pseudo-process uses a keyed event of its own for a NULL handle; no Wine runs.

Control (Steam 870780; X49WBQ, QJAQK3, P0DERY, 6AG46N, build 592, iPhone16,2) and Slay the Spire 2 (GZFVM3, build 579) died on a read of 8 bytes of
code (0xa90453f3d10183ff, 0xe9010000000887c6) at the ARM64EC ntdll's +0x68008: Wine's RtlRunOnceComplete, walking the waiters of an InitOnce.
Its waiters are stack slots of threads that sleep in NtWaitForKeyedEvent( 0, ... ); the walk calls NtReleaseKeyedEvent( 0, ... ) on each. In
Control's register dump the release before the fault had returned x0 = 0xc0000024 (STATUS_OBJECT_TYPE_MISMATCH). A NULL handle is Wine's
process-global `keyed_event`, created once by start_main_thread; BCD runs every Windows process of a session as a pseudo-process of one unix
process and wine_ios_child_main never made a keyed event for a child, so the global holds the FIRST pseudo-process's handle number, which names
another object (or none) in a child's handle table: the wait returns at once, the waiter spins and leaves its frame, the release wakes nobody and
the walk reads the frame's new content.

What this checks:
  1. The Wine fork (dlls/ntdll/unix/sync.c, where one is at hand: $BCD_WINE_SRC, wine/ of this checkout, ~/madeira-bcd/wine; skipped when the tree
     is not the commit this checkout pins, failed when it is that commit and lacks the change): NtWaitForKeyedEvent and NtReleaseKeyedEvent take
     `ios_keyed_event()` for a NULL handle, declared under WINE_IOS and defined as `keyed_event` otherwise; the global and its one creation
     (start_main_thread) are as they were; the only users of a NULL handle in Wine's own code are RtlRunOnce.
  2. build/ntdll-unix/ios_keyed_event.h, compiled on the host and run over every combination: with the switch off (unset, 0, empty, 2, yes) a NULL
     handle is the session's, whatever is recorded; with the exact value 1 a process with a record gets its own handle and one without (the
     session's first, a NULL PEB) gets the session's; records overwrite the same PEB, take free slots, go round robin when the 64 are taken, and a
     dropped record falls back to the session's.
  3. loader_ios.c: the header is included; ios_keyed_event() returns the global when the switch is off; the child's own keyed event is made right
     after init_thread_stack and before the i386 ntdll is bound, inside wine_ios_child_main; ios_proc_ident_release drops the record; the session's
     own NtCreateKeyedEvent( &keyed_event ... ) in start_main_thread's twin is untouched.
  4. Opt-in: only the exact value 1 turns it on; nothing sets it by default (no list, no launcher entry, no app code); auto-tune's rule
     runonce-stale-waiter-keyed-event is the only thing that adds it, after that crash only, and its two fixtures propose it.
  5. The settings catalog knows the key and the CI runs this check.
Needs python3 and a C compiler.
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
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def sh(*args, cwd=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


# ---------------------------------------------------------------- 1. the Wine fork
pinned = ''
ls = sh('git', 'ls-tree', 'HEAD', 'wine', cwd=root)
m = re.match(r'160000 commit ([0-9a-f]{40})\twine$', ls)
if m:
    pinned = m.group(1)
candidates = []
if os.environ.get('BCD_WINE_SRC'):
    candidates.append(Path(os.environ['BCD_WINE_SRC']).expanduser())
candidates += [root / 'wine', Path('~/madeira-bcd/wine').expanduser()]
wine = None
for c in candidates:
    if (c / 'dlls/ntdll/unix/sync.c').is_file():
        wine = c
        break
if wine is None:
    print('skip the Wine source is not at hand (set BCD_WINE_SRC to a checkout of the Wine fork)')
else:
    head = sh('git', 'rev-parse', 'HEAD', cwd=wine)
    sync = (wine / 'dlls/ntdll/unix/sync.c').read_text()
    if pinned and head != pinned:
        print('skip the Wine tree %s is at %s, this checkout pins %s' % (wine, head[:12] or '?', pinned[:12]))
        # still hold what the OLD tree shows about the premise
        check('Wine (older tree): a NULL handle was the global in both calls (the premise)',
              sync.count('if (!handle) handle = keyed_event;') == 2 or sync.count('if (!handle) handle = ios_keyed_event();') == 2)
    else:
        wait = re.search(r'NTSTATUS WINAPI NtWaitForKeyedEvent\(.*?\n}\n', sync, re.S)
        rel = re.search(r'NTSTATUS WINAPI NtReleaseKeyedEvent\(.*?\n}\n', sync, re.S)
        check('Wine: NtWaitForKeyedEvent and NtReleaseKeyedEvent exist', bool(wait and rel))
        for name, body in (('NtWaitForKeyedEvent', wait), ('NtReleaseKeyedEvent', rel)):
            text = body.group(0) if body else ''
            check('Wine: %s takes ios_keyed_event() for a NULL handle' % name, 'if (!handle) handle = ios_keyed_event();' in text
                  and 'keyed_event;' not in text.replace('ios_keyed_event()', ''))
        check('Wine: ios_keyed_event is declared under WINE_IOS and is the global elsewhere',
              re.search(r'#ifdef WINE_IOS\n(?:/\*.*?\*/\n)?extern HANDLE ios_keyed_event\( void \);\n#else\n#define ios_keyed_event\(\) keyed_event\n#endif\n',
                        sync, re.S) is not None)
        check('Wine: the global is still defined once', sync.count('HANDLE keyed_event = 0;') == 1)
        loader = (wine / 'dlls/ntdll/unix/loader.c').read_text() if (wine / 'dlls/ntdll/unix/loader.c').is_file() else ''
        if loader:
            check('Wine: start_main_thread still creates the process-global keyed event',
                  'NtCreateKeyedEvent( &keyed_event, GENERIC_READ | GENERIC_WRITE, NULL, 0 );' in loader)
        users = sh('grep', '-rn', '--include=*.c', '-E', r'Nt(WaitFor|Release)KeyedEvent\(\s*0\s*,', str(wine / 'dlls/ntdll'))
        sites = [l for l in users.splitlines() if '/tests/' not in l and 'sync.c:' in l]
        check('Wine: the only NULL-handle callers in ntdll are RtlRunOnceBeginInitialize and RtlRunOnceComplete',
              len(sites) == 2 and all('dlls/ntdll/sync.c' in l for l in sites), '\n'.join(users.splitlines()[:6]))
        print('  (Wine tree %s at %s)' % (wine, head[:12]))

# ---------------------------------------------------------------- 2. the header, compiled and run
header = root / 'build/ntdll-unix/ios_keyed_event.h'
htext = header.read_text()
cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
harness = r'''
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "ios_keyed_event.h"

static int fails;
#define EXPECT(c, msg) do { if (!(c)) { printf("FAIL %s\n", msg); fails++; } } while (0)

int main(int argc, char **argv)
{
    struct ios_keyed_event_slot slots[IOS_KEYED_EVENT_SLOTS];
    int next = 0, i, en = ios_keyed_event_enabled();
    void *session = (void *)(uintptr_t)0x44;
    char *pebs = (char *)(uintptr_t)0x7000000000ULL;      /* PEB addresses: only compared */

    memset( slots, 0, sizeof(slots) );
    printf("enabled=%d\n", en);

    /* nothing recorded: everything is the session's */
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, pebs, session, en ) == session, "empty table" );
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, NULL, session, en ) == session, "NULL peb" );

    /* a record */
    EXPECT( ios_keyed_event_store( slots, IOS_KEYED_EVENT_SLOTS, &next, pebs, (void *)(uintptr_t)0x84 ) == 0, "first record in slot 0" );
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, pebs, session, en ) == (en ? (void *)(uintptr_t)0x84 : session), "recorded process" );
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, pebs + 0x10000, session, en ) == session, "another process" );
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, NULL, session, en ) == session, "NULL peb with a record" );
    /* `enabled` passed as 0 is the switch off whatever the table holds (what ios_keyed_event() does before it looks) */
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, pebs, session, 0 ) == session, "switch off beats the table" );

    /* the same PEB again (its address was reused): overwritten, same slot */
    EXPECT( ios_keyed_event_store( slots, IOS_KEYED_EVENT_SLOTS, &next, pebs, (void *)(uintptr_t)0x88 ) == 0, "same PEB overwrites slot 0" );
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, pebs, session, 1 ) == (void *)(uintptr_t)0x88, "overwritten handle" );

    /* a NULL peb or handle is not stored */
    EXPECT( ios_keyed_event_store( slots, IOS_KEYED_EVENT_SLOTS, &next, NULL, (void *)(uintptr_t)0x8c ) == -1, "NULL peb not stored" );
    EXPECT( ios_keyed_event_store( slots, IOS_KEYED_EVENT_SLOTS, &next, pebs + 0x20000, NULL ) == -1, "NULL handle not stored" );

    /* drop */
    EXPECT( ios_keyed_event_drop( slots, IOS_KEYED_EVENT_SLOTS, pebs ) == 1, "drop finds the record" );
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, pebs, session, 1 ) == session, "dropped process is the session's" );
    EXPECT( ios_keyed_event_drop( slots, IOS_KEYED_EVENT_SLOTS, pebs ) == 0, "second drop finds nothing" );
    EXPECT( ios_keyed_event_drop( slots, IOS_KEYED_EVENT_SLOTS, NULL ) == 0, "drop of NULL" );

    /* fill all 64 slots with distinct processes, then one more: round robin from slot 0 */
    memset( slots, 0, sizeof(slots) ); next = 0;
    for (i = 0; i < IOS_KEYED_EVENT_SLOTS; i++)
        EXPECT( ios_keyed_event_store( slots, IOS_KEYED_EVENT_SLOTS, &next, pebs + (i + 1) * 0x10000, (void *)(uintptr_t)(0x100 + 4 * i) ) == i, "free slots in order" );
    EXPECT( ios_keyed_event_store( slots, IOS_KEYED_EVENT_SLOTS, &next, pebs + 0x900000, (void *)(uintptr_t)0x900 ) == 0, "full: slot 0 goes first" );
    EXPECT( ios_keyed_event_store( slots, IOS_KEYED_EVENT_SLOTS, &next, pebs + 0xA00000, (void *)(uintptr_t)0xA00 ) == 1, "full: then slot 1" );
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, pebs + 0x900000, session, 1 ) == (void *)(uintptr_t)0x900, "the new one is found" );
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, pebs + 0x10000, session, 1 ) == session, "the replaced one is the session's" );
    EXPECT( ios_keyed_event_pick( slots, IOS_KEYED_EVENT_SLOTS, pebs + 3 * 0x10000, session, 1 ) == (void *)(uintptr_t)0x108, "an untouched one keeps its own" );
    for (i = 0; i < IOS_KEYED_EVENT_SLOTS + 3; i++) ios_keyed_event_store( slots, IOS_KEYED_EVENT_SLOTS, &next, pebs + 0xB00000 + i * 0x10000, (void *)(uintptr_t)0xB00 );
    EXPECT( next >= 0 && next < IOS_KEYED_EVENT_SLOTS, "the round robin index stays inside the table" );

    printf(fails ? "FAILED %d\n" : "all ok\n", fails);
    return fails != 0;
}
'''
tmp = Path(tempfile.mkdtemp(prefix='keyed-event-check-'))
if not cc:
    check('a C compiler is at hand', False)
else:
    (tmp / 'h.c').write_text(harness)
    b = subprocess.run([cc, '-std=gnu11', '-O1', '-Wall', '-Werror', '-I', str(header.parent), '-o', str(tmp / 'h'), str(tmp / 'h.c')],
                       capture_output=True, text=True)
    check('ios_keyed_event.h compiles on the host', b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        for value, want in ((None, 0), ('0', 0), ('', 0), ('2', 0), ('yes', 0), ('11', 0), (' 1', 0), ('1', 1)):
            env = {k: v for k, v in os.environ.items() if k != 'MADEIRA_KEYED_EVENT_PER_PROCESS'}
            if value is not None:
                env['MADEIRA_KEYED_EVENT_PER_PROCESS'] = value
            r = subprocess.run([str(tmp / 'h')], env=env, capture_output=True, text=True)
            label = 'unset' if value is None else repr(value)
            check('switch %s: enabled=%d and every table case behaves' % (label, want),
                  r.returncode == 0 and ('enabled=%d' % want) in r.stdout and 'all ok' in r.stdout, r.stdout[-800:] + r.stderr[-400:])
shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 3. loader_ios.c
loader = (root / 'build/ntdll-unix/loader_ios.c').read_text()
check('loader_ios.c includes the header', '#include "ios_keyed_event.h"' in loader)
fn = re.search(r'HANDLE ios_keyed_event\( void \)\n\{.*?\n\}\n', loader, re.S)
check('ios_keyed_event() returns the global when the switch is off',
      bool(fn) and 'if (!ios_keyed_event_enabled()) return keyed_event;' in fn.group(0)
      and 'ios_keyed_event_pick( ios_keyed_event_slots, IOS_KEYED_EVENT_SLOTS, ios_jit_current_peb(), keyed_event, 1 )' in fn.group(0))
boot = re.search(r'static void ios_keyed_event_child_boot\( void \*child_peb \)\n\{.*?\n\}\n', loader, re.S)
check('the child boot makes the keyed event only with the switch on',
      bool(boot) and boot.group(0).index('if (!ios_keyed_event_enabled()) return;') < boot.group(0).index('NtCreateKeyedEvent( &h,')
      and 'ios_keyed_event_store( ios_keyed_event_slots' in boot.group(0))
child = loader[loader.index('wine_ios_child_main( '):] if 'wine_ios_child_main( ' in loader else ''
i_stack = child.find('init_thread_stack( teb, 0, 0, 0 )')
i_hook = child.find('ios_keyed_event_child_boot( child_peb );')
i_wow = child.find('load_wow64_ntdll( ios_cur_image_info()->Machine );')
check('wine_ios_child_main: the hook comes after init_thread_stack and before the i386 ntdll is bound, once',
      0 < i_stack < i_hook < i_wow and loader.count('ios_keyed_event_child_boot( child_peb );') == 1, '%d %d %d' % (i_stack, i_hook, i_wow))
rel = re.search(r'void ios_proc_ident_release\( void \*peb_id \)\n\{.*?\n\}\n', loader, re.S)
check('ios_proc_ident_release drops the record before the identity goes',
      bool(rel) and 'ios_keyed_event_drop( ios_keyed_event_slots, IOS_KEYED_EVENT_SLOTS, peb_id );' in rel.group(0))
check('the session\'s own keyed event is made as before, unconditionally',
      loader.count('    NtCreateKeyedEvent( &keyed_event, GENERIC_READ | GENERIC_WRITE, NULL, 0 );\n') == 1)

# ---------------------------------------------------------------- 4. opt-in and auto-tune
check('the header reads the one switch and only the value 1 turns it on',
      htext.count('getenv(') == 1 and 'getenv( "MADEIRA_KEYED_EVENT_PER_PROCESS" )' in htext and "e[0] == '1' && !e[1]" in htext)
readers = []
for path in list((root / 'build').rglob('*.c')) + list((root / 'build').rglob('*.h')) + list((root / 'app/Madeira').rglob('*.swift')) \
        + list((root / 'app/Madeira').rglob('*.m')) + [root / 'recommended/recommendations.json']:
    if path.name in ('ConfigCatalog.generated.swift', 'AutoTuneRules.generated.swift') or not path.is_file():
        continue
    try:
        text = path.read_text(errors='replace')
    except OSError:
        continue
    if 'MADEIRA_KEYED_EVENT_PER_PROCESS' in text:
        readers.append(str(path.relative_to(root)))
# round 624: the list policy may accept the key (0 or 1), and the list may set it for Control alone, whose every log is this crash (5 of 5).
check('only the header and the loader name the switch in the C sources and the app (besides the list policy): no launcher entry or app code sets it',
      set(readers) - {'recommended/recommendations.json', 'app/Madeira/GameRecommendationsList.swift'}
      == {'build/ntdll-unix/ios_keyed_event.h', 'build/ntdll-unix/loader_ios.c'}, str(sorted(readers)))
recs = json.loads((root / 'recommended/recommendations.json').read_text())
setters = sorted(r['id'] for r in recs['rules'] if 'MADEIRA_KEYED_EVENT_PER_PROCESS' in json.dumps(r.get('config', [])) + json.dumps(r.get('smallRAMConfig', [])))
check('in the list only the rule of Control sets it (a game that never ran past this crash)', setters in ([], ['control']), str(setters))
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rule = next((r for r in mod.RULES if r['id'] == 'runonce-stale-waiter-keyed-event'), None)
check('auto-tune has the rule with exactly the one line, no retry first, after the crash only',
      rule is not None and rule['lines'] == ['env.MADEIRA_KEYED_EVENT_PER_PROCESS = 1'] and rule.get('noRetry') is True
      and rule.get('outcomes') == ['crashed'])
if rule:
    flat = json.dumps(rule['all'])
    anyof = json.dumps(rule.get('any', []))
    check('the rule needs the fault in one of the two RunOnce waiter loops of the ARM64EC ntdll (+0x68008, +0x680a8) and Wine\'s unhandled read '
          'message or the c0000024 the release returned (round 624), and is off once the line is in force',
          '"[mach_exc] pc PE: ntdll.dll+0x680"' in flat and 'Unhandled page fault on read access to' in anyof and '[fault-full] x0=c0000024 x1=' in anyof
          and 'MADEIRA_KEYED_EVENT_PER_PROCESS=1' in json.dumps(rule['none']))
for name in ('control-runonce-keyed-event-2026-10-23', 'slay-the-spire-2-runonce-keyed-event-2026-10-22',
             'control-launcher-child-runonce-2026-10-29', 'spider-man-remastered-runonce-keyed-event-2026-10-28',
             'mechabellum-runonce-failpath-2026-10-29'):
    exp = root / 'tests/fixtures/autotune' / (name + '.expect')
    data = json.loads(exp.read_text()) if exp.is_file() else {}
    check('fixture %s proposes the line' % name,
          data.get('next') == ['env.MADEIRA_KEYED_EVENT_PER_PROCESS = 1'] and data.get('matched') == ['runonce-stale-waiter-keyed-event'])

# ---------------------------------------------------------------- 4b. the rule's offsets are still the two loops of the committed ntdll
# round 624 (agent B): the rule names ntdll.dll+0x68008 / +0x680a8, offsets of the ARM64EC ntdll.dll committed since build 579; a rebuilt
# ntdll can move them silently (the fixtures are old logs). Both must still be `ldr x19, [x1]` (0xf9400033), the load of the waiter list.
def pe_word_at_rva(path, rva):
    import struct
    d = path.read_bytes()
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    opt = struct.unpack_from('<H', d, pe + 20)[0]
    sec = pe + 24 + opt
    for i in range(nsec):
        vsize, va, raw_size, raw_off = struct.unpack_from('<IIII', d, sec + i * 40 + 8)   # VirtualSize, VirtualAddress, SizeOfRawData, PointerToRawData
        if va <= rva < va + max(vsize, raw_size):
            return struct.unpack_from('<I', d, raw_off + rva - va)[0]
    return None
ntdll = root / 'app/Madeira/arm64ec-windows/ntdll.dll'
words = [pe_word_at_rva(ntdll, rva) if ntdll.is_file() else None for rva in (0x68008, 0x680a8)]
check('the committed ARM64EC ntdll.dll still has `ldr x19, [x1]` at +0x68008 and +0x680a8 (the rule\'s two loops)',
      words == [0xf9400033, 0xf9400033], str([hex(w) if w is not None else None for w in words]))

# ---------------------------------------------------------------- 5. catalog and CI
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the settings catalog knows the key', 'key: "env.MADEIRA_KEYED_EVENT_PER_PROCESS"' in catalog)
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('the CI runs this check', 'python3 tests/host/check-keyed-event-per-process.py' in ci)

print('PASS: keyed event per process' if ok else 'FAIL: keyed event per process')
sys.exit(0 if ok else 1)
