#!/usr/bin/env python3
"""totalphys-cap (round 631, agent R3 S3): an upper limit on the physical memory BCD reports.

RDR2 on 16 GB iPads (P8H75J, 91C75Y, NKSXWK, QKWFC3): told 15,389 MB, RDR2.exe reserves 7/8 of
32 GB = 28,672 MB in one block, which no address map here can place; told 8191 MB it reserves the
8960 MB its list keeps free (4DTB43 played 193 s). totalphys = N raises a smaller report too (a
6 GB iPhone would be told 8191 MB), the cap only lowers.

Compiles the production block (virtual_ios.c, between "sys-cap-begin" and "sys-cap-end") against a
stand-in madeira_cfg_int and checks: no cap or 0 leaves every report alone; a cap cuts a larger
report to the cap and leaves a smaller or equal one alone; the result never rises; the key is in
the settings catalog (a list line needs it) and the block sits after the jetsam clamp.
Needs python3 and a C compiler.
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
start = native.index('jetsam limit). sys-cap-begin */')
block = native[start + len('jetsam limit). sys-cap-begin */'):native.index('/* sys-cap-end */', start)]
clamp = native.index('info->MmHighestPhysicalPage = max( 1, cached_limit / page_size );')
assert clamp < start < native.index('#elif defined(_SC_PHYS_PAGES)', clamp), 'the cap must follow the jetsam clamp'
assert 'madeira_cfg_int( "totalphys-cap", 0 )' in block

program = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define dprintf(fd, ...) ((void)(fd), 0)
#define max(a, b) ((a) > (b) ? (a) : (b))
static long long cfg_value;
static long long madeira_cfg_int( const char *key, long long def ) { return strcmp( key, "totalphys-cap" ) ? def : cfg_value; }
struct info { unsigned long long MmHighestPhysicalPage; };
static unsigned long long run( long long cap, unsigned long long report_mb )
{
    struct info i = { (report_mb << 20) / 4096 }, *info = &i;
    unsigned long long page_size = 4096;
    cfg_value = cap;
''' + block.replace('static long long cap_mb = -1;', 'long long cap_mb = -1;').replace('static int cap_said;', 'int cap_said = 0;') + r'''
    return (info->MmHighestPhysicalPage * page_size) >> 20;
}
int main( void )
{
    unsigned long long reports[] = { 3072, 4095, 6143, 7455, 8191, 8192, 11980, 15389, 15416 };
    long long caps[] = { 0, -5, 4096, 8191, 12000 };
    for (unsigned c = 0; c < sizeof(caps) / sizeof(caps[0]); c++)
        for (unsigned r = 0; r < sizeof(reports) / sizeof(reports[0]); r++)
        {
            unsigned long long got = run( caps[c], reports[r] );
            unsigned long long want = (caps[c] > 0 && reports[r] > (unsigned long long)caps[c]) ? (unsigned long long)caps[c] : reports[r];
            if (got != want || got > reports[r])
            {
                printf( "cap %lld report %llu -> %llu, want %llu\n", caps[c], reports[r], got, want );
                return 1;
            }
        }
    puts( "ok" );
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='totalphys-cap-') as tmp:
    src, exe = Path(tmp) / 'cap.c', Path(tmp) / 'cap'
    src.write_text(program)
    r = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-fsanitize=address,undefined', '-o', str(exe), str(src)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == 'ok', out.stdout + out.stderr

catalog = (root / 'build/tools/gen-config-catalog.py').read_text()
assert '"totalphys-cap": {"category": "Memory & JIT pool", "kind": "int"' in catalog
assert 'key: "totalphys-cap"' in (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
print('totalphys-cap: production block compiled: no cap = unchanged, a cap only lowers (15,389 -> 8191, 6143 stays); '
      'after the jetsam clamp; in the settings catalog (game setting a list may set)')
