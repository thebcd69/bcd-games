#!/usr/bin/env python3
"""[x64-image]: pure-x64 images get no JIT-pool copy (MADEIRA_X64_IMAGE_NOCOPY).

GTA V Enhanced build 422 (2026-10-07 07:59): 454 MiB of the 889 MiB pool span
were copies of pure-x64 images (libcef, the game exe, steamclient64, Launcher
...) that the emulator never executes from -- FEX compiles x64 at the PE VA and
redirects a pool-copy RIP back to it ([pool-rip-fix]) -- and the game died at
its third steamclient64.dll copy. Behind the switch, map_image_into_view marks
such a view VPROT_X64DATA and mprotect_exec drops PROT_EXEC for it, as ml1030
already does for i386 image pages, so no copy is made and Wine still records
VPROT_EXEC.

Checks, without a Wine run:
  - the view flag is a fresh bit;
  - the mark is set in map_image_into_view after update_arm64ec_ranges (a
    hybrid image carries VPROT_ARM64EC by then) and before the section
    protections and the eager copy loop, and only for machine AMD64 / PE32+ /
    not hybrid / not builtin / not a resource-only map / not WoW64;
  - mprotect_exec consults the mark after the ml1030 block and before the
    resource-only refusal and the JIT-pool arm, clears PROT_EXEC and never sets
    ios_jit_copy_refused;
  - the switch is exactly "1" (compiled and run against a stub getenv);
  - the catalog overlay documents the key; the CI workflow runs this check.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


# --- the view flag ------------------------------------------------------------
m = re.search(r'#define VPROT_X64DATA\s+(0x[0-9a-fA-F]+)', native)
assert m, 'VPROT_X64DATA defined'
flag = int(m.group(1), 16)
others = {int(v, 16) for n, v in re.findall(r'#define (VPROT_\w+)\s+(0x[0-9a-fA-F]+)', native) if n != 'VPROT_X64DATA'}
assert flag not in others and flag & (flag - 1) == 0, hex(flag)
assert flag > 0x0800, 'above the per-mapping flags that exist'

# --- the mark in map_image_into_view ------------------------------------------
mapper = function(native, 'static NTSTATUS map_image_into_view(')
mark = mapper.index('view->protect |= VPROT_X64DATA;')
# round 625 (owner's item 22, agent E1 S14-b): one guard for both marks; inside it the switch, else the image that can never fit
cond_start = mapper.rindex('if (!ios_map_resource_view && !ios_wow_base() &&', 0, mark)
cond = mapper[cond_start:mark]
branch = mapper[cond_start:mapper.index('#endif', cond_start)]
assert branch.count('view->protect |= VPROT_X64DATA;') == 2, 'two marks under the one guard'
assert branch.index('if (ios_x64_image_nocopy_enabled())') < branch.index('else if (ios_x64_image_copy_can_never_fit( total_size ))'), \
    'the switch first, then the fallback'
assert 'NOCOPY fallback; MADEIRA_X64_IMAGE_NOCOPY_AUTO=0 turns it off' in branch, 'the fallback says so in its [x64-image] line'
for need in ('!ios_map_resource_view', '!ios_wow_base()', 'nt->FileHeader.Machine == IMAGE_FILE_MACHINE_AMD64',
             'nt->OptionalHeader.Magic == IMAGE_NT_OPTIONAL_HDR64_MAGIC', '!(view->protect & VPROT_ARM64EC)',
             '!image_info->is_hybrid', '!image_info->wine_builtin'):
    assert need in cond, need
assert mapper.index('update_arm64ec_ranges( view, nt, dir, &image_info->entry_point );') < mark, 'after the EC ranges'
assert mapper.index('ios_sc_render_handler_patch( ptr, total_size, nt, sec, nt_name );') < mark, 'after [sc-rph]'
# (the comment "set the image protections" appears twice in the file; the header set_vprot is unique)
assert mark < mapper.index('set_vprot( view, ptr, ROUND_SIZE( 0, header_size, align_mask ), VPROT_COMMITTED | VPROT_READ );'), \
    'before the section protections'
assert mark < mapper.index('mprotect_exec(sec_addr, sec_size, prot);'), 'before the eager copy loop'
assert '[x64-image]' in mapper[mark:mark + 600], 'names the image'
print('PASS: the mark is set after the EC ranges, before the protections, with the full guard')

# --- mprotect_exec ------------------------------------------------------------
prot = function(native, 'static inline int mprotect_exec(')
x64 = prot.index('ios_x64_image_is_host_data( base, size )')
assert prot.index('ios_guest_image_is_host_data( base, size )') < x64, 'after the ml1030 block'
assert x64 < prot.index('(unix_prot & PROT_EXEC) && ios_map_resource_view'), 'before the resource-only refusal'
assert x64 < prot.index('if (unix_prot & PROT_EXEC)\n'), 'before the JIT-pool arm'
block = prot[x64:prot.index('ios_map_resource_view', x64)]
assert 'unix_prot &= ~PROT_EXEC;' in block and 'if (!unix_prot) unix_prot = PROT_READ;' in block
assert 'ios_jit_copy_refused' not in block, 'a dropped bit is not a refusal'
assert '!ios_in_mach_exc' in block, 'silent on the Mach exception thread (ml374)'

helper = function(native, 'static int ios_x64_image_is_host_data(')
# round 625: the view's mark alone decides (only the switch or the fallback sets it), so a fallback image drops EXEC like a switched one
assert 'ios_x64_image_nocopy_enabled()' not in helper
# build 423: a host-page rounded request for the last section runs past a 4 KB
# aligned SizeOfImage; the view must be found by its start, not by the range
assert 'find_view( base, 0 )' in helper and 'find_view( base, size )' not in helper
assert 'view->protect & VPROT_X64DATA' in helper and 'ios_x64_image_view_covers(' in helper
print('PASS: mprotect_exec drops EXEC for a marked view and copies nothing')

# --- the host-page tail, compiled ---------------------------------------------
covers = native[native.index('#define IOS_X64_IMAGE_HOST_PAGE'):]
covers = covers[:covers.index('\n}', covers.index('static int ios_x64_image_view_covers(')) + 2] + '\n'
tail_prog = r'''
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
''' + covers + r'''
int main(void)
{
    const uintptr_t vb = 0x72d4840000u;      /* GTA5_Enhanced.exe, build 423 */
    const size_t vs = 0x5b46000u;            /* SizeOfImage, 4 KB aligned */
    int bad = 0;
#define T(b, s, want) do { int got = ios_x64_image_view_covers( vb, vs, (b), (s) ); \
        if (got != (want)) { printf("FAIL %#lx+%#lx -> %d\n", (unsigned long)(b), (unsigned long)(s), got); bad = 1; } } while (0)
    T( 0x72d9964000u, 0xa24000u, 1 );        /* the logged last-section request, 8 KB past the view */
    T( 0x72d4840000u, 0x4000u, 1 );          /* the header page */
    T( 0x72d4841000u, 0x2479400u, 1 );       /* a section inside */
    T( 0x72da384000u, 0x4000u, 1 );          /* the last host page alone */
    T( 0x72da384000u, 0x8000u, 0 );          /* one host page past the view's last one */
    T( 0x72da386000u, 0x2000u, 0 );          /* starts at the view's end */
    T( 0x72d483c000u, 0x8000u, 0 );          /* starts below the view */
    T( 0x72d9964000u, (size_t)-1, 0 );       /* overflow */
    return bad;
}
'''
with tempfile.TemporaryDirectory() as d:
    src = Path(d) / 'tail.c'
    src.write_text(tail_prog)
    exe = Path(d) / 'tail'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=undefined', '-o', str(exe), str(src)],
                   check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
print('PASS: a last-section request that ends inside the view\'s last 16 KB host page counts as the view')

# --- the switch, compiled -----------------------------------------------------
enabled = function(native, 'static int ios_x64_image_nocopy_enabled(')
prog = r'''
#include <stdio.h>
#include <string.h>
static const char *fake;
static const char *getenv(const char *n) { (void)n; return fake; }
''' + enabled + r'''
int main(void)
{
    const char *cases[] = { "1", "0", "10", "", NULL, "yes" };
    int want[] = { 1, 0, 0, 0, 0, 0 }, i, rc = 0;
    /* the function caches its answer, so run it once per process */
    (void)cases; (void)want; (void)i;
    fake = CASE;
    printf("%d\n", ios_x64_image_nocopy_enabled());
    return rc;
}
'''
with tempfile.TemporaryDirectory() as d:
    for case, want in (('"1"', 1), ('"0"', 0), ('"10"', 0), ('""', 0), ('NULL', 0), ('"yes"', 0)):
        src = Path(d) / 'sw.c'
        src.write_text(prog.replace('CASE', case))
        exe = Path(d) / 'sw'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable', '-o', str(exe), str(src)], check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.strip()
        assert out == str(want), (case, out)
print('PASS: MADEIRA_X64_IMAGE_NOCOPY is on for exactly "1"')

# --- round 625: the fallback, compiled ---------------------------------------------
never = function(native, 'static int ios_x64_image_copy_can_never_fit(')
nprog = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static size_t ios_jit_pool_size_global;
static const char *fake_auto, *fake_size;
static const char *fake_getenv(const char *n) { return !strcmp(n, "MADEIRA_X64_IMAGE_NOCOPY_AUTO") ? fake_auto : !strcmp(n, "WINE_IOS_JIT_SIZE") ? fake_size : NULL; }
#define getenv fake_getenv
''' + never + r'''
int main(void)
{
    fake_auto = @AUTO@; fake_size = @SIZE@; ios_jit_pool_size_global = @POOL@;
    printf("%d %d %d %d\n", ios_x64_image_copy_can_never_fit( 0x241c0000u ), ios_x64_image_copy_can_never_fit( 0x35735000u ),
           ios_x64_image_copy_can_never_fit( 0x23000000u ), ios_x64_image_copy_can_never_fit( 0x1c000000u ));
    return 0;
}
'''
with tempfile.TemporaryDirectory() as d:
    # (MADEIRA_X64_IMAGE_NOCOPY_AUTO, WINE_IOS_JIT_SIZE, the published pool) -> mgs4.exe 578 MB, Slay the Spire 2 853 MB, the pool's own size, 448 MB
    for auto, size, pool, want in (('NULL', 'NULL', '0x23000000u', '1 1 0 0'),        # 560 MB pool: only what is larger than the whole pool
                                   ('NULL', '"23000000"', '0', '1 1 0 0'),             # before the pool's first use: the app's size
                                   ('NULL', 'NULL', '0x27000000u', '0 1 0 0'),         # a 624 MB pool holds mgs4.exe: it keeps its copy
                                   ('"0"', 'NULL', '0x23000000u', '0 0 0 0'),          # turned off
                                   ('"1"', 'NULL', '0x23000000u', '1 1 0 0'),          # any other value: on
                                   ('NULL', 'NULL', '0', '0 0 0 0')):                  # no pool known: nothing
        src = Path(d) / 'nf.c'
        src.write_text(nprog.replace('@AUTO@', auto).replace('@SIZE@', size).replace('@POOL@', pool))
        exe = Path(d) / 'nf'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-o', str(exe), str(src)], check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.strip()
        assert out == want, (auto, size, pool, out)
print('PASS: an image larger than the whole JIT pool runs from its file (round 625); every image an empty pool could hold keeps its copy')

# --- catalog and CI -----------------------------------------------------------
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
assert '"env.MADEIRA_X64_IMAGE_NOCOPY"' in gen, 'catalog overlay'
assert '"env.MADEIRA_X64_IMAGE_NOCOPY_AUTO"' in gen, 'catalog overlay of the round 625 fallback switch'
swift = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
assert 'env.MADEIRA_X64_IMAGE_NOCOPY' in swift, 'generated catalog carries the key'
yml = (root / '.github/workflows/build-ipa.yml').read_text()
assert 'tests/host/check-x64-image-nocopy.py' in yml, 'CI gate'
print('PASS: [x64-image] pure-x64 images run from their PE view without a pool copy, behind MADEIRA_X64_IMAGE_NOCOPY')
