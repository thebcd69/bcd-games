#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""SetNamedPipeHandleState on a pipe that is a plain unix descriptor (build 660; Wine fork commit by SirDank, upstream Madeira's fork
929cc3690b3, and BCD's follow-up); no Wine runs.

A standard handle can be a unix pipe wrapped as a file object. GetFileType says FILE_TYPE_PIPE, but NtSetInformationFile
(FilePipeInformation) goes to the server's set_named_pipe_info, which only knows named-pipe objects, and fails with an invalid-handle
status. libuv calls SetNamedPipeHandleState on every pipe it adopts (uv_pipe_open) and treats the failure as fatal: Node.js with a piped
stdout stops at startup with "Error: open EBADF". Upstream answers STATUS_SUCCESS when the server rejects the handle and its device type is
FILE_DEVICE_NAMED_PIPE. BCD's follow-up commit accepts only the state such a pipe really has, byte reads and blocking (ReadMode and
CompletionMode 0, what libuv asks for); a request for message reads or the non-blocking mode keeps the server's failure.

Two things to know. First, in a default session the change is not reachable: BCD wraps exactly three unix descriptors as file objects
(stdin, stdout, stderr), and stdout and stderr are the log file unless MADEIRA_STDOUT_PIPE or MADEIRA_STDIN_PIPE puts a pipe there (no
list row does). A pipe a Windows program creates itself is a server pipe object and never reaches the new lines. Second, the device
type the new lines ask for is FILE_DEVICE_NAMED_PIPE for a unix SOCKET too, so SetNamedPipeHandleState(socket, byte reads, blocking)
now succeeds where it failed (it fails on Windows); byte reads and blocking is what a stream socket does, and no program is known to
depend on that failure.

No BCD log has "open EBADF" (0 of the 3,881 log files of 2026-10-20 to 2026-11-10), so there is no auto-tune rule. Keyless: only a call that
failed changes, and only for the state the pipe already has.

Checks (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none, or when the one there
lacks the change and is not this checkout's pin): the FilePipeInformation case of NtSetInformationFile, cut from the Wine source and compiled
on the host with a stand-in server and volume query, over every server status (success, invalid handle, type mismatch, access denied), device
type (named pipe, disk, the query failing), ReadMode and CompletionMode (0, 1, 2) and buffer length: the answer changes from the server's
only for (invalid handle or type mismatch) + named pipe + both modes 0, to success; an invalid mode never reaches the server; a short buffer
is STATUS_INVALID_PARAMETER_3; the device type is not asked when the server succeeded. And the CI runs this.
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


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


pin = ''
tree = git('ls-tree', 'HEAD', 'wine')
if tree:
    pin = tree.split()[2]
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'dlls/ntdll/unix/file.c').is_file():
        wine = Path(candidate)
        break
file_c = (wine / 'dlls/ntdll/unix/file.c').read_text() if wine else ''
has_change = 'madeira-pipe-state-fd-backed' in file_c
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int NTSTATUS; typedef void *HANDLE; typedef unsigned int ULONG; typedef unsigned long ULONG_PTR;
typedef struct { NTSTATUS Status; ULONG_PTR Information; } IO_STATUS_BLOCK;
typedef struct { ULONG ReadMode; ULONG CompletionMode; } FILE_PIPE_INFORMATION;
typedef struct { ULONG DeviceType; ULONG Characteristics; } FILE_FS_DEVICE_INFORMATION;
#define STATUS_SUCCESS 0
#define STATUS_INVALID_PARAMETER ((NTSTATUS)0xc000000d)
#define STATUS_INVALID_PARAMETER_3 ((NTSTATUS)0xc00000f1)
#define STATUS_INVALID_HANDLE ((NTSTATUS)0xc0000008)
#define STATUS_OBJECT_TYPE_MISMATCH ((NTSTATUS)0xc0000024)
#define FILE_DEVICE_NAMED_PIPE 0x11
#define FILE_DEVICE_DISK 0x07
#define FileFsDeviceInformation 4
#define NAMED_PIPE_NONBLOCKING_MODE 1
#define NAMED_PIPE_MESSAGE_STREAM_READ 2
static NTSTATUS server_status; static int server_calls, server_flags, volume_calls, volume_fails; static ULONG device_type;
struct req { int handle; int flags; };
#define SERVER_START_REQ(name) { struct req _req, *req = &_req;
#define SERVER_END_REQ }
static int wine_server_obj_handle( HANDLE h ) { return 1; }
static NTSTATUS wine_server_call( struct req *req ) { server_calls++; server_flags = req->flags; return server_status; }
static NTSTATUS NtQueryVolumeInformationFile( HANDLE h, IO_STATUS_BLOCK *io, void *buf, ULONG len, int cls )
{
    volume_calls++;
    if (cls != FileFsDeviceInformation || len != sizeof(FILE_FS_DEVICE_INFORMATION)) abort();
    if (volume_fails) return STATUS_INVALID_HANDLE;
    ((FILE_FS_DEVICE_INFORMATION *)buf)->DeviceType = device_type;
    return STATUS_SUCCESS;
}
static NTSTATUS set_pipe_information( HANDLE handle, void *ptr, ULONG len )
{
    NTSTATUS status = 0x7777;
    switch (1)
    {
    case 1:
CASE_BODY
    }
    return status;
}
int main( int argc, char **argv )
{
    FILE_PIPE_INFORMATION info = { (ULONG)atoi( argv[2] ), (ULONG)atoi( argv[3] ) };
    NTSTATUS st;
    server_status = (NTSTATUS)strtoul( argv[1], NULL, 16 );
    device_type = (ULONG)atoi( argv[4] ); volume_fails = atoi( argv[5] );
    st = set_pipe_information( (HANDLE)1, &info, (ULONG)atoi( argv[6] ) );
    printf( "%08x %d %d %d\n", (unsigned int)st, server_calls, server_flags, volume_calls );
    return 0;
}
'''

if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    start = file_c.index('    case FilePipeInformation:\n        if (len >= sizeof(FILE_PIPE_INFORMATION))')
    body = file_c[file_c.index('\n', start) + 1:file_c.index('    case FileMailslotSetInformation:', start)]
    check('the case is the one cut (it ends with its own break)', body.rstrip().endswith('break;') and body.count('SERVER_START_REQ( set_named_pipe_info )') == 1)
    check('BCD\'s follow-up is there: only byte reads and the blocking mode are accepted for a unix pipe',
          '(status == STATUS_INVALID_HANDLE || status == STATUS_OBJECT_TYPE_MISMATCH) &&\n                !info->CompletionMode && !info->ReadMode)' in body)
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='pipe-state-unix-fd-'))
    try:
        (tmp / 't.c').write_text(HARNESS.replace('CASE_BODY', body))
        r = subprocess.run([cc, '-std=gnu11', '-O1', '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('the case compiles on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            bad = []
            n = changed = 0
            for server in (0, 0xc0000008, 0xc0000024, 0xc0000022):
                for read_mode in (0, 1, 2):
                    for completion in (0, 1, 2):
                        for device, vfail in ((0x11, 0), (0x07, 0), (0x11, 1)):
                            for length in (8, 4):
                                out = subprocess.run([str(tmp / 't'), '%x' % server, str(read_mode), str(completion), str(device), str(vfail), str(length)],
                                                     capture_output=True, text=True).stdout.split()
                                st, calls, flags, vcalls = int(out[0], 16), int(out[1]), int(out[2]), int(out[3])
                                n += 1
                                if length < 8:
                                    want = (0xc00000f1, 0, 0)
                                elif (read_mode | completion) & ~1:
                                    want = (0xc000000d, 0, 0)
                                else:
                                    rejected = server in (0xc0000008, 0xc0000024)
                                    accept = rejected and not read_mode and not completion and device == 0x11 and not vfail
                                    changed += accept
                                    want = (0 if accept else server, 1, 1 if (rejected and not read_mode and not completion) else 0)
                                    if flags != (completion * 1 | read_mode * 2):
                                        bad.append(('flags', out))
                                if (st, calls, vcalls) != want:
                                    bad.append((hex(server), read_mode, completion, device, vfail, length, out, want))
            check('%d combinations: the answer is the server\'s except for a rejected handle that is a pipe by device type and is asked for byte reads and '
                  'blocking (%d cases, now success); the device type is asked only then; invalid modes and short buffers never reach the server'
                  % (n, changed), not bad and changed == 2, str(bad[:3]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-pipe-state-unix-fd.py' in ci)

print('PASS: pipe state on a unix descriptor' if ok else 'FAILED: pipe state on a unix descriptor')
sys.exit(0 if ok else 1)
