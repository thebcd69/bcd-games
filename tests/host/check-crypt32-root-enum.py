#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Every process gets the root certificates, not only the first one (round 624, Discord forum item 2; OpenAL's installer 12157,
Persona 3 Reload "server not reachable").

On iOS one unix side serves every pseudo-process of a session. build/crypto-unix/crypt32_unixlib_ios.c's 64-bit enum_root_certs
handed out each root and removed it from the shared list (Wine's own behaviour, for one process): the first 64-bit process of a
session (Steam's Dock host, a launcher) took every root, a later one found none, and its TLS failed. The WoW64 path has enumerated
with a per-thread position since 2026-09-27; the 64-bit path now uses the same function (ios_enum_root_certs_cursor), so the list is
never consumed, and each finished walk logs how many roots it got ("[root-store]").

Checks: the function compiled on the host with the real pthread calls and driven from threads: one walk gets every root in order
(a small buffer gets the size and the same root again), a second thread later gets them all too, the list stays whole, an idle
walk restarts after 10 s, one line per finished walk; the source: both call tables use the cursor, nothing removes or frees a root.
"""
from pathlib import Path
import re, shutil, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/crypto-unix/crypt32_unixlib_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


enum64 = src[src.index('static NTSTATUS enum_root_certs( void *args )'):]
enum64 = enum64[:enum64.index('\n}\n') + 3]
check('the 64-bit call uses the per-thread cursor and removes nothing', 'return ios_enum_root_certs_cursor( args );' in enum64
      and 'list_remove' not in enum64 and 'free(' not in enum64)
check('the WoW64 call uses the same function', 'return ios_enum_root_certs_cursor( &params );' in src and 'ios_enum_root_certs_wow' not in src)
check('no root is removed or freed anywhere after the load', 'list_remove( &cert->entry )' not in src)
cur = src[src.index('#define IOS_ROOT_CURSORS 32'):]
cur = cur[:cur.index('\nconst unixlib_entry_t __wine_unix_call_funcs[]')]

HARNESS = r'''
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
typedef unsigned int DWORD; typedef unsigned char BYTE; typedef size_t SIZE_T; typedef long NTSTATUS;
#define STATUS_SUCCESS 0
#define STATUS_NO_MORE_ENTRIES ((NTSTATUS)0x8000001A)
#define ERR(...) printf(__VA_ARGS__)
struct list { struct list *next, *prev; };
#define LIST_FOR_EACH(cursor, list) for ((cursor) = (list)->next; (cursor) != (list); (cursor) = (cursor)->next)
#define LIST_ENTRY(elem, type, field) ((type *)((char *)(elem) - offsetof(type, field)))
#include <stddef.h>
struct root_cert { struct list entry; SIZE_T size; BYTE data[1]; };
struct enum_root_certs_params { void *buffer; DWORD size; DWORD *needed; };
static struct list root_cert_list = { &root_cert_list, &root_cert_list };
static pthread_mutex_t ios_root_lock = PTHREAD_MUTEX_INITIALIZER;
static int loaded;
static void ios_load_roots_locked(void) {
  if (loaded) return; loaded = 1;
  for (int i = 0; i < 5; i++) {
    struct root_cert *c = malloc(sizeof(*c) + 40); c->size = 10 + i;
    memset(c->data, 'A' + i, c->size);
    c->entry.prev = root_cert_list.prev; c->entry.next = &root_cert_list; root_cert_list.prev->next = &c->entry; root_cert_list.prev = &c->entry;
  }
}
static unsigned long long fake_ms = 100000;
@CURSOR@
static NTSTATUS one(BYTE *buf, DWORD size, DWORD *needed) { struct enum_root_certs_params p = { buf, size, needed }; return ios_enum_root_certs_cursor(&p); }
static void *walk(void *tag) {
  BYTE buf[64]; DWORD needed = 0; char got[16] = {0}; int n = 0;
  NTSTATUS st = one(buf, 4, &needed);          /* too small: the size, nothing copied */
  printf("%s small %ld %u\n", (char *)tag, (long)st, needed);
  while ((st = one(buf, sizeof buf, &needed)) == STATUS_SUCCESS) got[n++] = buf[0];
  printf("%s walk %s %d\n", (char *)tag, got, n);
  return 0;
}
int main(void) {
  pthread_t a, b;
  pthread_create(&a, 0, walk, "first"); pthread_join(a, 0);
  pthread_create(&b, 0, walk, "second"); pthread_join(b, 0);
  int len = 0; struct list *p; LIST_FOR_EACH(p, &root_cert_list) len++;
  printf("list %d\n", len);
  { BYTE buf[64]; DWORD needed; one(buf, sizeof buf, &needed); one(buf, sizeof buf, &needed);   /* this thread: two roots */
    fake_ms += 11000; one(buf, sizeof buf, &needed); printf("idle-restart %c\n", buf[0]); }
  return 0;
}
'''
cc = shutil.which('cc')
if cc:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        body = cur.replace('#include <pthread.h>', '').replace('#include <stdint.h>', '').replace('#include <time.h>', '')
        body = re.sub(r'static unsigned long long ios_root_now_ms\(void\)\n\{.*?\n\}\n', 'static unsigned long long ios_root_now_ms(void) { return fake_ms; }\n', body, flags=re.S)
        (t / 'h.c').write_text(HARNESS.replace('@CURSOR@', body))
        b = subprocess.run([cc, '-std=gnu11', '-w', '-pthread', '-o', str(t / 'h'), str(t / 'h.c')], capture_output=True, text=True)
        check('the enumeration compiles on the host (real pthreads)', b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            out = subprocess.run([str(t / 'h')], capture_output=True, text=True).stdout.splitlines()
            check('a too-small buffer gets the size and the same root again', 'first small 0 10' in out and 'second small 0 10' in out, out)
            check('the first walk gets every root in order; a later thread (another process) gets them all too',
                  'first walk ABCDE 5' in out and 'second walk ABCDE 5' in out, out)
            check('one [root-store] line per finished walk, and the shared list stays whole',
                  sum('[root-store] madeira-bcd: thread' in l and 'enumerated 5 root certificate(s), 5 in the shared list' in l for l in out) == 2
                  and 'list 5' in out, out)
            check('a walk idle for more than 10 s starts again from the first root', 'idle-restart A' in out, out)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-crypt32-root-enum.py' in wf)
print('PASS: crypt32 root enumeration' if ok else 'FAIL: crypt32 root enumeration')
raise SystemExit(0 if ok else 1)
