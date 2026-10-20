#!/usr/bin/env python3
"""Reproduce sub-page ntdll data sync clearing a neighbouring live callback.

Compile the production helper with sanitizers. The fixture models the device's
separate PE and pool data copies; it does not run Wine or replace device tests.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
start = native.index('static int ios_sync_ntdll_data_patch(')
helper = native[start:native.index('\n}', start) + 2]
sync = native[native.index('iOS NtProtect-sync: triggered'):]
assert sync.index('ios_sync_ntdll_data_patch(') < sync.index('memcpy(jit_rw_dest, base, size)')
assert 'ios_jit_reverse_translate_addr( ios_patch_requested ), ios_patch_length' in sync

code = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#define min(a,b) ((a)<(b)?(a):(b))
#define FIELD_OFFSET(t,f) offsetof(t,f)
#define IMAGE_DOS_SIGNATURE 0x5a4d
#define IMAGE_NT_SIGNATURE 0x4550
#define IMAGE_NT_OPTIONAL_HDR64_MAGIC 0x20b
#define IMAGE_SIZEOF_SHORT_NAME 8
#define IMAGE_SCN_MEM_WRITE 0x80000000u
#define IMAGE_SCN_MEM_EXECUTE 0x20000000u
typedef struct { uint16_t e_magic; int32_t e_lfanew; } IMAGE_DOS_HEADER;
typedef struct { uint32_t Signature;
    struct { uint16_t NumberOfSections, SizeOfOptionalHeader; } FileHeader;
    struct { uint16_t Magic, reserved; } OptionalHeader; } IMAGE_NT_HEADERS64;
typedef struct { unsigned char Name[8]; union { uint32_t VirtualSize; } Misc;
    uint32_t VirtualAddress, Characteristics; } IMAGE_SECTION_HEADER;
static int ios_va_is_x86_code(uint64_t p) { return p == 0x71001000; }
static unsigned translations;
static void *ios_jit_translate_addr_for_owner(void *p, void *owner)
{
    translations++;
    return (uintptr_t)p == 0x71002000 ? (void *)(0x140002000ull + (uintptr_t)owner) : p;
}
''' + helper + r'''
static union { uint64_t align; unsigned char bytes[0x5000]; } pe_store, pool_store;
#define pe pe_store.bytes
#define pool pool_store.bytes
static IMAGE_NT_HEADERS64 *nt;
static IMAGE_SECTION_HEADER *sec;
static const void *ntdll_entry;
static void put(unsigned char *p, uint64_t v) { memcpy(p, &v, 8); }
static uint64_t get(const unsigned char *p) { uint64_t v; memcpy(&v,p,8); return v; }
static int run(size_t off, size_t n, void *owner)
{ return ios_sync_ntdll_data_patch(pe, sizeof(pe), pe+off, n, pe+0x2000, 0x1000, pool+0x2000, owner, ntdll_entry); }
int main(void)
{
    IMAGE_DOS_HEADER *dos = (void *)pe;
    dos->e_magic = IMAGE_DOS_SIGNATURE; dos->e_lfanew = 0x100;
    nt = (void *)(pe+0x100); nt->Signature = IMAGE_NT_SIGNATURE;
    nt->FileHeader.SizeOfOptionalHeader = sizeof(nt->OptionalHeader);
    nt->FileHeader.NumberOfSections = 2;
    nt->OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR64_MAGIC;
    sec = (void *)((char *)&nt->OptionalHeader + sizeof(nt->OptionalHeader));
    memcpy(sec[0].Name,".text",6); sec[0].VirtualAddress=0x1000;
    sec[0].Misc.VirtualSize=0x1000; sec[0].Characteristics=IMAGE_SCN_MEM_EXECUTE;
    memcpy(sec[1].Name,".data",6); sec[1].VirtualAddress=0x2000;
    sec[1].Misc.VirtualSize=0x2000; sec[1].Characteristics=IMAGE_SCN_MEM_WRITE;
    ntdll_entry=pe+0x1000;
    /* Exact device pattern: hook slot +0x4f0, callbacks +0xe78/+0xec8.
     * Before the fix, the page memcpy erased BOTH sentinel callbacks. */
    put(pool+0x2e78,0x154111111); put(pool+0x2ec8,0x154222222);
    put(pe+0x24f0,0x71001000);
    unsigned char legacy[0x1000];
    memcpy(legacy,pe+0x2000,sizeof(legacy));
    assert(get(legacy+0xec8)==0 && get(pool+0x2ec8)!=0);
    puts("control: the old whole-page copy erases the live recovery callback");
    assert(run(0x24f0,8,(void *)1));
    assert(get(pool+0x24f0)==0x71001000 && translations==0);
    assert(get(pool+0x2e78)==0x154111111 && get(pool+0x2ec8)==0x154222222);
    assert(run(0x24f0,8,(void *)1)); /* restore protection must preserve too */
    put(pe+0x24f0,0x71002000);
    assert(run(0x24f0,8,(void *)1) && get(pool+0x24f0)==0x140002001ull);
    assert(run(0x24f0,8,(void *)2) && get(pool+0x24f0)==0x140002002ull);
    /* Unchanged constants are not rebound just because they resemble VAs. */
    put(pe+0x2500,0x71002000); put(pool+0x2500,0x71002000);
    unsigned before=translations;
    assert(run(0x2500,8,NULL) && translations==before);
    assert(get(pool+0x2500)==0x71002000);
    memset(pool+0x2510,0xa5,16); memset(pe+0x2510,0x5a,16);
    assert(run(0x2513,3,NULL));
    for(unsigned i=0;i<16;i++) assert(pool[0x2510+i]==(i>=3 && i<6?0x5a:0xa5));
    /* Partial words on both sides of a complete pointer. */
    memset(pool+0x2520,0xa5,24); memset(pe+0x2520,0x5a,24);
    put(pe+0x2528,0x71002000);
    assert(run(0x2523,16,(void *)1));
    assert(get(pool+0x2528)==0x140002001ull);
    assert(pool[0x2522]==0xa5 && pool[0x2533]==0xa5);
    assert(!run(0x24f0,0,NULL));
    assert(!run(0x2ff8,16,NULL)); /* not contained in the protected page */
    assert(!run(0x4ff8,16,NULL)); /* not contained in the image */
    assert(!ios_sync_ntdll_data_patch(pe,sizeof(pe),(void *)(UINTPTR_MAX-3),8,pe+0x2000,0x1000,pool+0x2000,NULL,ntdll_entry));
    ntdll_entry=NULL; assert(!run(0x24f0,8,NULL));
    ntdll_entry=pool; assert(!run(0x24f0,8,NULL)); ntdll_entry=pe+0x1000;
    sec[1].Characteristics|=IMAGE_SCN_MEM_EXECUTE; assert(!run(0x24f0,8,NULL));
    sec[1].Characteristics=0; assert(!run(0x24f0,8,NULL)); sec[1].Characteristics=IMAGE_SCN_MEM_WRITE;
    sec[1].Name[0]='?'; assert(!run(0x24f0,8,NULL)); sec[1].Name[0]='.';
    nt->FileHeader.NumberOfSections=0xffff; assert(!run(0x24f0,8,NULL));
    nt->FileHeader.NumberOfSections=2; dos->e_lfanew=0x4fff; assert(!run(0x24f0,8,NULL));
    dos->e_lfanew=-1; assert(!run(0x24f0,8,NULL));
    puts("PASS: ntdll sub-page data patches preserve neighbouring live callbacks");
}
'''

with tempfile.TemporaryDirectory() as d:
    source, binary = Path(d) / 'check.c', Path(d) / 'check'
    source.write_text(code)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-g',
                    str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
