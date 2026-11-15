#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""SetupCommitFileQueueW with setupapi's default callback no longer asks for the same disc for ever (build 660; Wine fork commit
ef6c5cf183e of upstream Madeira's fork, ml1310, narrowed by BCD's fork commit "the media-loop change applies to setupapi's own default
callback only"); no Wine runs.

When a queued copy fails with "file not found" or "path not found" while its source media is not resolved yet, Wine's
SetupCommitFileQueueW goes back to the top of its loop and sends SPFILENOTIFY_NEEDMEDIA again. setupapi's own default callback
(SetupDefaultQueueCallbackA/W) answers "same place" (FILEOP_DOIT) to every NEEDMEDIA without looking at anything, so with it the loop
never ends: the install hangs. Madeira turns the missing file into a copy error (SPFILENOTIFY_COPYERROR) for EVERY callback.

BCD narrows that. "Same place" is also what a program's own callback answers after it has shown its "insert the disc" prompt, or
while it counts retries before it answers something else; Windows asks such a callback again, and with Madeira's change it would
get a copy error first, which some callbacks answer with an abort. So by default the change applies only where the old path
provably never ended: the callback is SetupDefaultQueueCallbackW, or SetupDefaultQueueCallbackA behind SetupCommitFileQueueA's
wrapper (the default callback answers the copy error with FILEOP_SKIP). Every other callback is asked again, as before.
env.MADEIRA_SETUPAPI_MEDIA_FIX: unset = that; 1 = every callback (Madeira's behaviour); 0 = never (Wine's).

Who gets it: 32-bit programs (the i386 farm is built from the Wine pin) and a 64-bit game with setupapi in env.MADEIRA_OPTIN_DLLS
(setupapi-optin.dll is built from the same tree; today Dying Light and Dead Island Definitive, which list devices and commit no file
queue). The shipped 64-bit setupapi.dll is a committed binary BCD does not replace: a 64-bit installer still loops. No BCD log shows
the loop (an installer that hangs here logs nothing), so there is no auto-tune rule.

Checks (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none, or when the one there
lacks the change and is not this checkout's pin): the media loop and madeira_media_fix_applies, cut from the Wine source and compiled on
the host with a scripted callback and a copy that fails:
  1. setupapi's default callback (W, and A behind the wrapper): one question, one copy error, the loop ends;
  2. a program's own callback (W, and A behind the wrapper) is asked again exactly as in Wine: "same place" for ever is asked until the
     harness stops it, and a callback that retries twice and then skips, names a new path or aborts gets all its questions and never a
     copy error (old and new loops give the same line for every such script);
  3. the switch: 1 gives every callback the copy error, 0 gives the default callback the endless question, any other value (empty, 10,
     yes, 0000) is "unset";
  4. the other paths as before (a new path is asked again, another error goes to the copy error, a copy that succeeds resolves the
     media), the real default callback's answers by text, BCD's setupapi device-node-lock script still applies, and with the repo's
     llvm-mingw and generated headers (the full tree) queue.c compiles for i686 and x86_64. The CI runs this.
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
    if candidate and (Path(candidate) / 'dlls/setupapi/queue.c').is_file():
        wine = Path(candidate)
        break
queue_c = (wine / 'dlls/setupapi/queue.c').read_text() if wine else ''
has_change = 'madeira_media_fix_applies' in queue_c
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
typedef wchar_t WCHAR; typedef unsigned int UINT; typedef unsigned long UINT_PTR; typedef int BOOL; typedef unsigned int DWORD; typedef void *PVOID;
#define TRUE 1
#define FALSE 0
#define MAX_PATH 260
#define CALLBACK
#define WINAPI
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define SPFILENOTIFY_STARTCOPY 0xb
#define SPFILENOTIFY_ENDCOPY 0xc
#define SPFILENOTIFY_COPYERROR 0xd
#define SPFILENOTIFY_NEEDMEDIA 0xe
#define FILEOP_COPY 0
#define FILEOP_ABORT 0
#define FILEOP_DOIT 1
#define FILEOP_SKIP 2
#define FILEOP_NEWPATH 4
#define SP_COPY_WARNIFSKIP 0x4000
#define SP_COPY_NOSKIP 0x80
#define SP_FLAG_CABINETCONTINUATION 0x800
#define SP_COPY_NOBROWSE 0x8000
#define ERROR_FILE_NOT_FOUND 2
#define ERROR_PATH_NOT_FOUND 3
#define ERROR_ACCESS_DENIED 5
#define FIXME(...) do { } while (0)
#define ERR(...) do { errs++; } while (0)
#define debugstr_w(s) "x"
#define lstrcpyW wcscpy
#define lstrlenW wcslen
#define wcsnicmp wcsncasecmp
typedef struct { void *Reserved; const WCHAR *Tagfile, *Description, *SourcePath, *SourceFile; DWORD Flags; } SOURCE_MEDIA_W;
typedef struct { const WCHAR *Target, *Source; UINT Win32Error; DWORD Flags; } FILEPATHS_W;
struct source_media { WCHAR root[MAX_PATH]; WCHAR *tag, *desc; BOOL resolved, cabinet; };
struct file_op { DWORD style; WCHAR *src_path, *src_file; struct source_media *media; };
typedef UINT (CALLBACK *PSP_FILE_CALLBACK_W)( PVOID, UINT, UINT_PTR, UINT_PTR );
typedef UINT (CALLBACK *PSP_FILE_CALLBACK_A)( PVOID, UINT, UINT_PTR, UINT_PTR );
struct callback_WtoA_context { void *orig_context; PSP_FILE_CALLBACK_A orig_handler; };
static int errs, needmedia, copyerror, startcopy, endcopy, copies, cap_hit;
static int media_answers[16], n_media_answers, error_answer, copy_error, copy_ok_at;
/* the program's own callback: scripted answers */
static UINT scripted( UINT note, UINT_PTR b )
{
    if (note == SPFILENOTIFY_NEEDMEDIA)
    {
        int ans = media_answers[needmedia < n_media_answers ? needmedia : n_media_answers - 1];
        if (++needmedia > 1000) { cap_hit = 1; return FILEOP_ABORT; }
        if (ans == FILEOP_NEWPATH) wcscpy( (WCHAR *)b, L"D:\\other" );
        return ans;
    }
    if (note == SPFILENOTIFY_STARTCOPY) { startcopy++; return FILEOP_DOIT; }
    if (note == SPFILENOTIFY_ENDCOPY) { endcopy++; return 0; }
    if (note == SPFILENOTIFY_COPYERROR) { copyerror++; return error_answer; }
    abort();
}
static UINT CALLBACK own_callback_W( PVOID ctx, UINT note, UINT_PTR a, UINT_PTR b ) { return scripted( note, b ); }
static UINT CALLBACK own_callback_A( PVOID ctx, UINT note, UINT_PTR a, UINT_PTR b ) { return scripted( note, b ); }
/* setupapi's default callbacks, with the two answers the real ones give (checked by text below) */
static UINT default_answers( UINT note )
{
    if (note == SPFILENOTIFY_NEEDMEDIA) { if (++needmedia > 1000) { cap_hit = 1; return FILEOP_ABORT; } return FILEOP_DOIT; }
    if (note == SPFILENOTIFY_COPYERROR) { copyerror++; return FILEOP_SKIP; }
    if (note == SPFILENOTIFY_STARTCOPY) { startcopy++; return FILEOP_DOIT; }
    if (note == SPFILENOTIFY_ENDCOPY) { endcopy++; return 0; }
    abort();
}
UINT WINAPI SetupDefaultQueueCallbackW( PVOID ctx, UINT note, UINT_PTR a, UINT_PTR b ) { return default_answers( note ); }
UINT WINAPI SetupDefaultQueueCallbackA( PVOID ctx, UINT note, UINT_PTR a, UINT_PTR b ) { return default_answers( note ); }
UINT CALLBACK QUEUE_callback_WtoA( void *context, UINT note, UINT_PTR a, UINT_PTR b )
{ struct callback_WtoA_context *c = context; return c->orig_handler( c->orig_context, note, a, b ); }
static UINT last_error;
static UINT GetLastError( void ) { return last_error; }
static BOOL queue_copy_file( const WCHAR *src, const WCHAR *dst, struct file_op *op, PSP_FILE_CALLBACK_W h, void *ctx )
{ if (++copies == copy_ok_at) return TRUE; last_error = copy_error; return 0; }
static void build_filepathsW( struct file_op *op, FILEPATHS_W *paths ) { paths->Source = L"s"; paths->Target = L"t"; paths->Win32Error = 0; }
/* Windows' contract: the characters stored (without the NUL), or the size needed (with it) when the buffer is too small */
static DWORD GetEnvironmentVariableW( const WCHAR *name, WCHAR *buf, DWORD size )
{
    const char *e = getenv( "MADEIRA_SETUPAPI_MEDIA_FIX" ); DWORD i, n;
    if (wcscmp( name, L"MADEIRA_SETUPAPI_MEDIA_FIX" )) abort();
    if (!e) return 0;
    n = (DWORD)strlen( e );
    if (n + 1 > size) return n + 1;
    for (i = 0; i <= n; i++) buf[i] = (WCHAR)e[i];
    return n;
}
HELPER
static int run_loop( struct file_op *op, PSP_FILE_CALLBACK_W handler, void *context )
{
    FILEPATHS_W paths; UINT op_result; WCHAR newpath[MAX_PATH]; WCHAR src_path[MAX_PATH]; size_t path_len = 0;
    src_path[0] = 0;
LOOP
    return 1;
done:
    return 0;
}
/* argv: callback (0 the program's own W, 1 its own A behind the wrapper, 2 setupapi's default W, 3 its default A behind the wrapper),
 *       NEEDMEDIA answers of the program's callback (one digit each, the last repeats), its COPYERROR answer, the copy's error,
 *       which copy succeeds (0 = none) */
int main( int argc, char **argv )
{
    struct source_media media; struct file_op op; struct callback_WtoA_context wtoa; int i, r, which = atoi( argv[1] );
    memset( &media, 0, sizeof(media) ); memset( &op, 0, sizeof(op) );
    wcscpy( media.root, L"C:\\src" ); op.media = &media; op.src_file = L"a.dll";
    for (i = 0; argv[2][i]; i++) media_answers[n_media_answers++] = argv[2][i] - '0';
    error_answer = atoi( argv[3] ); copy_error = atoi( argv[4] ); copy_ok_at = atoi( argv[5] );
    wtoa.orig_context = NULL; wtoa.orig_handler = which == 3 ? SetupDefaultQueueCallbackA : own_callback_A;
    if (which == 0) r = run_loop( &op, own_callback_W, NULL );
    else if (which == 2) r = run_loop( &op, SetupDefaultQueueCallbackW, NULL );
    else r = run_loop( &op, QUEUE_callback_WtoA, &wtoa );
    printf( "%d needmedia=%d copyerror=%d copies=%d endcopy=%d resolved=%d cap=%d errs=%d root=%ls\n",
            r, needmedia, copyerror, copies, endcopy, media.resolved, cap_hit, errs, media.root );
    return 0;
}
"""

# Wine 11.4's loop for comparison: the same text with "ask again" where the fork decides (LGPL-2.1-or-later, the Wine authors')
if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    f = queue_c[queue_c.index('BOOL WINAPI SetupCommitFileQueueW('):]
    start = f.index('                for (;;)\n                {\n                    SOURCE_MEDIA_W media;')
    end = f.index('\n                }\n            }\n            else\n', start)
    loop = f[start:end + len('\n                }\n')]
    h0 = queue_c.find('static BOOL madeira_media_fix_applies( PSP_FILE_CALLBACK_W handler, void *context )\n{')
    helper = queue_c[h0:queue_c.index('\n}\n', h0) + 3] if h0 >= 0 else ''
    check('the media loop and madeira_media_fix_applies are in the Wine source',
          bool(helper) and loop.count('SPFILENOTIFY_NEEDMEDIA') == 1 and loop.count('SPFILENOTIFY_COPYERROR') == 1 and 'media_result = op_result;' in loop)
    decide = '                        if (media_result == FILEOP_NEWPATH || !madeira_media_fix_applies( handler, context ))\n                            continue;\n'
    check('the loop asks again after a new path, or when madeira_media_fix_applies says the change is not for this callback',
          loop.count(decide) == 1)
    check('the change applies to SetupDefaultQueueCallbackW, to SetupDefaultQueueCallbackA behind the wrapper, with 1 to every callback, with 0 to none',
          "if (len == 1 && value[0] == '0') return FALSE;" in helper and "if (len == 1 && value[0] == '1') return TRUE;" in helper
          and 'if (handler == SetupDefaultQueueCallbackW) return TRUE;' in helper
          and 'return ((struct callback_WtoA_context *)context)->orig_handler == SetupDefaultQueueCallbackA;' in helper
          and helper.rstrip().endswith('return FALSE;\n}'))
    for name in ('SetupDefaultQueueCallbackA', 'SetupDefaultQueueCallbackW'):
        d0 = queue_c.index('UINT WINAPI %s( PVOID context, UINT notification,' % name)
        d = queue_c[d0:queue_c.index('\n}\n', d0)]
        need = d[d.index('case SPFILENOTIFY_NEEDMEDIA:'):]
        cerr = d[d.index('case SPFILENOTIFY_COPYERROR:'):d.index('case SPFILENOTIFY_NEEDMEDIA:')]
        check('the real %s answers FILEOP_DOIT to every NEEDMEDIA and FILEOP_SKIP to a copy error' % name,
              'return FILEOP_DOIT;' in need[:need.index('default:')] and 'FILEOP_NEWPATH' not in need[:need.index('default:')]
              and 'return FILEOP_SKIP;' in cerr and 'FILEOP_ABORT' not in cerr)
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='setupapi-media-loop-'))
    try:
        exes = {}
        for name, lp, hp in (('new', loop, helper), ('wine', loop.replace(decide, '                        continue;\n'), '')):
            (tmp / (name + '.c')).write_text(HARNESS.replace('HELPER', hp).replace('LOOP', lp))
            r = subprocess.run([cc, '-std=gnu11', '-O1', '-Wno-unused-variable', '-Wno-unused-but-set-variable', '-Wno-unused-function',
                                '-o', str(tmp / name), str(tmp / (name + '.c'))], capture_output=True, text=True)
            check('the %s loop compiles on the host' % ('fork\'s' if name == 'new' else 'loop as Wine has it (always "ask again")'), r.returncode == 0, r.stderr[-1500:])
            exes[name] = tmp / name
        if all(p.exists() for p in exes.values()):
            def run(args, fix=None, exe='new'):
                e = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
                if fix is not None:
                    e['MADEIRA_SETUPAPI_MEDIA_FIX'] = fix
                return subprocess.run([str(exes[exe])] + [str(a) for a in args], env=e, capture_output=True, text=True).stdout.strip()
            # 1. the default callback: the case that never ended
            for which, name in ((2, 'SetupDefaultQueueCallbackW'), (3, 'SetupDefaultQueueCallbackA behind SetupCommitFileQueueA\'s wrapper')):
                o = run((which, '1', 2, 2, 0))
                w = run((which, '1', 2, 2, 0), exe='wine')
                check('%s and a missing file: Wine asks for ever (stopped at 1000 questions); now one question, one copy error (skipped), the loop ends' % name,
                      o.startswith('1 needmedia=1 copyerror=1 copies=1 ') and 'cap=0 errs=1' in o and 'cap=1' in w and 'copyerror=0' in w, o + ' | ' + w)
            o = run((2, '1', 2, 3, 0))
            check('the same for path not found', o.startswith('1 needmedia=1 copyerror=1 copies=1 ') and 'cap=0' in o, o)
            # 2. a program's own callback: asked again exactly as in Wine
            scripts = ['1', '112', '1112', '114', '1114', '110', '11110', '2', '0', '4', '41', '12', '14', '10']
            bad = []
            for which in (0, 1):
                for sc in scripts:
                    for err in (2, 3, 5):
                        for cerr in (2, 0, 1):
                            for okat in (0, 3):
                                a = (which, sc, cerr, err, okat)
                                if run(a) != run(a, exe='wine'):
                                    bad.append((a, run(a), run(a, exe='wine')))
            check('a program\'s own callback (W, and A behind the wrapper): %d scripts of answers (for ever, retry then skip, retry then a new path, retry then abort, ...) '
                  'with three errors, three copy-error answers and a late success give the same line from the fork\'s loop and from Wine\'s'
                  % (2 * len(scripts) * 3 * 3 * 2), not bad, str(bad[:2]))
            o = run((0, '1', 2, 2, 0))
            check('there "same place" for ever is asked for ever, as in Wine (no copy error, no log line)', 'cap=1' in o and 'copyerror=0' in o and 'errs=0' in o, o)
            o = run((0, '112', 0, 2, 0))
            check('a callback that retries twice and then skips gets its three questions and no copy error (with Madeira\'s form it got one question and a copy error, here answered with abort)',
                  o.startswith('1 needmedia=3 copyerror=0 copies=2 ') and run((0, '112', 0, 2, 0), fix='1').startswith('0 needmedia=1 copyerror=1 '), o)
            # 3. the switch
            o = run((0, '1', 2, 2, 0), fix='1')
            check('MADEIRA_SETUPAPI_MEDIA_FIX=1: a program\'s own callback gets the copy error too (Madeira\'s behaviour)',
                  o.startswith('1 needmedia=1 copyerror=1 copies=1 ') and 'errs=1' in o, o)
            o = run((2, '1', 2, 2, 0), fix='0')
            check('MADEIRA_SETUPAPI_MEDIA_FIX=0: the default callback is asked for ever again (Wine\'s behaviour)', 'cap=1' in o and 'copyerror=0' in o, o)
            same = [v for v in ('', '10', 'yes', '0000', '00', '1x', '11111') if run((2, '1', 2, 2, 0), fix=v) != run((2, '1', 2, 2, 0)) or run((0, '1', 2, 2, 0), fix=v) != run((0, '1', 2, 2, 0))]
            check('any other value (empty, 10, yes, 0000, 00, 1x, 11111) is "unset" for both kinds of callback', not same, str(same))
            # 4. the other paths
            o = run((2, '1', 2, 5, 0))
            check('another error (access denied) goes to the copy error without the new log line, as before',
                  o.startswith('1 needmedia=1 copyerror=1 copies=1 ') and 'errs=0' in o, o)
            o = run((2, '1', 2, 2, 1))
            check('a copy that succeeds resolves the media and ends the loop', o.startswith('1 needmedia=1 copyerror=0 copies=1 endcopy=1 resolved=1'), o)
            o = run((0, '41', 2, 2, 0), fix='1')
            check('with 1, a NEWPATH answer is still asked again once, with the new root, before the copy error',
                  o.startswith('1 needmedia=2 copyerror=1 copies=2 ') and 'root=D:\\other' in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # BCD's device-node-lock script (setupapi-optin.dll) on the changed tree: devinst.c is a different file, and it must still apply
    tmp = Path(tempfile.mkdtemp(prefix='setupapi-media-loop-lock-'))
    try:
        (tmp / 'dlls/setupapi').mkdir(parents=True)
        for name in os.listdir(wine / 'dlls/setupapi'):
            if name.endswith(('.c', '.h', '.spec', '.in')):
                shutil.copy(wine / 'dlls/setupapi' / name, tmp / 'dlls/setupapi' / name)
        r = subprocess.run([sys.executable, str(root / 'tools/patch-wine-setupapi-devinst-lock.py'), str(tmp)], capture_output=True, text=True)
        check('BCD\'s setupapi device-node-lock script still applies to the tree', r.returncode == 0, (r.stdout + r.stderr)[-300:])
        check('and it leaves queue.c alone', (tmp / 'dlls/setupapi/queue.c').read_text() == queue_c)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # the whole file, for the two targets that get it (the i386 farm, and x86_64 as the stand-in for the ARM64EC setupapi-optin.dll)
    tc = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
    if (tc / 'i686-w64-mingw32-clang').exists() and (wine / 'build-macos/include').is_dir():
        for tgt in ('i686', 'x86_64'):
            r = subprocess.run([str(tc / (tgt + '-w64-mingw32-clang')), '-fsyntax-only', '-O2', '-Wall', '-D__WINESRC__', '-D_SETUPAPI_', '-D_WIN32_WINNT=0x0A00',
                                '-I' + str(wine / 'dlls/setupapi'), '-I' + str(wine / 'build-macos/include'), '-I' + str(wine / 'include'),
                                str(wine / 'dlls/setupapi/queue.c')], capture_output=True, text=True)
            check('setupapi/queue.c compiles for %s' % tgt, r.returncode == 0 and ' error' not in r.stderr, r.stderr[-1500:])
    else:
        print('note: no llvm-mingw or generated headers in this tree; the compile runs in the full tree')

ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-setupapi-media-loop.py' in ci)

print('PASS: setupapi media loop' if ok else 'FAILED: setupapi media loop')
sys.exit(0 if ok else 1)
