#!/usr/bin/env python3
"""The stale-pointer heal translates each copy for the process that uses it; no Wine runs.

GTA V Enhanced, build 327 (PlayGTAV.exe 2026-10-02 11:50:19, line 20305):
`[stale-heal] 0x71ffd31508 -> 0x148441508, rewrote 2 slot(s)`. 0x71ffd31508 is
ntdll's KiUserExceptionDispatcher (PE), which every emulator jumps to for each
x64 syscall and exception. One of the two slots was the child emulator's
(libarm64ecfex.dll, a copy with owner NULL); it was rewritten to the SESSION's
ntdll copy, so the child's next syscall ran the parent's ntdll and handed its
emulator the parent's invoke_arm64ec_syscall (0x148467050) -> NoExec (20421).

Compiles the production ios_jit_patch_stale_pointer (build/ntdll-unix/
virtual_ios.c) with its helpers against a model of the mapping table and pool:
session ntdll copy, a child's private ntdll copy, the parent's and the child's
emulator copies (owner NULL, mapped by their own process), an image of unknown
mapper. Checks:
  - default: the parent's emulator slot -> session copy, the child's emulator
    slot -> the CHILD's ntdll copy, the unknown-mapper slot left alone (and
    named), the child's own ntdll copy -> itself;
  - MADEIRA_HEAL_OWNER=0: the old rule (owner_peb only): the child's emulator
    slot goes to the session copy -- the build 327 crash;
  - a target image without per-process copies heals everywhere as before;
  - ios_jit_add_mapping records the mapping process, the child copy its owner.
Needs python3 and a C compiler (AddressSanitizer/UBSan when available).
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


def between(source, first, last):
    start = source.index(first)
    return source[start:source.index(last, start) + len(last)] + '\n'


add = function(native, 'void ios_jit_add_mapping(void *pe_base, void *jit_base, size_t size)')
assert 'ios_jit_mappings[slot].map_peb = ios_jit_current_peb();' in add
child_copy = native[native.index('ios_jit_mappings[slot].owner_peb = child_peb;'):][:200]
assert 'ios_jit_mappings[slot].map_peb = child_peb;' in child_copy

code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
void *ios_jit_rx_base_global, *ios_jit_rw_base_global;
static int dprintf_quiet;
const char *ios_pe_module_name( const void *image_base, size_t image_size )
{ (void)image_base; (void)image_size; return "model.dll"; }
/* night round 2026-11-11 (R1 S8): the diagnostics read the name through the kernel-checked reader (tests/host/check-pe-module-name-safe.py runs the real one) */
static const char *ios_pe_module_name_safe( const void *image_base, size_t image_size, char *out, size_t out_size )
{ (void)out; (void)out_size; return ios_pe_module_name( image_base, image_size ); }
'''
code += between(native, '#define IOS_JIT_MAX_MAPPINGS', '\n};')
code += 'static struct ios_jit_mapping ios_jit_mappings[IOS_JIT_MAX_MAPPINGS];\n'
code += 'static int ios_jit_mapping_count = 0;\n'
code += function(native, 'void *ios_jit_translate_addr_for_owner(void *addr, void *owner_peb)')
code += between(native, 'struct ios_iat_range { unsigned int rva, size; };', '\n    return n;\n}')
code += between(native, 'static int ios_heal_owner_aware( void )', 'return !ios_va_has_owned_copy( stale_va );\n}')
code += function(native, 'int ios_jit_patch_stale_pointer(unsigned long long stale_va)')
code += r'''
#define NT      0x71ffcd0000ull
#define NT_SZ   0x130000ull
#define KIUED   (NT + 0x61508)
#define FEXP    0x71fe8c0000ull
#define FEXC    0x71fcdb0000ull
#define UNK     0x71f0000000ull
#define OTHER   0x71e0000000ull     /* an image nobody copies per process */
#define IMG_SZ  0x40000ull
#define MAIN    ((void *)0x71ffff0000ull)
#define CHILD   ((void *)0x10a238000ull)
static unsigned char *pool;
static void map( int i, uint64_t pe, size_t off, size_t sz, void *owner, void *mapper )
{
    ios_jit_mappings[i].pe_base = (void *)pe;
    ios_jit_mappings[i].jit_base = pool + off;
    ios_jit_mappings[i].size = sz;
    ios_jit_mappings[i].owner_peb = owner;
    ios_jit_mappings[i].map_peb = mapper;
    if (i >= ios_jit_mapping_count) ios_jit_mapping_count = i + 1;
}
static uint64_t *slot( int i ) { return (uint64_t *)((unsigned char *)ios_jit_mappings[i].jit_base + 0x2000); }

int main( int argc, char **argv )
{
    const int legacy = argc > 1 && !strcmp( argv[1], "legacy" );
    uint64_t session_ki, child_ki;
    pool = calloc( 1, 0x400000 );
    ios_jit_rx_base_global = ios_jit_rw_base_global = pool;
    map( 0, NT,   0x000000, NT_SZ,  NULL,  MAIN );   /* session ntdll copy */
    map( 1, NT,   0x140000, NT_SZ,  CHILD, CHILD );  /* the child's private ntdll copy */
    map( 2, FEXP, 0x280000, IMG_SZ, NULL,  MAIN );   /* parent emulator */
    map( 3, FEXC, 0x2c0000, IMG_SZ, NULL,  CHILD );  /* child emulator */
    map( 4, UNK,  0x300000, IMG_SZ, NULL,  NULL );   /* mapper unknown */
    map( 5, OTHER,0x340000, IMG_SZ, NULL,  CHILD );  /* a plain image */
    session_ki = (uint64_t)(uintptr_t)pool + 0x61508;
    child_ki = (uint64_t)(uintptr_t)pool + 0x140000 + 0x61508;
    for (int i = 1; i <= 4; i++) *slot( i ) = KIUED;

    assert( ios_jit_patch_stale_pointer( KIUED ) > 0 );
    printf( "parent fex %#llx child fex %#llx unknown %#llx child ntdll %#llx\n",
            (unsigned long long)*slot( 2 ), (unsigned long long)*slot( 3 ),
            (unsigned long long)*slot( 4 ), (unsigned long long)*slot( 1 ) );
    assert( *slot( 2 ) == session_ki );
    assert( *slot( 1 ) == child_ki );
    if (legacy)
    {
        assert( *slot( 3 ) == session_ki );     /* build 327: the child's emulator -> the parent's ntdll */
        assert( *slot( 4 ) == session_ki );
        printf( "legacy: child emulator healed into the session copy\n" );
        return 0;
    }
    assert( *slot( 3 ) == child_ki );           /* the child's emulator -> the child's ntdll */
    assert( *slot( 4 ) == KIUED );              /* unknown process: left to the owner-aware redirect */

    /* an image without per-process copies heals everywhere, unknown mapper included */
    *slot( 4 ) = OTHER + 0x1234;
    *slot( 3 ) = OTHER + 0x1234;
    assert( ios_jit_patch_stale_pointer( OTHER + 0x1234 ) == 2 );
    assert( *slot( 4 ) == (uint64_t)(uintptr_t)pool + 0x340000 + 0x1234 );
    assert( *slot( 3 ) == (uint64_t)(uintptr_t)pool + 0x340000 + 0x1234 );
    printf( "owner-aware: child emulator -> child ntdll, unknown left alone, plain images as before\n" );
    return 0;
}
'''

with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    source = folder / 'check.c'
    source.write_text(code)
    executable = folder / 'check'
    cc = os.environ.get('CC', 'cc')
    flags = [cc, '-std=gnu11', '-Wall', '-Wextra', '-Wno-unused-function', '-Wno-unused-parameter',
             '-Wno-unused-variable', '-Werror', '-g', str(source), '-o', str(executable)]
    sanitize = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    if subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True).returncode == 0:
        print('built with AddressSanitizer/UBSan')
    else:
        r = subprocess.run(flags, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        print('built without sanitizers')
    for value, mode in [(None, 'new'), ('1', 'new'), ('0', 'legacy')]:
        env = dict(os.environ)
        env.pop('MADEIRA_HEAL_OWNER', None)
        if value is not None:
            env['MADEIRA_HEAL_OWNER'] = value
        result = subprocess.run([str(executable), mode], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        print(f'MADEIRA_HEAL_OWNER={value!r}: ' + result.stdout.strip().splitlines()[-1])
        err = result.stderr
        assert '[stale-heal] 0x71ffd31508 -> ' in err, err
        if mode == 'new':
            assert 'left to the owner-aware fault redirect' in err, err
            assert 'for peb=0x10a238000 (owner=(nil) mapper=0x10a238000)' in err or \
                   'for peb=0x10a238000 (owner=0x0 mapper=0x10a238000)' in err, err
        else:
            assert 'left to the owner-aware' not in err, err
print('PASS: healed slots follow the process that maps the copy; MADEIRA_HEAL_OWNER=0 restores the old rule')
