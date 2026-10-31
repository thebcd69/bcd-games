#!/usr/bin/env python3
"""[fault-rgn] neighbour lines, the small-view history and a provenance line that knows its view is gone (virtual_ios.c); no Wine runs.

Batman: Arkham City GOTY (Steam 200260, build 576, iPad16,5) died reading 0x790f080000. The report said "NO wine view" and "no view of
1 MB+ covered this address", and its PROVENANCE line described a 0x11000-byte file view at 0x790f070000 ("backing covers the mapping;
EOF is NOT the explanation") that the thread had unmapped 67 times by then: the page belongs to no view, the host region
(0x790f080000+0x4000, prot 0, SM_PRIVATE, never resident) is the free hole after the 0xf070000 block, and Wine's own vprot of the page before it
(0x23: committed) says a live view ends right there. What stood next to the fault could not be read from the log.

  - ios_dump_fault_region names the view BELOW (the one holding the byte before the fault: its base, size, end, protect, kind, and whether
    the fault is exactly one past its end) and the view ABOVE (first view in the next 1 MB), then the 1 MB+ history as before, then
    the history of the views under 1 MB around the fault (ios_vhs_*: guest-band views, a ring of 2048, newest first);
  - ios_map_record's ring marks a record "unmapped" when delete_view deletes a file view of that base and size, and ios_map_describe says
    so instead of calling the dead view's file the owner of the page.
Failure path only (the lines print on a fault Wine cannot serve; the rings store a few words per view created or deleted).

Source checks (every host) hold the shape; the ring and the provenance functions are then compiled and run against fakes.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


vhs = source[source.index('struct ios_vhs_rec\n{'):source.index('static void ios_vh_note( char kind, const struct file_view *view )')]
note_fn = function('static void ios_vh_note( char kind, const struct file_view *view )')
delete_fn = function('static void delete_view( struct file_view *view )')
# round 624 (agents E1 S17, D2 S8): the lines moved into ios_fault_region_lines, shared by ios_dump_fault_region (the storm gate and the lock,
# as before) and the 32-bit fault report's variant (the lock only tried)
dump_fn = function('static void ios_fault_region_lines( void *addr )\n{')
dump_wrap = function('void ios_dump_fault_region( void *addr )\n{')
ring = source[source.index('#define IOS_MAP_RING 256'):source.index('static NTSTATUS map_file_into_view_ex( struct file_view *view, int fd, size_t start, size_t size,\n                                       off_t offset, unsigned int vprot, BOOL removable,\n                                       BOOL for_image )\n{')]

# ---- source shape --------------------------------------------------------------------------------------------------
check('the small-view ring is separate from the 1 MB ring and holds 2048 records', '#define IOS_VHS_N 2048' in vhs and 'static struct ios_vhs_rec ios_vhs_ring[IOS_VHS_N];' in vhs)
check('only guest-band views under 1 MB go into it, before the 1 MB test returns, and the 1 MB path is unchanged',
      'if ((uintptr_t)view->base >= 0x7000000000ULL && (uintptr_t)view->base < 0x7c00000000ULL && view->size < (1u << 20))\n        ios_vhs_note( kind, view );' in note_fn
      and note_fn.index('ios_vhs_note( kind, view );') < note_fn.index('if (view->size < (1u << 20) ||')
      and 'r = &ios_vh_ring[__atomic_fetch_add( &ios_vh_pos, 1, __ATOMIC_RELAXED ) % IOS_VH_N];' in note_fn)
check('the ring records only; its dump is the only place that prints', vhs.count('dprintf(') == 2 and 'mprotect' not in vhs and 'mmap(' not in vhs)
check('delete_view marks a deleted file view in the provenance ring, and only a non-VirtualAlloc one',
      'if (!is_view_valloc( view )) ios_map_note_unmapped( view->base, view->size );' in delete_fn
      and 'static void ios_map_note_unmapped( const void *base, size_t size );' in source[:source.index('static void delete_view( struct file_view *view )')])
check('the fault report names the views below and above before the histories, inside the "no Wine view" branch',
      dump_fn.index('NO wine view') < dump_fn.index('below = find_view( (char *)addr - 1, 1 );') < dump_fn.index('above = find_view_range( addr, 0x100000 );')
      < dump_fn.index('ios_vh_dump( addr );') < dump_fn.index('ios_vhs_dump( addr, 0x40000, 10 );')
      and dump_wrap.index('if (!ios_storm_gate( &fr_storm )) return;') < dump_wrap.index('mutex_lock( &virtual_mutex );')
      < dump_wrap.index('ios_fault_region_lines( addr );') < dump_wrap.index('mutex_unlock( &virtual_mutex );'))
check('the neighbour lines say whether the fault is exactly one past the end of the view below', 'the fault is exactly one past its end' in dump_fn)
check('the provenance ring has the flag, a new record clears it, and the description says the view is gone',
      'int unmapped;' in ring and 'ios_map_ring[slot].unmapped = 0;' in ring and 'THAT VIEW IS GONE (unmapped since)' in ring)

# ---- compiled helpers against fakes --------------------------------------------------------------------------------
cc = os.environ.get('CC', 'cc')
HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <time.h>
typedef unsigned long ULONG_PTR_T;
#define ULONG_PTR ULONG_PTR_T
struct file_view { char *base; size_t size; unsigned int protect; };
struct fake_cid { void *UniqueThread; };
struct fake_teb { struct fake_cid ClientId; };
static struct fake_teb the_teb = { { (void *)0x1a4 } };
#define NtCurrentTeb() (&the_teb)
static unsigned int fake_ms = 1000;
static unsigned int ios_vh_ms( void ) { return fake_ms; }
''' + vhs + r'''
''' + ring + r'''
static void show(const char *tag, const void *a)
{
    char buf[420];
    ios_map_describe( a, buf, sizeof(buf) );
    printf("%s: %s\n", tag, buf);
}
int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    if (!strcmp(s, "vhs")) {
        struct file_view v1 = { (char *)0x790f070000UL, 0x11000, 0x0 }, v2 = { (char *)0x790f070000UL, 0x10000, 0x23 },
                         far_ = { (char *)0x7910000000UL, 0x1000, 0x23 };
        fake_ms = 1000; ios_vhs_note('c', &v1);
        fake_ms = 1010; ios_vhs_note('d', &v1);
        fake_ms = 1020; ios_vhs_note('c', &far_);
        fake_ms = 1030; ios_vhs_note('c', &v2);
        fake_ms = 1500;
        printf("n=%u\n", ios_vhs_dump((void *)0x790f080000UL, 0x40000, 10));
        printf("n2=%u\n", ios_vhs_dump((void *)0x790f080000UL, 0x40000, 2));
        printf("n3=%u\n", ios_vhs_dump((void *)0x7800000000UL, 0x40000, 10));
        for (int i = 0; i < 5000; i++) { v1.base += 0x20000; ios_vhs_note('c', &v1); }     /* the ring wraps: the old records are gone */
        printf("n4=%u\n", ios_vhs_dump((void *)0x790f080000UL, 0x40000, 10));
    } else if (!strcmp(s, "ring")) {
        char path[] = "/tmp/vfaultXXXXXX";
        int fd = mkstemp(path);
        assert(fd >= 0 && ftruncate(fd, 0x11000) == 0);
        unlink(path);
        ios_map_record((void *)0x790f070000UL, 0x11000, 0, 0x11000, 0x11000, 0x11000, fd, 0, 1, 0);
        show("live", (void *)0x790f080000UL);
        ios_map_note_unmapped((void *)0x790f070000UL, 0x10000);          /* another size: not this view */
        show("other-size", (void *)0x790f080000UL);
        ios_map_note_unmapped((void *)0x790f070000UL, 0x11000);
        show("gone", (void *)0x790f080000UL);
        ios_map_record((void *)0x790f070000UL, 0x11000, 0, 0x11000, 0x11000, 0x11000, fd, 0, 1, 0);   /* mapped again: live again */
        show("again", (void *)0x790f080000UL);
        show("none", (void *)0x790f200000UL);
    }
    return 0;
}
'''

if sys.platform != 'darwin' or not shutil.which(cc):
    print('skip the compiled runs: macOS and a C compiler are needed')
else:
    with tempfile.TemporaryDirectory(prefix='faultnb-') as d:
        d = Path(d)
        (d / 'h.c').write_text(HARNESS)
        subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
                        '-Wno-unused-variable', str(d / 'h.c'), '-o', str(d / 'h')], check=True)

        def run(scenario):
            return subprocess.run([str(d / 'h'), scenario], capture_output=True, text=True)

        r = run('vhs')
        out = dict(l.split('=') for l in r.stdout.split() if '=' in l)
        lines = [l for l in r.stderr.splitlines() if 'small-view history' in l]
        check('the Batman neighbourhood: the three views within 0x40000 print, newest first, the far view does not',
              out.get('n') == '3' and len(lines) >= 3 and 'view 0x790f070000+0x10000 (ends 0x790f080000) protect=0x23 CREATED by tid 01a4 470 ms ago' in lines[0]
              and 'view 0x790f070000+0x11000 (ends 0x790f081000) protect=0x0 DELETED by tid 01a4 490 ms ago' in lines[1]
              and 'view 0x790f070000+0x11000 (ends 0x790f081000) protect=0x0 CREATED by tid 01a4 500 ms ago' in lines[2]
              and '0x7910000000' not in ''.join(lines[:3]), r.stderr)
        check('the budget caps the lines', out.get('n2') == '2', r.stdout)
        check('nothing near an address says so (and prints one line)', out.get('n3') == '0' and 'no view under 1 MB within 0x40000 of this address in the last 2048 records' in r.stderr, r.stderr)
        check('after 5000 more views the old records have been overwritten', out.get('n4') == '0', r.stdout)

        r = run('ring')
        lines = dict(l.split(': ', 1) for l in r.stdout.splitlines())
        check('a live file view: described as before ("backing covers the mapping")',
              'view=0x790f070000+0x11000' in lines['live'] and 'file_size=0x11000' in lines['live'] and 'EOF is NOT the explanation' in lines['live'] and 'GONE' not in lines['live'], lines['live'])
        check('a delete of another size does not mark it', 'GONE' not in lines['other-size'], lines['other-size'])
        check('after the delete: the same record, now said to be a view that is gone',
              'view=0x790f070000+0x11000' in lines['gone'] and 'THAT VIEW IS GONE (unmapped since)' in lines['gone'] and 'EOF is NOT the explanation' not in lines['gone'], lines['gone'])
        check('mapped again: the new record is live', 'GONE' not in lines['again'] and 'EOF is NOT the explanation' in lines['again'], lines['again'])
        check('an address no file mapping covers: still the plain answer', 'NO file mapping covers this address' in lines['none'], lines['none'])

sys.exit(0 if ok else 1)
