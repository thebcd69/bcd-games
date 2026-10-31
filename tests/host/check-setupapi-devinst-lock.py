#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""setupapi-optin.dll: Wine's setupapi with a lock round its device node table; no Wine runs.

Dying Light (Steam 239140, build 576, iPhone18,2, log 1c807435) ends with an access violation in
setupapi.dll+0x132bc (create_device, get_devinst_for_device_id inlined) while libpad.dll lists HID devices and
the main thread loads dinput8.dll: Wine's alloc_devinst_for_device_id has no lock and sets
devinst_table_size = 256 BEFORE devinst_table = calloc(...). tools/patch-wine-setupapi-devinst-lock.py puts one
SRWLOCK round every read and write of the table and allocates before it publishes the size;
tools/build-wine-extra-dlls.sh ships the result as setupapi-optin.dll NEXT TO the untouched setupapi.dll;
app/Madeira/WineProcessBridge.m links it as setupapi.dll only for a game with env.MADEIRA_OPTIN_DLLS = setupapi.

Checks:
  - the patch applies to the pinned Wine's devinst.c (every anchor once), a second run changes nothing, it
    removes nothing but table accesses, and the file carries a "Modified by BCD" notice;
  - the patched code takes the lock on every path: the allocation, both readers of the table
    (get_devnode_id, locate_devnode), and every caller of the unlocked lookup; a new table is allocated before
    its size is published; no other code reads the table; the five readers (get_devnode_device,
    CM_Get_Device_IDA/IDW, CM_Get_Device_ID_Size, CM_Locate_DevNode_ExW) go through the helpers;
  - the patched table code, cut out of devinst.c and compiled on the host with a pthread lock behind the SRWLOCK
    calls, keeps its invariants under concurrent allocation and lookup (indices unique, ids found, growth past
    256 and 512 entries, the table and its size never seen apart); the original code, run the same way, is
    reported (it can fault in the same place; not a pass condition: a race);
  - devinst.c compiles with llvm-mingw for arm64ec with no warning (when the toolchain and a configured Wine
    build are found);
  - --verify accepts a DLL with the marker and the shipped exports and refuses one without the marker or with
    other exports (synthetic PEs);
  - the build step patches, builds, strips, pads, verifies, ships as setupapi-optin.dll, never writes
    setupapi.dll, restores devinst.c and the objects; MADEIRA_SETUPAPI_BUILD=0 skips it;
  - the existing opt-in link in WineProcessBridge.m is what runs it: a listed name is linked over its shipped
    name after the farms were relinked from the bundle, an unlisted one loses only a link to the -optin file
    (so the shipped setupapi.dll can never be removed by it);
  - RecommendationsPolicy and the catalog allow and describe `setupapi`; Dying Light's list carries the line
    for build 577 on; auto-tune names it for the fault; CI runs this check and verifies the shipped DLL;
    .gitignore keeps the built DLL out of git.
The compile steps are skipped when no compiler is found. MADEIRA_WINE_SRC names the Wine tree (the submodule's
by default); MADEIRA_MINGW names the llvm-mingw bin directory (toolchains/llvm-mingw-*/bin by default).
"""
from pathlib import Path
import difflib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
script = root / 'tools/patch-wine-setupapi-devinst-lock.py'
spec = importlib.util.spec_from_file_location('devinstlock', script)
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)
wine = Path(os.environ.get('MADEIRA_WINE_SRC', root / 'wine'))
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


def body(text, header):
    """a function's text: from its header to the first closing brace in column 0"""
    i = text.index(header)
    return text[i:text.index('\n}\n', i) + 3]


# ---- the patch --------------------------------------------------------------
patched = original = None
src_dir = wine / 'dlls/setupapi'
if (src_dir / 'devinst.c').is_file():
    original = (src_dir / 'devinst.c').read_text()
    with tempfile.TemporaryDirectory(prefix='madeira-setupapi-') as directory:
        tree = Path(directory)
        (tree / 'dlls/setupapi').mkdir(parents=True)
        shutil.copy(src_dir / 'devinst.c', tree / 'dlls/setupapi/devinst.c')
        run = subprocess.run([sys.executable, str(script), str(tree)], capture_output=True, text=True)
        check('the patch applies (%s)' % (run.stdout.strip().replace(str(tree), '<tree>') or run.stderr.strip()),
              run.returncode == 0)
        patched = (tree / 'dlls/setupapi/devinst.c').read_text()
        again = subprocess.run([sys.executable, str(script), str(tree)], capture_output=True, text=True)
        check('a second run changes nothing', again.returncode == 0 and 'already patched' in again.stdout and
              (tree / 'dlls/setupapi/devinst.c').read_text() == patched)
        # the original names, for the two runs below: an unmodified tree is refused when an anchor moved
        moved = tree / 'moved'
        (moved / 'dlls/setupapi').mkdir(parents=True)
        (moved / 'dlls/setupapi/devinst.c').write_text(original.replace('static unsigned int devinst_table_size;',
                                                                         'static unsigned int devinst_table_count;'))
        refused = subprocess.run([sys.executable, str(script), str(moved)], capture_output=True, text=True)
        check('a tree whose anchor moved is refused by name, and left as it was',
              refused.returncode != 0 and 'anchor found 0 times' in refused.stderr and
              (moved / 'dlls/setupapi/devinst.c').read_text().count('devinst_lock') == 0)
        freed = tree / 'freed'
        (freed / 'dlls/setupapi').mkdir(parents=True)
        (freed / 'dlls/setupapi/devinst.c').write_text(original + '\nstatic void bcd_probe(void) { free(devinst_table[0]); }\n')
        # free(devinst_table[...]) is Wine freeing an entry: the lock-free use of the string is then unsafe
        refused = subprocess.run([sys.executable, str(script), str(freed)], capture_output=True, text=True)
        check('a Wine that frees table entries is refused (the strings would no longer outlive the lock)',
              refused.returncode != 0 and 'frees devinst_table' in refused.stderr)
else:
    print('SKIP: no Wine source (set MADEIRA_WINE_SRC or check out the submodule)')

if patched:
    removed = [l[1:] for l in difflib.unified_diff(original.split('\n'), patched.split('\n'), lineterm='', n=0)
               if l.startswith('-') and not l.startswith('---')]
    check('the patch removes nothing but accesses to the table (%d lines)' % len(removed),
          all('devinst_table' in l or l in ('        {', '        }') for l in removed))
    check('the file says it was modified by BCD, once, in the header, and names the script',
          patched.count('Modified by BCD, 2026-10-21 (tools/patch-wine-setupapi-devinst-lock.py)') == 1 and
          patched.index('Modified by BCD, 2026-10-21') < patched.index('#include <assert.h>') and
          'Modified by BCD: devinst_lock guards' in patched)
    check('the marker is in the file (the DLL carries it for --verify)', dl.MARKER in patched)

    start = patched.index(dl.GUARDED_FROM)
    end = patched.index(dl.GUARDED_TO, start) + len(dl.GUARDED_TO)
    block = patched[start:end]
    check('no code outside the helpers touches the table',
          'devinst_table' not in patched[:start] + patched[end:])
    check('there is one lock, a static SRWLOCK', patched.count('SRWLOCK devinst_lock = SRWLOCK_INIT;') == 1 and
          block.count('AcquireSRWLockExclusive(&devinst_lock);') == 3)

    alloc = body(patched, 'static DEVINST alloc_devinst_for_device_id(const WCHAR *id)')
    check('alloc: the lock is taken before the first look at the table',
          alloc.index('AcquireSRWLockExclusive(&devinst_lock);') < alloc.index('devinst_table') and
          alloc.count('AcquireSRWLockExclusive(&devinst_lock);') == 1)
    returns = [m.start() for m in re.finditer(r'\breturn\b', alloc)]
    check('alloc: every return is right after a release (the lock is held on no path out)',
          len(returns) == 2 and alloc.count('ReleaseSRWLockExclusive(&devinst_lock);') == 2 and
          all(re.search(r'ReleaseSRWLockExclusive\(&devinst_lock\);\n\s*$', alloc[:r]) for r in returns))
    check('alloc: the new table is allocated before its size is published',
          alloc.index('calloc(') < alloc.index('devinst_table = table;') < alloc.index('devinst_table_size = size;') and
          alloc.index('realloc(') < alloc.index('devinst_table = table;') and
          'devinst_table_size = 256' not in alloc and 'devinst_table_size *= 2' not in alloc)
    check('alloc: a failed allocation changes neither the table nor the size and gives no device node',
          alloc.index('if (!table)') < alloc.index('devinst_table = table;') and 'return -1;' in alloc)
    for name, header in (('get_devnode_id', 'static const WCHAR *get_devnode_id(DEVINST devnode)'),
                         ('locate_devnode', 'static BOOL locate_devnode(const WCHAR *id, DEVINST *devnode)')):
        b = body(patched, header)
        a, t, r = (b.index('AcquireSRWLockExclusive(&devinst_lock);'), b.index('devinst_table'),
                   b.index('ReleaseSRWLockExclusive(&devinst_lock);'))
        check('%s: every access to the table is between the lock and its release' % name,
              a < t < r and b.rfind('devinst_table') < r and b.count('return') == 1 and b.index('return') > r)
    lookup = 'static DEVINST get_devinst_for_device_id(const WCHAR *id)'
    check('the unlocked lookup says so, and is called only with the lock held (alloc, locate_devnode)',
          '/* Called with devinst_lock held. */\n' + lookup in patched and
          len(re.findall(r'\bget_devinst_for_device_id\(', patched)) == 3 and
          all('get_devinst_for_device_id(' in b for b in (alloc, body(patched, 'static BOOL locate_devnode('))))
    readers = {
        'get_devnode_device': 'static struct device *get_devnode_device(DEVINST devnode, HDEVINFO *set, SP_DEVINFO_DATA *data)',
        'CM_Get_Device_IDA': 'CONFIGRET WINAPI CM_Get_Device_IDA(',
        'CM_Get_Device_IDW': 'CONFIGRET WINAPI CM_Get_Device_IDW(',
        'CM_Get_Device_ID_Size': 'CONFIGRET WINAPI CM_Get_Device_ID_Size(',
    }
    for name, header in readers.items():
        b = body(patched, header)
        check('%s asks get_devnode_id (the string outlives the lock: entries are never freed)' % name,
              'get_devnode_id(devnode)' in b and 'devinst_table' not in b)
    b = body(patched, 'CONFIGRET WINAPI CM_Locate_DevNode_ExW(')
    check('CM_Locate_DevNode_ExW asks locate_devnode', 'locate_devnode(device_id, &ret)' in b and 'devinst_table' not in b)
    b = body(patched, 'static struct device *get_devnode_device(')
    check('get_devnode_device opens the device with the string, outside the lock',
          'SetupDiOpenDeviceInfoW(*set, id, NULL, 0, data)' in b and 'SRWLock' not in b)
    check('no lock is held across a call out of the table code (the helpers only allocate and compare)',
          all(call not in block for call in ('SetupDi', 'CM_', 'RegOpen', 'SendMessage', 'WaitFor', 'Sleep(')))

    # ---- the table code on the host, with and without the lock ----------------
    GET_ORIG = original[original.index(dl.GUARDED_FROM):original.index('static void SETUPDI_GuidToString')]
    PRELUDE = r'''
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <wchar.h>
#include <signal.h>
#include <unistd.h>
typedef wchar_t WCHAR;
typedef uint32_t DEVINST;
typedef int BOOL;
#define TRUE 1
#define FALSE 0
#define wcsicmp wcscasecmp
#define TRACE(...) ((void)0)
typedef struct { pthread_mutex_t m; } SRWLOCK;
#define SRWLOCK_INIT { PTHREAD_MUTEX_INITIALIZER }
static void AcquireSRWLockExclusive(SRWLOCK *l) { pthread_mutex_lock(&l->m); }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { pthread_mutex_unlock(&l->m); }
'''
    COMMON = r'''
#define IDS 1500
#define WRITERS 6
static pthread_barrier_t gate;
static volatile int writers_done;
static int torn;                       /* the table and its size seen apart */

static void make_id(WCHAR *out, int i, int lower)
{
    if (lower) swprintf(out, 64, L"hid\\vid_%04x&pid_0001\\0&%04x&0&0&0", i, i);
    else       swprintf(out, 64, L"HID\\VID_%04X&PID_0001\\0&%04X&0&0&0", i, i);
}

static void reset(void)
{
    unsigned int i;
    for (i = 0; i < devinst_table_size; i++) free(devinst_table[i]);
    free(devinst_table);
    devinst_table = NULL;
    devinst_table_size = 0;
}
'''
    PATCHED_DRIVER = r'''
static void *writer(void *arg)
{
    long w = (long)arg;
    WCHAR id[64];
    int n;
    for (n = 0; n < IDS; n++)
    {
        int i = (int)(((long)n * 7 + w * 211) % IDS);   /* 7 and 1500 are coprime: every id once, in another order */
        make_id(id, i, (int)(w & 1));
        if (alloc_devinst_for_device_id(id) == (DEVINST)-1) { fprintf(stderr, "alloc failed\n"); exit(10); }
    }
    return NULL;
}

static void *reader(void *arg)
{
    WCHAR id[64];
    (void)arg;
    while (!writers_done)
    {
        int i;
        for (i = 0; i < IDS; i += 37)
        {
            const WCHAR *s = get_devnode_id((DEVINST)i);
            DEVINST back;
            if (!s) continue;
            if (!locate_devnode(s, &back) || back != (DEVINST)i) { fprintf(stderr, "node %d: %ls not found back\n", i, s); exit(11); }
        }
        make_id(id, 3, 0);
        locate_devnode(id, &(DEVINST){0});
    }
    return NULL;
}

static void *watcher(void *arg)     /* the invariant, under the lock: the table and its size go together */
{
    (void)arg;
    while (!writers_done)
    {
        AcquireSRWLockExclusive(&devinst_lock);
        if ((devinst_table == NULL) != (devinst_table_size == 0)) torn++;
        ReleaseSRWLockExclusive(&devinst_lock);
    }
    return NULL;
}

static void *round_a(void *arg) { WCHAR id[64]; make_id(id, 0, 0); pthread_barrier_wait(&gate); alloc_devinst_for_device_id(id); return arg; }
static void *round_b(void *arg) { WCHAR id[64]; make_id(id, 1, 0); pthread_barrier_wait(&gate); alloc_devinst_for_device_id(id); return arg; }
static void *round_r(void *arg) { pthread_barrier_wait(&gate); get_devnode_id(0); get_devnode_id(1); return arg; }

int main(int argc, char **argv)
{
    WCHAR id[64];
    DEVINST d;
    int i, rounds = argc > 1 ? atoi(argv[1]) : 2000;
    pthread_t t[WRITERS + 3];

    /* one thread: indices in order, a lower-case spelling finds the same node, growth 256 -> 512 -> 1024 */
    for (i = 0; i < 700; i++)
    {
        make_id(id, i, 0);
        if (alloc_devinst_for_device_id(id) != (DEVINST)i) { puts("single: index"); return 1; }
    }
    for (i = 0; i < 700; i++)
    {
        make_id(id, i, 1);
        if (alloc_devinst_for_device_id(id) != (DEVINST)i) { puts("single: lower case"); return 1; }
        make_id(id, i, 0);
        if (!locate_devnode(id, &d) || d != (DEVINST)i || !get_devnode_id((DEVINST)i) || wcscmp(get_devnode_id((DEVINST)i), id)) { puts("single: lookup"); return 1; }
    }
    if (devinst_table_size != 1024) { puts("single: size"); return 1; }
    if (get_devnode_id(700) || get_devnode_id(1024) || get_devnode_id((DEVINST)-1)) { puts("single: out of range"); return 1; }
    wcscpy(id, L"NOT\\THERE");
    if (locate_devnode(id, &d)) { puts("single: found a stranger"); return 1; }
    reset();
    puts("single thread: ok");

    /* many threads: every id gets one node, the nodes are distinct, readers never see a half-made entry */
    {
        pthread_t r1, r2, w1;
        long k;
        unsigned char seen[IDS] = {0};
        for (k = 0; k < WRITERS; k++) pthread_create(&t[k], NULL, writer, (void *)k);
        pthread_create(&r1, NULL, reader, NULL);
        pthread_create(&r2, NULL, reader, NULL);
        pthread_create(&w1, NULL, watcher, NULL);
        for (k = 0; k < WRITERS; k++) pthread_join(t[k], NULL);
        writers_done = 1;
        pthread_join(r1, NULL); pthread_join(r2, NULL); pthread_join(w1, NULL);
        for (i = 0; i < IDS; i++)
        {
            make_id(id, i, 0);
            if (!locate_devnode(id, &d) || d >= IDS || seen[d]) { printf("stress: id %d -> node %u (missing or twice)\n", i, d); return 1; }
            seen[d] = 1;
            if (!get_devnode_id(d) || wcscasecmp(get_devnode_id(d), id)) { puts("stress: node holds another id"); return 1; }
        }
        if (torn) { puts("stress: the table and its size were seen apart"); return 1; }
        if (devinst_table_size < IDS) { puts("stress: table too small"); return 1; }
        printf("stress: %d ids from %d threads, nodes distinct, table %u entries: ok\n", IDS, WRITERS, devinst_table_size);
        reset();
    }

    /* the first table: two threads register the first two devices at the same moment while a third reads */
    for (i = 0; i < rounds; i++)
    {
        pthread_barrier_init(&gate, NULL, 3);
        writers_done = 0;
        pthread_create(&t[0], NULL, round_a, NULL);
        pthread_create(&t[1], NULL, round_b, NULL);
        pthread_create(&t[2], NULL, round_r, NULL);
        pthread_join(t[0], NULL); pthread_join(t[1], NULL); pthread_join(t[2], NULL);
        pthread_barrier_destroy(&gate);
        make_id(id, 0, 0);
        if (!locate_devnode(id, &d) || d > 1) { puts("race: first device lost"); return 1; }
        make_id(id, 1, 0);
        if (!locate_devnode(id, &d) || d > 1) { puts("race: second device lost"); return 1; }
        reset();
    }
    printf("first table: %d rounds of two threads registering and one reading: ok\n", rounds);
    return 0;
}
'''
    ORIGINAL_DRIVER = r'''
static void *round_a(void *arg) { WCHAR id[64]; make_id(id, 0, 0); pthread_barrier_wait(&gate); alloc_devinst_for_device_id(id); return arg; }
static void *round_b(void *arg) { WCHAR id[64]; make_id(id, 1, 0); pthread_barrier_wait(&gate); alloc_devinst_for_device_id(id); return arg; }

/* Wine's code faults here (a NULL table read); a handler ends the run at once with 42, so the fault is
 * counted without a crash report on the developer's machine. */
static void on_fault(int sig) { static const char m[] = "original: fault in the table code\n"; (void)sig; if (write(1, m, sizeof(m) - 1)) {} _exit(42); }

int main(int argc, char **argv)
{
    pthread_t t[2];
    int i, rounds = argc > 1 ? atoi(argv[1]) : 2000;
    signal(SIGSEGV, on_fault);
    signal(SIGBUS, on_fault);
    for (i = 0; i < rounds; i++)
    {
        pthread_barrier_init(&gate, NULL, 2);
        pthread_create(&t[0], NULL, round_a, NULL);
        pthread_create(&t[1], NULL, round_b, NULL);
        pthread_join(t[0], NULL); pthread_join(t[1], NULL);
        pthread_barrier_destroy(&gate);
        reset();
    }
    printf("original: %d rounds, no fault\n", rounds);
    return 0;
}
'''
    PORT = r'''
#ifdef __APPLE__
/* macOS has no pthread barriers: the same calls on a mutex and a condition */
typedef struct { pthread_mutex_t m; pthread_cond_t c; int need, count, gen; } pthread_barrier_t;
static int pthread_barrier_init(pthread_barrier_t *b, void *attr, int n) { (void)attr; pthread_mutex_init(&b->m, NULL); pthread_cond_init(&b->c, NULL); b->need = n; b->count = 0; b->gen = 0; return 0; }
static int pthread_barrier_destroy(pthread_barrier_t *b) { pthread_mutex_destroy(&b->m); pthread_cond_destroy(&b->c); return 0; }
static int pthread_barrier_wait(pthread_barrier_t *b)
{
    pthread_mutex_lock(&b->m);
    if (++b->count == b->need) { b->count = 0; b->gen++; pthread_cond_broadcast(&b->c); }
    else { int g = b->gen; while (g == b->gen) pthread_cond_wait(&b->c, &b->m); }
    pthread_mutex_unlock(&b->m);
    return 0;
}
#endif
'''
    cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
    if not cc:
        print('SKIP host compile of the table code (no C compiler)')
    else:
        with tempfile.TemporaryDirectory(prefix='madeira-setupapi-cc-') as directory:
            d = Path(directory)
            # the barrier shim comes before the harness; both variants use the same prelude
            (d / 'patched.c').write_text(PRELUDE + PORT + block + COMMON + PATCHED_DRIVER)
            (d / 'original.c').write_text(PRELUDE + PORT + GET_ORIG + COMMON + ORIGINAL_DRIVER)
            flags = ['-std=gnu11', '-O1', '-g', '-Wall', '-Werror', '-Wno-unused-function', '-Wno-unused-variable',
                     '-Wno-unknown-warning-option', '-pthread']
            built = {}
            for name in ('patched', 'original'):
                r = subprocess.run([cc] + flags + ['-o', str(d / name), str(d / (name + '.c'))],
                                   capture_output=True, text=True)
                built[name] = r.returncode == 0
                check('the %s table code compiles on the host' % name, built[name])
                if r.returncode:
                    print(r.stderr[:3000])
            if built['patched']:
                try:
                    r = subprocess.run([str(d / 'patched'), '3000'], capture_output=True, text=True, timeout=300)
                    print('\n'.join('     | ' + l for l in r.stdout.strip().split('\n')))
                    check('the patched table code keeps its invariants under concurrent allocation and lookup',
                          r.returncode == 0 and 'single thread: ok' in r.stdout and 'stress: 1500 ids from 6 threads' in r.stdout and
                          'first table: 3000 rounds' in r.stdout)
                except subprocess.TimeoutExpired:
                    check('the patched table code keeps its invariants (it did not finish in 300 s)', False)
            if built['original']:
                # informational: Wine's code has the race, so a run may fault (the Dying Light crash) or may not
                faults = 0
                runs = 20
                try:
                    for _ in range(runs):
                        r = subprocess.run([str(d / 'original'), '3000'], capture_output=True, text=True, timeout=120)
                        faults += r.returncode != 0
                    print('     | original (no lock, size before table): %d of %d runs of 3000 rounds faulted%s'
                          % (faults, runs, '' if faults else ' (a race: not every machine shows it)'))
                except subprocess.TimeoutExpired:
                    print('     | original: a run did not finish in 120 s (informational)')

    # ---- compile devinst.c for Windows: llvm-mingw's arm64ec compiler when it is here, else any clang that
    # can parse Wine's headers for aarch64 Windows (syntax only; Wine's own file is the baseline) ----------
    mingw = os.environ.get('MADEIRA_MINGW')
    cc_pe = None
    for candidate in ([Path(mingw)] if mingw else []) + sorted((root / 'toolchains').glob('llvm-mingw-*/bin')):
        if (candidate / 'arm64ec-w64-mingw32-clang').is_file():
            cc_pe = [str(candidate / 'arm64ec-w64-mingw32-clang'), '-D__STDC__', '-target', 'arm64ec-windows',
                     '--no-default-config']
            break
    if not cc_pe and shutil.which('clang'):
        cc_pe = [shutil.which('clang'), '-target', 'aarch64-pc-windows-gnu', '-Wno-builtin-macro-redefined']
    inc = wine / 'build-arm64ec/include'
    if cc_pe and inc.is_dir():
        with tempfile.TemporaryDirectory(prefix='madeira-setupapi-pe-') as directory:
            d = Path(directory)
            (d / 'patched.c').write_text(patched)
            (d / 'original.c').write_text(original)
            results = {}
            for name in ('original', 'patched'):
                try:
                    r = subprocess.run(cc_pe + ['-D__WINE_PE_BUILD', '-D_SETUPAPI_', '-D__WINESRC__', '-fno-strict-aliasing',
                                                '-fsyntax-only', '-Wall', '-Wdeclaration-after-statement',
                                                '-Wstrict-prototypes', '-Wvla', '-Wwrite-strings', '-Wpointer-arith',
                                                '-I', str(src_dir), '-I', str(wine / 'include'), '-I', str(inc),
                                                '-I', str(wine / 'include/msvcrt'), str(d / (name + '.c'))],
                                       capture_output=True, text=True, timeout=300)
                    results[name] = (r.returncode, r.stderr.count('warning:'), r.stderr)
                except subprocess.TimeoutExpired:
                    results[name] = None
            if results['patched'] is None or results['original'] is None:
                print('SKIP: the Windows-target compiler did not answer in 300 s')
            elif results['original'][0] != 0:
                print('SKIP: this compiler cannot parse Wine\'s own devinst.c (%s)' % results['original'][2].strip()[:200])
            else:
                check('devinst.c compiles for Windows (%s, -Wall, the flags of Wine\'s own build), with no more '
                      'warnings than Wine\'s own file (%d)' % (Path(cc_pe[0]).name, results['original'][1]),
                      results['patched'][0] == 0 and results['patched'][1] <= results['original'][1])
                if results['patched'][0]:
                    print(results['patched'][2][:3000])
    else:
        print('SKIP: no clang, or no configured Wine build (build-arm64ec/include) to compile with')

# ---- --verify on synthetic PEs ----------------------------------------------
def pe(names, extra=b''):
    """a PE32+ image with one section holding an export table of names"""
    sec_rva, sec_raw = 0x1000, 0x200
    strings = b''
    name_rvas = []
    base = sec_rva + 40 + 4 * len(names)
    for n in names:
        name_rvas.append(base + len(strings))
        strings += n.encode() + b'\0'
    exp = struct.pack('<IIHHIIIIIII', 0, 0, 0, 0, 0, 1, 0, len(names), 0, sec_rva + 40, 0)
    body_ = exp + b''.join(struct.pack('<I', r) for r in name_rvas) + strings + extra
    hdr = bytearray(sec_raw)
    hdr[0:2] = b'MZ'
    struct.pack_into('<I', hdr, 0x3c, 0x40)
    hdr[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', hdr, 0x44, 0x8664, 1, 0, 0, 0, 240, 0x2022)
    opt = 0x58
    struct.pack_into('<H', hdr, opt, 0x20b)
    struct.pack_into('<II', hdr, opt + 112, sec_rva, 40)
    s = opt + 240
    hdr[s:s + 8] = b'.rdata\0\0'
    struct.pack_into('<IIII', hdr, s + 8, len(body_), sec_rva, len(body_), sec_raw)
    return bytes(hdr) + body_


with tempfile.TemporaryDirectory(prefix='madeira-setupapi-pe-') as directory:
    d = Path(directory)
    (d / 'shipped.dll').write_bytes(pe(['SetupDiGetClassDevsW', 'CM_Get_Device_IDW']))
    (d / 'good.dll').write_bytes(pe(['SetupDiGetClassDevsW', 'CM_Get_Device_IDW'], dl.MARKER.encode()))
    (d / 'plain.dll').write_bytes(pe(['SetupDiGetClassDevsW', 'CM_Get_Device_IDW']))
    (d / 'other.dll').write_bytes(pe(['SetupDiGetClassDevsW'], dl.MARKER.encode()))

    def verify(name):
        return subprocess.run([sys.executable, str(script), '--verify', str(d / name), str(d / 'shipped.dll')],
                              capture_output=True, text=True).returncode

    check('--verify accepts the marker with the shipped exports', verify('good.dll') == 0)
    check('--verify refuses a DLL without the marker', verify('plain.dll') != 0)
    check('--verify refuses other exports', verify('other.dll') != 0)

# ---- the build step -----------------------------------------------------------
build = (root / 'tools/build-wine-extra-dlls.sh').read_text()
cond = build[build.index('SDL=""'):]
cond = cond[:cond.index('\nfi\n') + 4]
check('the variant is built only where devinst.c and a shipped setupapi.dll are, and MADEIRA_SETUPAPI_BUILD=0 skips it',
      '"${MADEIRA_SETUPAPI_BUILD:-1}" != 0' in cond and '[ -f "$R/wine/dlls/setupapi/devinst.c" ]' in cond and
      '[ -f "$SHIP/setupapi.dll" ]' in cond and 'SDL=setupapi' in cond)
guard = re.search(r'\[ -n "\$todo((?:\$[A-Z]+)+)" \] \|\| \{ echo "nothing to build"; exit 0; \}', build)   # the variables in any order (round 624 added ZMP and DCO)
check('the guard "nothing to build" counts it', bool(guard) and '$SDL' in re.findall(r'\$[A-Z]+', guard.group(1)))
sdl = build[build.index('if [ -n "$SDL" ]; then'):]
sdl = sdl[:sdl.index('\nfi\n') + 4]
check('the build patches, builds setupapi, strips like build-modules.sh, pads, verifies, then ships as setupapi-optin.dll',
      'patch-wine-setupapi-devinst-lock.py" "$R/wine"' in sdl and
      'make -C "$B" -j"$JOBS" dlls/setupapi/arm64ec-windows/setupapi.dll' in sdl and
      'arm64ec-w64-mingw32-strip" --strip-debug "$sdl_tmp"' in sdl and '+ 0x10000' in sdl and
      '--verify "$sdl_tmp" "$SHIP/setupapi.dll"' in sdl and
      'mv "$sdl_tmp" "$SHIP/setupapi-optin.dll"' in sdl and 'sdl_tmp="$SHIP/setupapi-optin.dll.tmp"' in sdl)
check('the shipped setupapi.dll is only ever read (the condition, --verify): never copied over, moved, stripped or removed',
      build.count('"$SHIP/setupapi.dll"') == 2 and not re.search(r'\b(cp|mv|rm|strip)\b[^\n]*\$SHIP/setupapi\.dll', build) and
      not re.search(r'>\s*"?\$SHIP/setupapi\.dll', build))
check('the build restores devinst.c and drops the patched objects',
      'git -C "$R/wine" checkout -- dlls/setupapi/devinst.c' in sdl and
      sdl.count('rm -f "$sa" "$B"/dlls/setupapi/arm64ec-windows/*.o') == 2)
check('a failed patch, build or check ships nothing and says so',
      sdl.count('::warning::') == 3 and 'rm -f "$sdl_tmp"' in sdl)
check('setupapi is not in the list of DLLs shipped under their own name, nor in the opt-in list the build skips shipped names of',
      not re.search(r'\bsetupapi\b', build[build.index('WANT="'):build.index('"\n', build.index('WANT="') + 6)]) and
      not re.search(r'\bsetupapi\b', re.search(r'OPTIN_WANT="([^"]*)"', build).group(1)))

# ---- the app: the existing opt-in link runs it ----------------------------------
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
blk = bridge[bridge.index('const char *optList = getenv("MADEIRA_OPTIN_DLLS");'):]
blk = blk[:blk.index('\n            }\n')]
farm = bridge.index('[fm removeItemAtPath:dst error:nil];  // self-heal stale links on reinstall')
check('the farms (system32 and sysx64) are relinked from the bundle every session BEFORE the opt-in links, '
      'so dropping the line brings the shipped setupapi.dll back at the next start',
      farm < bridge.index('const char *optList = getenv("MADEIRA_OPTIN_DLLS");') and
      '{ "sysx64",  "arm64ec-windows" }' in bridge and
      '[fm removeItemAtPath:dst error:nil];\n                if ([fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil])\n                    linked++;' in bridge)
check('a listed name is linked over its shipped name (setupapi-optin.dll -> setupapi.dll), in sysx64 and in system32 in an ARM64EC session',
      '[name isEqualToString:@"wer"] || [name isEqualToString:@"wintypes"]' in blk and '@"setupapi"' not in blk and
      'NSString *dll = [name stringByAppendingString:@".dll"];' in blk and
      'use_arm64ec\n                    ? @[ [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"], sys32Dir ]' in blk and
      'createSymbolicLinkAtPath:dst withDestinationPath:src' in blk)
check('a name that is not listed loses only a link to its -optin file: never the shipped setupapi.dll the farm just linked',
      '[to.lastPathComponent isEqualToString:file]' in blk and blk.count('removeItemAtPath:dst') == 2 and
      'destinationOfSymbolicLinkAtPath:dst' in blk)
check('it says what it linked', 'MADEIRA_OPTIN_DLLS: %s -> %s' in blk)

# ---- policy, catalog, recommendation, auto-tune, CI -----------------------------
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('RecommendationsPolicy lets a downloaded list name setupapi (and still only the shipped -optin names)',
      '["dxva2", "evr", "xpsprint", "sfc", "sfc_os", "tbs", "twinapi.appcore", "setupapi"].contains($0)' in policy and
      'may only name dxva2, evr, xpsprint, sfc, sfc_os, tbs, twinapi.appcore or setupapi' in policy)
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
note = gen[gen.index('"env.MADEIRA_OPTIN_DLLS": {'):]
note = note[:note.index('"env.MADEIRA_WINTYPES"')]
check('the catalog note names setupapi and says it is the one name shipped for every game',
      '(dxva2, evr, xpsprint, sfc, sfc_os, tbs, twinapi.appcore, setupapi)' in note and 'setupapi is the one name BCD already ships for every game' in note and
      'tools/patch-wine-setupapi-devinst-lock.py' in note)
recs = json.loads((root / 'recommended/recommendations.json').read_text())
dying = [r for r in recs['rules'] if r['id'] == 'dying-light']
check('Dying Light (Steam 239140) has a list with the line from build 577 on (577-593 setupapi, 594 on setupapi,ddraw), after the standard lines',
      len(dying) == 1 and dying[0]['match']['steamAppIDs'] == [239140] and
      dying[0]['config'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.LC_ALL = en_US.UTF-8',
                                 {'line': 'env.MADEIRA_OPTIN_DLLS = setupapi', 'minBuild': 577, 'maxBuild': 593},
                                 {'line': 'env.MADEIRA_OPTIN_DLLS = setupapi,ddraw', 'minBuild': 594}])
check('no other list names setupapi',
      all('setupapi' not in json.dumps(r) for r in recs['rules'] if r['id'] != 'dying-light'))
rules = (root / 'build/tools/autotune_rules.py').read_text()
i = rules.index('"id": "setupapi-devinst-race"')
rule = rules[i:rules.index('\n    },', i)]
locale = rules[rules.index('"id": "locale-en-us-ansi"'):]
locale = locale[:locale.index('\n    },')]
prio = lambda text: int(re.search(r'"priority": (\d+)', text).group(1))
check('auto-tune adds the line for a NULL fault in setupapi.dll with an "HID\\" id in [av-args], with no retry, '
      'before the locale experiment that also matches Dying Light, and not again once the line ran',
      '"lines": ["env.MADEIRA_OPTIN_DLLS = setupapi"]' in rule and '"noRetry": True' in rule and
      'pc PE: setupapi.dll+' in rule and 'NULL-class fault' in rule and 'HID' in rule and
      'MADEIRA_OPTIN_DLLS: setupapi.dll -> setupapi-optin.dll' in rule and prio(rule) < prio(locale) and
      '"outcomes": ["crashed"]' in rule)
fx = root / 'tests/fixtures/autotune'
check('a fixture of the log proves the rule', (fx / 'dying-light-2026-10-21.expect').is_file() and
      'setupapi-devinst-race' in (fx / 'dying-light-2026-10-21.expect').read_text())
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-setupapi-devinst-lock.py' in wf)
check('CI verifies the shipped setupapi-optin.dll against the shipped setupapi.dll when it is there',
      'python3 tools/patch-wine-setupapi-devinst-lock.py --verify app/Madeira/arm64ec-windows/setupapi-optin.dll' in wf and
      'app/Madeira/arm64ec-windows/setupapi.dll' in wf[wf.index('patch-wine-setupapi-devinst-lock.py --verify'):][:300])
check('.gitignore keeps the built DLL out of git',
      'app/Madeira/arm64ec-windows/setupapi-optin.dll' in (root / '.gitignore').read_text().split('\n'))

print('PASS: setupapi device node lock' if ok else 'FAIL: setupapi device node lock')
sys.exit(0 if ok else 1)
