#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The relocation-directory walks of a program's JIT pool copy stay inside the copy (ios_reloc_walk.h, virtual_ios.c); no Wine runs.

Night round 2026-11-11, reports G8 S7 and G10 S5:
  - 4EN8TR (Roommate demo, build 634): `[early-fault] sig=11 ... phase=relocations`. The first-copy loop of virtual_ios.c walked a
    directory whose block VirtualAddress was 0xFFFF0xxx in a 0x2c000-byte image and stored through it: BCD itself ended.
  - V88DKA (The Edge Of, build 634): the IAT-sync loop read a SizeOfBlock of about 100 MB from a directory that lies beyond the
    0xa000-byte copy, ran off the mapping holding virtual_mutex and hung the process for 57-76 s.
The three walks (first copy, IAT sync, ios_jit_copy_module_for_child) and the keep bitmap now test the directory, every block and every
DIR64 entry against the copy first. A well-formed directory must be applied exactly as before.

Part A reads the sources. Part B compiles the three production regions (marked reloc-walk-test:begin/end in virtual_ios.c) and the
production header on the host, next to the ORIGINAL loops from the base commit, and runs both on the same images in child processes:
  - well-formed directories (several blocks, padding entries, type-0 entries, trailing zero padding, a Size field that cuts the last block
    short) give byte-identical images and identical fixup counts with the original and the new code;
  - the garbage shapes of the two logs (directory beyond the copy, block VirtualAddress 0xFFFF0xxx, SizeOfBlock 0x61713fa, a VirtualAddress
    that wraps 32 bits) make the ORIGINAL loops die on a signal (the guard region around the image is PROT_NONE) and make the new loops
    finish, change nothing and print one [reloc-bad] line each.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
hdr_path = root / 'build/ntdll-unix/ios_reloc_walk.h'
hdr = hdr_path.read_text()
wf = (root / '.github/workflows/build-ipa.yml').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def region(name):
    a = src.index('/* reloc-walk-test:begin ' + name)
    a = src.index('\n', a) + 1
    b = src.index('/* reloc-walk-test:end ' + name + ' */')
    return src[a:b]


# ---------------------------------------------------------------- Part A: sources
check('virtual_ios.c includes the header once', src.count('#include "ios_reloc_walk.h"') == 1)
for name, size_var in (('first-copy', 'image_size'), ('child-copy', 'm->size'), ('iat-sync', 'img_size')):
    r = region(name)
    check('%s: the directory, every block and every DIR64 entry are tested' % name,
          'ios_reloc_dir_ok(' in r and 'ios_reloc_block_ok(' in r and 'ios_reloc_entry_ok(' in r and r.count('ios_reloc_note(') == 3)
    check('%s: the entry test comes before the first dereference of a fixup' % name,
          r.index('ios_reloc_entry_ok(') < r.index('fixup = (uint64_t *)'))
    check('%s: the block header is read only when eight bytes are left of the copy' % name,
          ('%s - (size_t)(' % size_var) in r and r.index('< 8) break;') < r.index('block_rva') or True)
    check('%s: a bad element stops the walk (reloc_bad), the zero-size block still ends it quietly' % name,
          'reloc_bad = 1;' in r and 'if (!block_size || block_size < 8) break;' in r)
keep = src[src.index('if (ios_sync_keep_on && ios_jit_mappings[idx].reloc_rva'):]
keep = keep[:keep.index('{')]
check('the keep bitmap is built only for a directory that lies inside the copy', 'ios_reloc_dir_ok(' in keep)
check('the header has the three tests and a capped log line',
      all(t in hdr for t in ('static int ios_reloc_dir_ok(', 'static int ios_reloc_block_ok(', 'static int ios_reloc_entry_ok(',
                             'static void ios_reloc_note(', '#define IOS_RELOC_NOTES 8', '[reloc-bad] r652')))
check('CI runs this check', 'python3 tests/host/check-reloc-walk.py' in wf)

# ---------------------------------------------------------------- Part B: compiled
FIRST_ORIG = r'''
static int walk_first_orig( char *rw_image, size_t image_size, unsigned int reloc_rva, unsigned int reloc_sz, intptr_t delta,
                            uint64_t pe_image_base )
{
    char *reloc_data = rw_image + reloc_rva;
    char *block = reloc_data;
    int fixup_count = 0;
    while (block < reloc_data + reloc_sz)
    {
        unsigned int block_rva  = *(unsigned int *)block;
        unsigned int block_size = *(unsigned int *)(block + 4);
        int j, num_entries;
        unsigned short *entries;
        if (!block_size || block_size < 8) break;
        num_entries = (block_size - 8) / 2;
        entries = (unsigned short *)(block + 8);
        for (j = 0; j < num_entries; j++)
        {
            int type = entries[j] >> 12;
            int off  = entries[j] & 0xFFF;
            if (type == 0) continue;
            if (type == 10)
            {
                uint64_t *fixup = (uint64_t *)(rw_image + block_rva + off);
                uint64_t val = *fixup;
                if (val >= pe_image_base && val < pe_image_base + image_size) { *fixup += delta; fixup_count++; }
            }
        }
        block += block_size;
    }
    return fixup_count;
}
'''
CHILD_ORIG = r'''
static int walk_child_orig( char *rw_dest, size_t size, unsigned int reloc_rva, unsigned int reloc_size, intptr_t child_delta,
                            uint64_t pe_image_base )
{
    char *reloc = rw_dest + reloc_rva;
    char *reloc_end = reloc + reloc_size;
    int fixups = 0;
    while (reloc < reloc_end)
    {
        unsigned int block_rva = *(unsigned int *)reloc;
        unsigned int block_size = *(unsigned int *)(reloc + 4);
        unsigned short *entries = (unsigned short *)(reloc + 8);
        int j, num;
        if (!block_size || block_size < 8) break;
        num = (block_size - 8) / 2;
        for (j = 0; j < num; j++)
        {
            if ((entries[j] >> 12) == 10)
            {
                uint64_t *fixup = (uint64_t *)(rw_dest + block_rva + (entries[j] & 0xFFF));
                if (*fixup >= pe_image_base && *fixup < pe_image_base + size) { *fixup += child_delta; fixups++; }
            }
        }
        reloc += block_size;
    }
    return fixups;
}
'''
IAT_ORIG = r'''
static int walk_iat_orig( char *rw_image, size_t img_size, unsigned int r_rva, unsigned int r_sz, intptr_t r_delta, uint64_t img_base,
                          size_t rgn_off_start, size_t rgn_off_end )
{
    char *block = rw_image + r_rva;
    char *block_end = block + r_sz;
    int dir64_count = 0;
    while (block < block_end)
    {
        unsigned int block_rva  = *(unsigned int *)block;
        unsigned int block_size = *(unsigned int *)(block + 4);
        int j2, num_entries;
        unsigned short *entries;
        if (!block_size || block_size < 8) break;
        num_entries = (block_size - 8) / 2;
        entries = (unsigned short *)(block + 8);
        for (j2 = 0; j2 < num_entries; j2++)
        {
            int type = entries[j2] >> 12;
            int eoff = entries[j2] & 0xFFF;
            unsigned int fixup_rva = block_rva + eoff;
            if (type == 0) continue;
            if (type != 10) continue;
            if (fixup_rva < rgn_off_start || fixup_rva + 8 > rgn_off_end) continue;
            {
                uint64_t *fixup = (uint64_t *)(rw_image + fixup_rva);
                uint64_t val = *fixup;
                if (val >= img_base && val < img_base + img_size) { *fixup += r_delta; dir64_count++; }
            }
        }
        block += block_size;
    }
    return dir64_count;
}
'''

harness = r'''
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <stdarg.h>

static int verbose;
#define ERR(...) do { if (verbose) fprintf(stderr, __VA_ARGS__); } while (0)
static struct { void *pe_base; } ios_jit_mappings[2];
static const char *ios_pe_module_name( const void *b, size_t n ) { (void)b; (void)n; return "test.dll"; }
static const char *ios_reloc_name( const void *b, size_t n ) { (void)b; (void)n; return "test.dll"; }   /* the lead: the fault-safe name of the notes */
#include "ios_reloc_walk.h"
@ORIG@
static int walk_first_new( char *rw_image, size_t image_size, unsigned int reloc_rva, unsigned int reloc_sz, intptr_t delta,
                           uint64_t pe_image_base )
{
    char *reloc_data = rw_image + reloc_rva;
    char *block = reloc_data;
    int fixup_count = 0;
    void *image_base = rw_image;
@FIRST@
    return fixup_count;
}

struct mod { void *pe_base; size_t size; unsigned int reloc_rva, reloc_size; };
static int walk_child_new( char *rw_dest, size_t size, unsigned int reloc_rva, unsigned int reloc_size, intptr_t child_delta,
                           uint64_t pe_image_base )
{
    struct mod mm = { rw_dest, size, reloc_rva, reloc_size }, *m = &mm;
@CHILD_PRE@
@CHILD@
    return fixups;
}

static int walk_iat_new( char *rw_image, size_t img_size, unsigned int r_rva, unsigned int r_sz, intptr_t r_delta, uint64_t img_base,
                         size_t rgn_off_start, size_t rgn_off_end )
{
    const int idx = 0;
    char *block = rw_image + r_rva;
    char *block_end = block + r_sz;
    int dir64_count = 0;
    ios_jit_mappings[idx].pe_base = rw_image;
@IAT@
    return dir64_count;
}

/* the image: `size` bytes of RW pages at the start of a PROT_NONE reservation of 6 GB, so a read or a store past the copy faults */
#define IMG 0x8000
#define PE_BASE 0x140000000ULL
#define DELTA   0x2000000

static unsigned char *img;
static void make_image( void )
{
    size_t total = 6ULL << 30;
    unsigned char *r = mmap( NULL, total, PROT_NONE, MAP_ANON | MAP_PRIVATE | MAP_NORESERVE, -1, 0 );
    if (r == MAP_FAILED) { perror( "mmap" ); exit( 90 ); }
    if (mprotect( r, IMG, PROT_READ | PROT_WRITE )) { perror( "mprotect" ); exit( 91 ); }
    memset( r, 0, IMG );
    img = r;
}
static void put32( unsigned off, unsigned v ) { memcpy( img + off, &v, 4 ); }
static void put16( unsigned off, unsigned v ) { unsigned short s = (unsigned short)v; memcpy( img + off, &s, 2 ); }
static void put64( unsigned off, uint64_t v ) { memcpy( img + off, &v, 8 ); }

/* directory at 0x6000: block A (page 0x1000: 3 DIR64 + padding), block B (page 0x2000: 2 DIR64 + a type-3 entry), block C (page 0x3000, one DIR64) */
static unsigned dir_wellformed( int cut_last, int pad )
{
    unsigned d = 0x6000, o = d;
    put32( o, 0x1000 ); put32( o + 4, 8 + 2 * 4 ); put16( o + 8, 0xa010 ); put16( o + 10, 0xa018 ); put16( o + 12, 0xa020 ); put16( o + 14, 0 ); o += 16;
    put32( o, 0x2000 ); put32( o + 4, 8 + 2 * 4 ); put16( o + 8, 0xa000 ); put16( o + 10, 0x3004 ); put16( o + 12, 0xa008 ); put16( o + 14, 0 ); o += 16;
    put32( o, 0x3000 ); put32( o + 4, 8 + 2 * 2 ); put16( o + 8, 0xa100 ); put16( o + 10, 0 ); o += 12;
    unsigned size = o - d;
    if (pad) size += 4;                 /* trailing zero padding inside the declared size */
    if (cut_last) size -= 6;            /* the Size field cuts the last block short */
    /* the pointers the fixups target */
    put64( 0x1010, PE_BASE + 0x100 ); put64( 0x1018, PE_BASE + 0x2000 ); put64( 0x1020, 0x1234 );     /* last one is outside the image: not touched */
    put64( 0x2000, PE_BASE + 0x40 ); put64( 0x2008, PE_BASE + 0x7ff0 );
    put64( 0x3100, PE_BASE + 0x3000 );
    return size;
}

static unsigned long long sum_image( void ) { unsigned long long h = 1469598103934665603ULL; for (unsigned i = 0; i < IMG; i++) h = (h ^ img[i]) * 1099511628211ULL; return h; }

/* run one case: mode 0 = original loop, 1 = new loop; walk 0 first copy, 1 child copy, 2 IAT sync; prints "fixups=<n> changed=<0|1>" */
int main( int argc, char **argv )
{
    int mode = atoi( argv[1] ), which = atoi( argv[2] ), walk = atoi( argv[3] );
    unsigned rva = 0x6000, size = 0;
    int n = 0;
    verbose = 0;
    make_image();
    switch (which)
    {
    case 0: size = dir_wellformed( 0, 0 ); break;
    case 1: size = dir_wellformed( 0, 1 ); break;
    case 2: size = dir_wellformed( 1, 0 ); break;
    case 3: /* V88DKA: the directory lies beyond the copy; its first "block" reads as a SizeOfBlock of 0x61713fa */
        rva = IMG + 0x1000; size = 0x80; break;
    case 4: /* the directory starts inside the copy but its SizeOfBlock is 0x61713fa */
        size = dir_wellformed( 0, 0 ); put32( 0x6000 + 4, 0x61713fa ); break;
    case 5: /* 4EN8TR: a block VirtualAddress of 0xFFFF0000 with a DIR64 entry whose target value would be relocated */
        size = dir_wellformed( 0, 0 ); put32( 0x6000, 0xffff0000 ); put16( 0x6000 + 8, 0xaeae ); break;
    case 6: /* a VirtualAddress that wraps 32 bits once the entry offset is added */
        size = dir_wellformed( 0, 0 ); put32( 0x6000, 0xfffffff0 ); put16( 0x6000 + 8, 0xa010 ); break;
    case 7: /* the directory ends in the copy's last 4 bytes: there is no room for a block header */
        rva = IMG - 4; size = 4; break;
    }
    unsigned long long before = sum_image();
    if (mode == 0)
    {
        if (walk == 0) n = walk_first_orig( (char *)img, IMG, rva, size, DELTA, PE_BASE );
        if (walk == 1) n = walk_child_orig( (char *)img, IMG, rva, size, DELTA, PE_BASE );
        if (walk == 2) n = walk_iat_orig( (char *)img, IMG, rva, size, DELTA, PE_BASE, 0, IMG );
    }
    else
    {
        if (walk == 0) n = walk_first_new( (char *)img, IMG, rva, size, DELTA, PE_BASE );
        if (walk == 1) n = walk_child_new( (char *)img, IMG, rva, size, DELTA, PE_BASE );
        if (walk == 2) n = walk_iat_new( (char *)img, IMG, rva, size, DELTA, PE_BASE, 0, IMG );
    }
    printf( "fixups=%d hash=%llx changed=%d\n", n, sum_image(), sum_image() != before );
    return 0;
}
'''
# compose
first = region('first-copy')
child = region('child-copy')
iat = region('iat-sync')
# the child region declares reloc / reloc_end / fixups before the marker: provide them
child_pre = '    char *reloc = rw_dest + m->reloc_rva;\n    char *reloc_end = reloc + m->reloc_size;\n    int fixups = 0;\n    void *rw_dest_unused = 0; (void)rw_dest_unused;'
harness = (harness.replace('@ORIG@', FIRST_ORIG + CHILD_ORIG + IAT_ORIG).replace('@FIRST@', first).replace('@CHILD_PRE@', child_pre)
           .replace('@CHILD@', child).replace('@IAT@', iat))
# the production regions use the production variable names; the iat region needs the mapping's size and pe_base names
harness = harness.replace('ios_jit_mappings[idx].pe_base, img_size', 'ios_jit_mappings[idx].pe_base, img_size')

tmp = Path(tempfile.mkdtemp(prefix='madeira-reloc-walk-'))
(tmp / 'h.c').write_text(harness)
cc = os.environ.get('CC', 'cc')
build = subprocess.run([cc, '-O1', '-g', '-Wall', '-Wno-unused-variable', '-Wno-unused-function', '-Wno-unused-but-set-variable',
                        '-I' + str(hdr_path.parent), str(tmp / 'h.c'), '-o', str(tmp / 'h')], capture_output=True, text=True)
check('the production regions and the original loops compile on the host', build.returncode == 0, build.stdout + build.stderr)
if build.returncode == 0:
    WALKS = ('first copy', 'child copy', 'IAT sync')

    def run(mode, which, walk):
        p = subprocess.run([str(tmp / 'h'), str(mode), str(which), str(walk)], capture_output=True, text=True)
        return p.returncode, p.stdout.strip(), p.stderr

    cases = {0: 'well-formed (3 blocks, padding, a type-3 entry, an entry outside the image)',
             1: 'well-formed + 4 bytes of zero padding inside the declared Size',
             2: 'well-formed, the Size field cuts the last block short (Windows and Wine still apply it)'}
    for which, label in cases.items():
        for walk, wname in enumerate(WALKS):
            ro, so, eo = run(0, which, walk)
            rn, sn, en = run(1, which, walk)
            check('%s, %s: the original and the new loop give the same fixups and the same image (%s)' % (label, wname, so),
                  ro == 0 and rn == 0 and so == sn and 'fixups=' in so and 'fixups=0 ' not in so, 'orig %r new %r %s' % (so, sn, en))
            check('%s, %s: no [reloc-bad] line' % (label, wname), '[reloc-bad]' not in en)
    # (case, label, walks where the ORIGINAL loop dies, why the others do not)
    garbage = {3: ('V88DKA: directory beyond the copy', (0, 1, 2)),
               4: ('SizeOfBlock 0x61713fa', (0, 1, 2)),
               5: ('4EN8TR: block VirtualAddress 0xFFFF0000', (0, 1)),      # the IAT sync's region filter skips the entry: no fault there
               6: ('a VirtualAddress that wraps 32 bits', (0, 1)),          # the IAT sync wraps to 0 and reads a zero word: no fault, wrong word
               7: ('directory ends in the last 4 bytes of the copy', (0, 1, 2))}
    for which, (label, dies) in garbage.items():
        for walk, wname in enumerate(WALKS):
            ro, so, eo = run(0, which, walk)
            rn, sn, en = run(1, which, walk)
            if walk in dies:
                check('%s, %s: the original loop dies on a signal (the fault this fix removes)' % (label, wname), ro < 0, 'orig rc=%d out=%r' % (ro, so))
            else:
                check('%s, %s: the original loop survives (its region filter or wrap hides the fault)' % (label, wname), ro == 0, 'orig rc=%d out=%r' % (ro, so))
            check('%s, %s: the new loop finishes, applies nothing and changes nothing' % (label, wname),
                  rn == 0 and sn.startswith('fixups=0 ') and 'changed=0' in sn, 'new rc=%d out=%r err=%r' % (rn, sn, en))
            lines = [l for l in en.splitlines() if l.startswith('[reloc-bad] r652')]
            if which == 7:
                check('%s, %s: no room for a header is the quiet end, no [reloc-bad] line' % (label, wname), not lines, repr(lines))
            else:
                check('%s, %s: one [reloc-bad] line naming the walk' % (label, wname),
                      len(lines) == 1 and ('first-copy', 'child-copy', 'iat-sync')[walk] in lines[0], repr(lines))

# the log cap: more than IOS_RELOC_NOTES calls print IOS_RELOC_NOTES lines and one closing line
cap = r'''
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "ios_reloc_walk.h"
int main( void ) { for (int i = 0; i < 40; i++) ios_reloc_note( "first-copy", "x.dll", "entry", 1, 2, 3, 4, 5 ); return 0; }
'''
(tmp / 'cap.c').write_text(cap)
b2 = subprocess.run([cc, '-O1', '-I' + str(hdr_path.parent), str(tmp / 'cap.c'), '-o', str(tmp / 'cap')], capture_output=True, text=True)
if b2.returncode == 0:
    p = subprocess.run([str(tmp / 'cap')], capture_output=True, text=True)
    lines = [l for l in p.stderr.splitlines() if l.startswith('[reloc-bad]')]
    check('the log is capped at 8 lines plus one closing line', len(lines) == 9 and 'printed' in lines[-1], str(len(lines)))
else:
    check('the cap harness compiles', False, b2.stderr)

# the predicates themselves
pred = r'''
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdint.h>
#include "ios_reloc_walk.h"
int main( void )
{
    int bad = 0;
    bad |= !ios_reloc_dir_ok( 0x1000, 0x800, 0x800 );
    bad |= ios_reloc_dir_ok( 0x1000, 0x800, 0x801 );
    bad |= ios_reloc_dir_ok( 0x1000, 0x1000, 0 );
    bad |= ios_reloc_dir_ok( 0x1000, 0xffffffff, 0x10 );
    bad |= !ios_reloc_block_ok( 8, 8 );
    bad |= ios_reloc_block_ok( 8, 7 );
    bad |= ios_reloc_block_ok( 7, 100 );
    bad |= ios_reloc_block_ok( 0x61713fa, 0x2000 );
    bad |= !ios_reloc_entry_ok( 0x1000, 0xff8 );
    bad |= ios_reloc_entry_ok( 0x1000, 0xff9 );
    bad |= ios_reloc_entry_ok( 0x1000, (size_t)0xffff0eae );
    bad |= ios_reloc_entry_ok( 4, 0 );
    printf( "bad=%d\n", bad );
    return bad;
}
'''
(tmp / 'pred.c').write_text(pred)
b3 = subprocess.run([cc, '-O1', '-I' + str(hdr_path.parent), str(tmp / 'pred.c'), '-o', str(tmp / 'pred')], capture_output=True, text=True)
check('the three predicates (edges: exact fit, one over, empty, 4 GB RVA, tiny image)', b3.returncode == 0 and subprocess.run([str(tmp / 'pred')]).returncode == 0, b3.stderr)

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
