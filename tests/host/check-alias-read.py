#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Run Wine's read/pread paths against real RX/RW aliases, without Wine or a phone."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "build/ntdll-unix/virtual_ios.c").read_text()


def function(signature):
    start = source.index(signature)
    return source[start:source.index("\n}\n", start) + 3]


harness = r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
typedef int BOOL;
typedef int NTSTATUS;
typedef unsigned char BYTE;
#define FALSE 0
#define TRUE 1
#define STATUS_SUCCESS 0
#define STATUS_INVALID_USER_BUFFER 1
#define VPROT_WRITEWATCH 2
#define min(a,b) ((a) < (b) ? (a) : (b))
#define max(a,b) ((a) > (b) ? (a) : (b))
#define ROUND_ADDR(a,m) ((char *)((uintptr_t)(a) & ~(uintptr_t)(m)))
#define ROUND_SIZE(a,s,m) (((s) + ((uintptr_t)(a) & (m)) + (m)) & ~(m))
static size_t host_page_size = 0x4000, host_page_mask = 0x3fff;
static int virtual_mutex, use_kernel_writewatch, locks, denied, watch, updates, fail_alloc;
static size_t accessed;
static char *guest, *rw;
static struct { uintptr_t user_va, user_va_end, jit_rw_alias; } ios_jit_anon_aliases[2];
static int ios_jit_anon_alias_count = 2;
static void server_enter_uninterrupted_section(int *m, sigset_t *s) { assert(!locks++); }
static void server_leave_uninterrupted_section(int *m, sigset_t *s) { assert(!--locks); }
static BYTE get_host_page_vprot(const void *p) { return denied ? 0 : 1 | (watch ? VPROT_WRITEWATCH : 0); }
static int get_unix_prot(BYTE p) { return p & 1 ? PROT_READ | PROT_WRITE : PROT_READ; }
static void mprotect_range(void *p, size_t n, int a, int b) { assert(locks); }
static BOOL is_write_watch_range(void *p, size_t n) { return watch; }
static void update_write_watches(void *p, size_t n, size_t used)
{ assert(locks); updates++; accessed = used; }
static void *read_alloc(size_t n) { return fail_alloc ? NULL : malloc(n); }
#define malloc read_alloc
''' + "\n".join(function(s) for s in (
    "uintptr_t ios_jit_anon_alias_lookup(uintptr_t fault_addr)",
    "int ios_jit_anon_alias_overlaps(void *user_va, size_t size,",
    "static NTSTATUS check_write_access( void *base, size_t size,",
    "static ssize_t ios_alias_read( int fd, void *addr, size_t size,",
    "ssize_t virtual_locked_read( int fd, void *addr, size_t size )",
    "ssize_t virtual_locked_pread( int fd, void *addr, size_t size, off_t offset )",
)) + r'''
#undef malloc
int main(void)
{
    const size_t page = host_page_size, size = 5 * page;
    char backing_name[] = "/tmp/bcd-alias-backing-XXXXXX";
    char input_name[] = "/tmp/bcd-alias-input-XXXXXX";
    int backing = mkstemp(backing_name), input = mkstemp(input_name);
    unsigned char *pattern = malloc(size);
    assert(backing >= 0 && input >= 0 && pattern);
    unlink(backing_name); unlink(input_name);
    assert(!ftruncate(backing, 2 * page));
    guest = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    rw = mmap(NULL, 2 * page, PROT_READ | PROT_WRITE, MAP_SHARED, backing, 0);
    assert(guest != MAP_FAILED && rw != MAP_FAILED);
    for (int i = 0; i < 2; i++)
    {
        char *at = guest + (1 + 2 * i) * page;
        assert(mmap(at, page, PROT_READ, MAP_SHARED | MAP_FIXED, backing, i * page) == at);
        ios_jit_anon_aliases[i].user_va = (uintptr_t)at;
        ios_jit_anon_aliases[i].user_va_end = (uintptr_t)at + page;
        ios_jit_anon_aliases[i].jit_rw_alias = (uintptr_t)rw + i * page;
    }
    for (size_t i = 0; i < size; i++) pattern[i] = (i * 17 + i / page) % 251;
    assert(write(input, pattern, size) == size);

    /* The old retry passes logical validation but the kernel still cannot write RX. */
    errno = 0;
    assert(pread(input, guest + page, page, 0) == -1 && errno == EFAULT);
    puts("ok: kernel read into a guest-writable RX page reproduces EFAULT");

    assert(lseek(input, 0, SEEK_SET) == 0);
    assert(virtual_locked_read(input, guest, size) == size);
    assert(!memcmp(guest, pattern, size));
    assert(lseek(input, 0, SEEK_CUR) == size);
    puts("ok: read spans plain pages and two distinct aliases, advancing the file once");

    memset(rw, 0xaa, 2 * page);
    assert(virtual_locked_pread(input, guest + page - 7, 2 * page + 23, 11) == 2 * page + 23);
    assert(!memcmp(guest + page - 7, pattern + 11, 2 * page + 23));
    assert(lseek(input, 0, SEEK_CUR) == size);
    puts("ok: unaligned pread crosses both boundaries without changing the file offset");

    watch = 1;
    memset(rw, 0xaa, 2 * page);
    assert(virtual_locked_pread(input, guest + page, page, size - 13) == 13);
    assert(!memcmp(guest + page, pattern + size - 13, 13));
    for (size_t i = 13; i < page; i++) assert((unsigned char)guest[page + i] == 0xaa);
    assert(updates == 1 && accessed == 13);
    assert(virtual_locked_pread(input, guest + page, page, size) == 0);
    assert(updates == 2 && accessed == 0);
    errno = 0;
    assert(virtual_locked_read(-1, guest + page, page) == -1 && errno == EBADF);
    assert(updates == 3 && accessed == 0);
    puts("ok: short reads, EOF, errno and write-watch byte counts are preserved");

    fail_alloc = 1;
    assert(lseek(input, 0, SEEK_SET) == 0);
    errno = 0;
    assert(virtual_locked_read(input, guest + page, page) == -1 && errno == ENOMEM);
    assert(lseek(input, 0, SEEK_CUR) == 0);
    assert(updates == 4 && accessed == 0);
    fail_alloc = 0;
    puts("ok: allocation failure restores write watches without consuming input");

    denied = 1;
    assert(lseek(input, 0, SEEK_SET) == 0);
    errno = 0;
    assert(virtual_locked_read(input, guest + page, page) == -1 && errno == EFAULT);
    assert(lseek(input, 0, SEEK_CUR) == 0);
    assert((unsigned char)guest[page + 13] == 0xaa);
    denied = watch = 0;
    assert(virtual_locked_pread(input, guest, 32, 0) == 32);
    assert(!memcmp(guest, pattern, 32));
    assert(virtual_locked_read(input, guest + page, 0) == 0);
    assert(!locks);
    puts("ok: read-only guest memory is rejected before I/O; plain and zero-length reads work");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / "alias-read.c"
    exe = Path(tmp) / "alias-read"
    c.write_text(harness)
    subprocess.run([os.environ.get("CC", "cc"), "-O1", "-g", "-o", str(exe), str(c)], check=True)
    subprocess.run([str(exe)], check=True)
