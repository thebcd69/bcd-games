#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright the Wine authors (LGPL-2.1-or-later): the two functions quoted below as they were before the change
# (dlls/ntdll/unix/socket.c and system.c of Wine 11.4). BCD's terms above do not apply to those lines.
"""getsockopt and /dev/urandom fill a buffer the kernel cannot write, and only then take a new path (build 660; Wine fork commit by Ash
Hopkins, upstream Madeira's fork 89481d27baf, and BCD's follow-up 0a5f587e0d7); no Wine runs.

On iOS anonymous PAGE_EXECUTE_READWRITE memory is an RX view with a separate RW alias. A store from user space reaches the alias through the
fault handler, but the kernel cannot write the view: a system call that fills such a buffer fails with EFAULT.
  - do_getsockopt and the IP_DONTFRAG / IPV6_DONTFRAG queries answered WSAEFAULT where Windows succeeds;
  - read_dev_urandom (NtQuerySystemInformation(SystemInterruptInformation) and others) reported success and left the buffer as it was.
Madeira's commit fills a local copy on EVERY call and stores it. BCD's follow-up (drafted by its review of 2026-11-14) calls the kernel on
the caller's buffer first, exactly as Wine 11.4 does, and takes the local copy only when that call failed with EFAULT. So a call that
worked runs the code it ran before: the same number of system calls, the same bytes, no allocation, whatever length the caller passed
(ws2_32 passes a garbage *optlen through, and the kernel ignores it). Only a call that failed with EFAULT changes. The copy is bounded:
256 bytes for an option (the options that reach this function are integers, IP_OPTIONS at most 40 bytes), a DWORD for the DONTFRAG
queries, 256-byte chunks for /dev/urandom.

What the commit does NOT do in BCD: its third part makes NtReadFileScatter read through virtual_locked_pread / virtual_locked_read. In
Madeira those fill RWX memory through the RW alias (ios_kw_*, Madeira 5d0be19 and 81cbd38, in its app repository). BCD has not taken that
code: its virtual_locked_* (build/ntdll-unix/virtual_ios.c) are Wine's stock functions, which after EFAULT check for a write watch and try
the same read again. So in BCD a scatter read into RWX memory still fails; the change only gives scatter reads NtReadFile's write-watch
retry.

Not proven here: that BCD's Mach store emulator routes the copy's store (a memmove from native code) into the RW alias on a device. It
does so for every other Nt function that writes a result into a guest buffer; if it declined, a call that failed would still fail.

No BCD log shows one of these failures (the calls fail without a log line of their own, and no log of 2026-10-20 to 2026-11-10 has a
"Bad address" line), so there is no auto-tune rule.

Checks (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none, or when the one there
lacks the change and is not this checkout's pin):
  1. do_getsockopt and read_dev_urandom, cut from the Wine source, and Wine 11.4's originals (quoted below), compiled on the host against
     the same stand-in kernel, which refuses (EFAULT) to write a marked buffer as iOS refuses RWX memory, and counts its calls. For an
     ORDINARY buffer every scenario gives the same result, the same bytes and the same number of kernel calls from both: plain options, a
     garbage length of 2 GB, a failing getsockopt, a bad handle, an option of 600 bytes, short reads, EINTR, a read error, a missing
     device; nothing is allocated. For a MARKED buffer the originals fail and the new functions fill it with one extra kernel call; an
     option larger than the 256-byte copy is cut to 256 bytes there; a read error in the middle keeps what was read.
  2. The two DONTFRAG queries call the kernel on the caller's buffer first and use a local value only after EFAULT; NtReadFileScatter
     reads through virtual_locked_pread / virtual_locked_read, and BCD's virtual_ios.c has no ios_kw_ helper (the statement above).
  3. BCD's file.c patch scripts still apply to the changed file, and the CI runs this.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(text, signature):
    start = text.find(signature)
    if start < 0:
        return ''
    return text[start:text.index('\n}\n', start) + 3]


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


pin = ''
tree = git('ls-tree', 'HEAD', 'wine')
if tree:
    pin = tree.split()[2]
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'dlls/ntdll/unix/socket.c').is_file():
        wine = Path(candidate)
        break
socket_c = (wine / 'dlls/ntdll/unix/socket.c').read_text() if wine else ''
has_change = 'if (!(ret = getsockopt( fd, level, option, local, &len ))) copy = 1;' in socket_c
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

# The two functions as they were before the change: Wine's own text (dlls/ntdll/unix/socket.c and system.c of the fork's base,
# LGPL-2.1-or-later, copyright the Wine authors), quoted so that the new ones can be compared with them call by call.
ORIGINAL = r'''static NTSTATUS do_getsockopt( HANDLE handle, IO_STATUS_BLOCK *io, int level,
                               int option, void *out_buffer, ULONG out_size )
{
    int fd, needs_close = FALSE;
    socklen_t len = out_size;
    NTSTATUS status;
    int ret;

    if ((status = server_get_unix_fd( handle, 0, &fd, &needs_close, NULL, NULL )))
        return status;

    ret = getsockopt( fd, level, option, out_buffer, &len );
    if (needs_close) close( fd );
    if (ret) return sock_errno_to_status( errno );
    if (io)
    {
        io->Status = STATUS_SUCCESS;
        io->Information = len;
    }
    return STATUS_SUCCESS;
}
static void read_dev_urandom( void *buf, ULONG len )
{
    int fd = open( "/dev/urandom", O_RDONLY );
    if (fd != -1)
    {
        int ret;
        do
        {
            ret = read( fd, buf, len );
        }
        while (ret == -1 && errno == EINTR);
        close( fd );
    }
    else WARN( "can't open /dev/urandom\n" );
}
'''

HARNESS = r'''
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/socket.h>

typedef int NTSTATUS; typedef void *HANDLE; typedef unsigned int ULONG; typedef unsigned long ULONG_PTR;
typedef struct { NTSTATUS Status; ULONG_PTR Information; } IO_STATUS_BLOCK;
#define FALSE 0
#define STATUS_SUCCESS 0
#define STATUS_NO_MEMORY 0xc0000017
#define WARN(...) do { warned++; } while (0)
#ifndef min
#define min(a,b) ((a) < (b) ? (a) : (b))
#endif
static int warned;

/* the stand-in kernel: it refuses to write [guard, guard + guard_len), as iOS refuses anonymous RWX memory, and counts its calls */
static char *guard; static size_t guard_len;
static int in_guard( const void *p, size_t n ) { return guard && (const char *)p < guard + guard_len && (const char *)p + (n ? n : 1) > guard; }
static int opt_ret, opt_errno; static socklen_t opt_len;
static int closes, opens_fail, heap_allocs, sockopt_calls, read_calls;
static int short_reads, eintr_first, read_error_after = -1; static unsigned long stream_pos;

static int fake_getsockopt( int fd, int level, int option, void *buf, socklen_t *len )
{
    socklen_t i, n = opt_len < *len ? opt_len : *len;
    sockopt_calls++;
    if (opt_ret) { errno = opt_errno; return -1; }
    if (in_guard( buf, n )) { errno = EFAULT; return -1; }
    for (i = 0; i < n; i++) ((unsigned char *)buf)[i] = (unsigned char)(0xa0 + i);
    *len = n;
    return 0;
}
static int fake_open( const char *path, int flags ) { if (opens_fail) { errno = ENOENT; return -1; } return 7; }
static int fake_close( int fd ) { closes++; return 0; }
static long fake_read( int fd, void *buf, size_t len )
{
    size_t i, n = len;
    read_calls++;
    if (eintr_first) { eintr_first--; errno = EINTR; return -1; }
    if (read_error_after >= 0 && (long)stream_pos >= read_error_after) { errno = EIO; return -1; }
    if (in_guard( buf, len )) { errno = EFAULT; return -1; }
    if (short_reads && n > 37) n = 37;
    for (i = 0; i < n; i++) ((unsigned char *)buf)[i] = (unsigned char)((stream_pos + i) * 7 + 3);
    stream_pos += n;
    return (long)n;
}
static void *fake_malloc( size_t n ) { heap_allocs++; return n > (64u << 20) ? NULL : malloc( n ); }
static NTSTATUS server_get_unix_fd( HANDLE h, unsigned int access, int *fd, int *needs_close, void *type, void *options )
{ *fd = 5; *needs_close = 1; return h ? STATUS_SUCCESS : 0xc0000008; }
static NTSTATUS sock_errno_to_status( int err ) { return 0xc0000000 | err; }

#define getsockopt fake_getsockopt
#define open fake_open
#define close fake_close
#define read fake_read
#define malloc fake_malloc
#include "CODE"
#undef malloc

static unsigned sum( const char *p, size_t n ) { unsigned s = 0; size_t i; for (i = 0; i < n; i++) s = s * 31 + (unsigned char)p[i]; return s; }
static void reset( void )
{ opt_ret = 0; opt_errno = 0; opt_len = 4; closes = opens_fail = heap_allocs = sockopt_calls = read_calls = 0;
  short_reads = eintr_first = 0; read_error_after = -1; stream_pos = 0; warned = 0; guard = NULL; }

/* one line per scenario: status, io, a checksum of the whole buffer, and how the kernel was used */
int main( int argc, char **argv )
{
    static char buf[1024]; IO_STATUS_BLOCK io; NTSTATUS st; int marked;
    for (marked = 0; marked < 2; marked++)
    {
#define G(name, handle, optlen, size, fail, errn) do { reset(); if (marked) { guard = buf; guard_len = sizeof(buf); } \
        opt_len = optlen; opt_ret = fail; opt_errno = errn; memset( buf, 0x55, sizeof(buf) ); memset( &io, 0xff, sizeof(io) ); \
        st = do_getsockopt( (HANDLE)(long)handle, &io, 1, 2, buf, size ); \
        printf( "%s %s st=%08x io=%08x/%lu sum=%08x kernel=%d close=%d heap=%d\n", marked ? "marked" : "plain", name, (unsigned)st, \
                (unsigned)io.Status, (unsigned long)io.Information, sum( buf, sizeof(buf) ), sockopt_calls, closes, heap_allocs ); } while (0)
        G( "sockopt-int", 1, 4, 4, 0, 0 );
        G( "sockopt-larger-buffer", 1, 4, 8, 0, 0 );
        G( "sockopt-garbage-length", 1, 4, 0x7ffe1234u, 0, 0 );
        G( "sockopt-600-bytes", 1, 600, 600, 0, 0 );
        G( "sockopt-error", 1, 4, 4, -1, ENOPROTOOPT );
        G( "sockopt-bad-handle", 0, 4, 4, 0, 0 );
#define R(name, size, shorts, eintr, errafter, noopen) do { reset(); if (marked) { guard = buf; guard_len = sizeof(buf); } \
        short_reads = shorts; eintr_first = eintr; read_error_after = errafter; opens_fail = noopen; memset( buf, 0x55, sizeof(buf) ); \
        read_dev_urandom( buf, size ); \
        printf( "%s %s sum=%08x kernel=%d close=%d warned=%d\n", marked ? "marked" : "plain", name, sum( buf, sizeof(buf) ), read_calls, closes, warned ); } while (0)
        R( "urandom-16", 16, 0, 0, -1, 0 );
        R( "urandom-700", 700, 0, 0, -1, 0 );
        R( "urandom-short-reads", 300, 1, 0, -1, 0 );
        R( "urandom-eintr", 64, 0, 2, -1, 0 );
        R( "urandom-error-at-once", 64, 0, 0, 0, 0 );
        R( "urandom-error-after-256", 600, 0, 0, 256, 0 );
        R( "urandom-no-device", 16, 0, 0, -1, 1 );
    }
    return 0;
}
'''


def expected_sum(pairs):
    """checksum of a 1024-byte buffer of 0x55 with the given (offset, bytes) written"""
    b = bytearray([0x55]) * 1024
    for off, data in pairs:
        b[off:off + len(data)] = data
    s = 0
    for c in b:
        s = (s * 31 + c) & 0xffffffff
    return '%08x' % s


def opt(n):
    return bytes((0xa0 + i) & 0xff for i in range(n))


def stream(n, start=0):
    return bytes(((start + i) * 7 + 3) & 0xff for i in range(n))


if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    system_c = (wine / 'dlls/ntdll/unix/system.c').read_text()
    file_c = (wine / 'dlls/ntdll/unix/file.c').read_text()
    new_sockopt = function(socket_c, 'static NTSTATUS do_getsockopt( HANDLE handle, IO_STATUS_BLOCK *io, int level,')
    new_urandom = function(system_c, 'static void read_dev_urandom( void *buf, ULONG len )\n{')
    check('do_getsockopt and read_dev_urandom are in the Wine source', bool(new_sockopt) and bool(new_urandom))
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='kernel-writes-rwx-'))
    try:
        out = {}
        for name, code in (('new', new_sockopt + new_urandom), ('original', ORIGINAL)):
            (tmp / (name + '.c')).write_text(HARNESS.replace('#include "CODE"', code))
            r = subprocess.run([cc, '-std=gnu11', '-O1', '-Wno-unused-function', '-o', str(tmp / name), str(tmp / (name + '.c'))],
                               capture_output=True, text=True)
            check('the %s functions compile on the host' % name, r.returncode == 0, r.stderr[-1500:])
            if r.returncode == 0:
                out[name] = subprocess.run([str(tmp / name)], capture_output=True, text=True).stdout.splitlines()
        if len(out) == 2:
            new = {' '.join(l.split()[:2]): l for l in out['new']}
            old = {' '.join(l.split()[:2]): l for l in out['original']}
            plain = sorted(k for k in new if k.startswith('plain '))
            check('an ORDINARY buffer: all %d scenarios give the same line from the new functions and from Wine 11.4\'s (status, status block, '
                  'every byte of the buffer, the number of kernel calls, the close, no allocation)' % len(plain),
                  len(plain) == 13 and all(new[k] == old[k] for k in plain), '\n     '.join(new[k] + ' | ' + old[k] for k in plain if new[k] != old[k]))
            check('there a call is one kernel call, as before, also with a garbage length of 2 GB (nothing is allocated for it)',
                  'kernel=1 close=1 heap=0' in new['plain sockopt-int'] and 'kernel=1 close=1 heap=0' in new['plain sockopt-garbage-length']
                  and 'st=00000000 io=00000000/4' in new['plain sockopt-garbage-length'])
            check('there a failing getsockopt returns the system call\'s own error and leaves the buffer alone',
                  ('st=%08x' % (0xc0000000 | 42)) in new['plain sockopt-error'] and expected_sum([]) in new['plain sockopt-error'],
                  new['plain sockopt-error'])   # ENOPROTOOPT is 42 on Darwin
            # the marked buffer: Wine 11.4 fails, the new functions fill it
            check('a MARKED buffer: Wine 11.4\'s getsockopt fails with EFAULT and leaves it, the new one fills it with one more kernel call',
                  ('st=%08x' % (0xc0000000 | 14)) in old['marked sockopt-int'] and expected_sum([]) in old['marked sockopt-int']
                  and 'st=00000000 io=00000000/4 sum=%s kernel=2 close=1 heap=0' % expected_sum([(0, opt(4))]) in new['marked sockopt-int'],
                  new['marked sockopt-int'] + ' | ' + old['marked sockopt-int'])
            check('there a larger buffer and a garbage length get the option\'s 4 bytes, nothing more, with no allocation',
                  'io=00000000/4 sum=%s kernel=2 close=1 heap=0' % expected_sum([(0, opt(4))]) in new['marked sockopt-larger-buffer']
                  and 'io=00000000/4 sum=%s kernel=2 close=1 heap=0' % expected_sum([(0, opt(4))]) in new['marked sockopt-garbage-length'])
            check('there an option of 600 bytes is cut to the 256-byte copy (an ordinary buffer gets all 600, as before)',
                  'io=00000000/256 sum=%s kernel=2' % expected_sum([(0, opt(256))]) in new['marked sockopt-600-bytes']
                  and 'io=00000000/600 sum=%s kernel=1' % expected_sum([(0, opt(600))]) in new['plain sockopt-600-bytes'])
            check('there an error that is not EFAULT, and a bad handle, are what they were (one kernel call, or none)',
                  new['marked sockopt-error'].split(' ', 1)[1] == old['marked sockopt-error'].split(' ', 1)[1]
                  and new['marked sockopt-bad-handle'].split(' ', 1)[1] == old['marked sockopt-bad-handle'].split(' ', 1)[1]
                  and 'kernel=0' in new['marked sockopt-bad-handle'])
            check('/dev/urandom into a marked buffer: Wine 11.4 leaves it as it was, the new function fills all 700 bytes in 256-byte chunks',
                  'sum=%s kernel=1' % expected_sum([]) in old['marked urandom-700']
                  and 'sum=%s kernel=4 close=1' % expected_sum([(0, stream(700))]) in new['marked urandom-700'], new['marked urandom-700'])
            check('there short reads and EINTR are completed, and a read error keeps the 256 bytes read before it and closes the descriptor',
                  'sum=%s' % expected_sum([(0, stream(300))]) in new['marked urandom-short-reads']
                  and 'sum=%s' % expected_sum([(0, stream(64))]) in new['marked urandom-eintr']
                  and 'sum=%s kernel=3 close=1' % expected_sum([(0, stream(256))]) in new['marked urandom-error-after-256'],
                  new['marked urandom-short-reads'] + ' | ' + new['marked urandom-error-after-256'])
            check('an ordinary buffer keeps Wine 11.4\'s single read: a short read is NOT completed (37 of 300 bytes), as before',
                  'sum=%s kernel=1' % expected_sum([(0, stream(37))]) in new['plain urandom-short-reads'])
            check('a missing device warns and writes nothing, marked or not',
                  'warned=1' in new['plain urandom-no-device'] and expected_sum([]) in new['marked urandom-no-device'])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for name in ('IP_DONTFRAG', 'IPV6_DONTFRAG'):
        level = 'IPPROTO_IP' if name == 'IP_DONTFRAG' else 'IPPROTO_IPV6'
        direct = 'ret = getsockopt( fd, %s, %s, out_buffer, &len );\n            if (ret && errno == EFAULT)' % (level, name)
        local = 'ret = getsockopt( fd, %s, %s, value, &len );\n                if (!ret) memcpy( out_buffer, value, len );' % (level, name)
        check('the %s query calls the kernel on the caller\'s buffer first and uses a local DWORD only after EFAULT' % name,
              socket_c.count(direct) == 1 and socket_c.count(local) == 1 and socket_c.index(direct) < socket_c.index(local))
    scatter = function(file_c, 'NTSTATUS WINAPI NtReadFileScatter( HANDLE file, HANDLE event, PIO_APC_ROUTINE apc, void *apc_user,')
    check('NtReadFileScatter reads through virtual_locked_pread / virtual_locked_read, as NtReadFile does',
          'result = virtual_locked_pread( unix_handle, (char *)segments->Buffer + pos,' in scatter
          and 'result = virtual_locked_read( unix_handle, (char *)segments->Buffer + pos,' in scatter
          and 'result = pread( unix_handle' not in scatter and 'result = read( unix_handle' not in scatter)
    virtual_ios = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
    check('and in BCD those are Wine\'s stock functions: no RW-alias read (ios_kw_) exists, so a scatter read into RWX memory still fails',
          'ios_kw_' not in virtual_ios and 'ssize_t virtual_locked_pread(' in virtual_ios and 'ssize_t virtual_locked_read(' in virtual_ios)

    # BCD's own scripts on the changed file.c: every one applies to a copy, in the build's order
    scripts = ['patch-wine-guest-log.py', 'patch-wine-guest-log-tag.py', 'patch-wine-guest-log-service.py', 'patch-wine-guest-log-renpy.py',
               'patch-wine-guest-log-trace.py', 'patch-wine-guest-log-redact.py', 'patch-wine-guest-log-secrets.py', 'patch-wine-guest-log-ring.py', 'patch-wine-file-trace.py',
               'patch-wine-file-census.py', 'patch-wine-globalroot.py', 'patch-wine-app-local-dll.py', 'patch-wine-file-info-fstat.py',
               'patch-wine-hide-host-version-plist.py', 'patch-wine-dir-storm.py']
    tmp = Path(tempfile.mkdtemp(prefix='kernel-writes-rwx-scripts-'))
    try:
        copy = tmp / 'file.c'
        shutil.copy(wine / 'dlls/ntdll/unix/file.c', copy)
        failed = []
        for s in scripts:
            r = subprocess.run([sys.executable, str(root / 'tools' / s), str(copy)], capture_output=True, text=True)
            if r.returncode:
                failed.append(s + ': ' + (r.stdout + r.stderr)[-200:])
        check('BCD\'s %d file.c patch scripts apply to the changed file in the build\'s order' % len(scripts), not failed, '; '.join(failed))
        once = copy.read_text()
        for s in scripts:
            subprocess.run([sys.executable, str(root / 'tools' / s), str(copy)], capture_output=True, text=True)
        check('and a second run of them changes nothing', copy.read_text() == once)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-kernel-writes-rwx.py' in ci)

print('PASS: kernel writes into RWX buffers' if ok else 'FAILED: kernel writes into RWX buffers')
sys.exit(0 if ok else 1)
