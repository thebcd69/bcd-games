#!/usr/bin/env python3
# Copyright 2026 alantangok. This file was taken from https://github.com/willfaust/Madeira (pull request #377)
# under GPL-3.0-or-later. See THIRD-PARTY-NOTICES.md.
"""Exercise the production allocator with variable-sized shared object payloads.

SimCity 4's startup allocated a class with four extra bytes, then a window.
The following object's 64-bit sequence store was four-byte aligned and faulted
in wineserver on ARM64. Stub mapping/list management, retain the allocator and
session structures, and require naturally aligned sequence stores.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/wineserver/mapping_ios.c'
text = source.read_text()


def definition(start, end):
    offset = text.index(start)
    return text[offset:text.index(end, offset) + len(end)]


code = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef uint64_t mem_size_t;
typedef uint64_t object_id_t;
struct list { struct list *next, *prev; };
typedef struct { uint64_t payload[8]; } object_shm_t;
typedef struct { uint64_t seq; object_id_t id; object_shm_t shm; } shared_object_t;
#define max(a,b) ((a) > (b) ? (a) : (b))
#define CONTAINING_RECORD(p,t,f) ((t *)((char *)(p) - offsetof(t,f)))
static void write_sequence(volatile uint64_t *p, uint64_t value)
{
    if ((uintptr_t)p % 8) {
        fprintf(stderr, "FAIL: sequence address is not 8-byte aligned\n");
        exit(1);
    }
    __atomic_store_n(p, value, __ATOMIC_RELEASE);
}
#define SHARED_WRITE_BEGIN(p,t) do { t *shared=(p); \
    shared_object_t *obj=CONTAINING_RECORD(shared,shared_object_t,shm); \
    write_sequence(&obj->seq, obj->seq + 1); do
#define SHARED_WRITE_END while(0); write_sequence(&obj->seq, obj->seq + 1); } while(0)
#define mark_block_uninitialized(p,n) ((void)0)
''' + definition('struct session_block\n{', '\n};') + '\n' + \
    definition('struct session_object\n{', '\n};') + '\n' + \
    definition('static inline mem_size_t round_size(', '\n}') + r'''
static struct { object_id_t last_object_id; } session;
static struct session_block block;
static struct session_object *find_free_session_object(mem_size_t size)
{ (void)size; return NULL; }
static void list_remove(struct list *entry) { (void)entry; }
static struct session_block *find_free_session_block(mem_size_t size)
{ assert(block.used_size + size <= block.block_size); return &block; }
''' + definition('volatile void *alloc_shared_object(', '\n}') + r'''
int main(void)
{
    block.data=calloc(1, 1024*1024); assert(block.data);
    block.block_size=1024*1024;
    for (unsigned extra=0; extra<128; extra++) {
        volatile void *payload=alloc_shared_object(sizeof(object_shm_t)+extra);
        shared_object_t *obj=CONTAINING_RECORD(payload,shared_object_t,shm);
        assert(obj->id==extra+1 && obj->seq==2);
        assert((uintptr_t)obj % 8==0);
    }
    free((void *)block.data);
    puts("PASS: 128 variable-sized allocations preserve aligned 64-bit sequence stores");
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-session-alignment-') as temporary:
    directory = Path(temporary)
    (directory / 'check.c').write_text(code)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Werror', '-Wno-unused-function',
                    '-fsanitize=address,undefined', str(directory / 'check.c'),
                    '-o', str(directory / 'check')], check=True)
    subprocess.run([str(directory / 'check')], check=True)
