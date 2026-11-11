#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Directory reads by path and one directory cache per process (the "Terraria set", build 632).

Terraria (Steam 105600) stopped at "Failed to load asset: PixelShader" with Content\\PixelShader.xnb on disk (six BCD logs,
builds 576-592: YFYT0R, 9HS7YY, KS07PK, 7BFE9R, 76XNE6, R7BAJJ). Two faults of ntdll's directory reads, both in
wine/dlls/ntdll/unix/file.c (the Wine fork's commits 9a64361cc and 3e1dcdfda, taken from upstream Madeira 0.2.0):

 1. NtQueryDirectoryFile read a directory with fchdir( fd ), opendir( "." ) and a stat of each name relative to ".". On iOS every
    pseudo-process and the wineserver thread share one unix cwd, so a listing could read another directory and an entry whose stat
    failed was skipped as "no longer exists" without a word. The readers now use the directory's absolute path (F_GETPATH) and leave
    the cwd alone; a directory whose path cannot be had takes the old fchdir read, and so does one whose path no longer names it
    (a BCD addition: a directory removed or replaced while open keeps F_GETPATH's old name); MADEIRA_DIR_BY_PATH=0 turns the new
    read off.
 2. dir_data_cache (the client half of the server's directory cache) was one array for every pseudo-process while the server hands
    out its slots per process: Terraria's Content handle got the slot of one of Steam's handles and read Steam's finished listing
    (STATUS_NO_MORE_FILES with nothing in it). Each process id now has its own array, switched in under dir_mutex, and
    madeira_dir_cache_flush_pid() (called from server_init_process_done, build/ntdll-unix/server_ios.c) drops what a dead process
    with a reissued id left behind.

Checks: (a) the helper block of 1. compiled on the host (AddressSanitizer): the absolute path of an open directory, the entries of
a listing found while the process's cwd is moved around by another thread (the old read, run the same way, is shown for contrast and
not asserted), the fallbacks (switch off, directory removed or replaced while open, path too long), a renamed directory; (b) the cache block of
2. compiled on the host against Wine's list.h: arrays are kept apart per process id, come back intact, a flushed id starts empty,
pid 0 is left alone, 5,000 random steps against a model; (c) the sources: every reader goes through the helper, the cwd is touched
only on the fallback, the hook is called once in server_init_process_done after the fastsync flush; (d) on the pinned Wine source
BCD's twelve file.c patch scripts still apply after the fork's commits, in the workflow's order; (e) the workflow runs this check.
"""
from pathlib import Path
import os
import importlib.util
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


file_c = root / 'wine/dlls/ntdll/unix/file.c'
server_c = root / 'build/ntdll-unix/server_ios.c'
wf = (root / '.github/workflows/build-ipa.yml').read_text()
srv = server_c.read_text()

# ---- (c) the server hook
call = 'madeira_dir_cache_flush_pid();'
body = srv[srv.index('void server_init_process_done(void)'):]
body = body[:body.index('\n}\n')]
check('server_init_process_done calls madeira_dir_cache_flush_pid() once, after the fastsync flush and before the removable-media chdir',
      body.count(call) == 1 and body.index('madeira_fast_flush_pid();') < body.index(call) < body.index('get_device_info( initial_cwd'),
      body[:400])
check('the hook is declared where it is called (the definition is in the Wine fork\'s file.c)',
      'extern void madeira_dir_cache_flush_pid(void);' in body)

if not file_c.is_file():
    print('SKIP: no wine sources (check out the submodule)')
    print('PASS' if ok else 'FAIL')
    raise SystemExit(0 if ok else 1)

src = file_c.read_text()


def block(start_comment):
    """the '#ifdef WINE_IOS' block that starts with this comment, up to its '#endif'"""
    i = src.index('#ifdef WINE_IOS\n/* iOS-Madeira: ' + start_comment)
    j = src.index('\n#endif\n', i)
    return src[i + len('#ifdef WINE_IOS\n'):j]


reader = block('every pseudo-process and the wineserver thread share one unix cwd')
cache = block('the client half of the directory cache')

# ---- (c) sources
def func(name, stop='\n}\n'):
    i = src.index(name)
    return src[i:src.index(stop, i)]


entry = func('static NTSTATUS get_dir_data_entry(')
getattr_ = func('static NTSTATUS read_directory_data_getattrlist(')
stat_ = func('static NTSTATUS read_directory_data_stat(')
readdir_ = func('static NTSTATUS read_directory_data_readdir(')
query = func('NTSTATUS WINAPI NtQueryDirectoryFile(')
cached = func('static unsigned int get_cached_dir_data(')
check('get_dir_data_entry stats the entry by its absolute path while a directory path is set',
      'path = ios_dir_entry_path( names->unix_name, full, sizeof(full) );' in entry and 'get_file_info( path, &st' in entry)
check('the getattrlist, stat and readdir readers use the directory path, not "."',
      'unix_name = ios_dir_entry_path( unix_name, full, sizeof(full) );' in getattr_
      and 'if (ios_dir_path[0]) dir = ios_dir_path;' in stat_ and 'path = ios_dir_entry_path( unix_name, full, sizeof(full) );' in stat_
      and 'opendir( ios_dir_path[0] ? ios_dir_path : "." )' in readdir_)
check('NtQueryDirectoryFile touches the cwd only when the path could not be had (the old read)',
      'by_path = ios_dir_read_by_path( fd );' in query and 'cwd = by_path ? -1 : open( ".", O_RDONLY );' in query
      and 'if (by_path || fchdir( fd ) != -1)' in query and 'if (!by_path && (cwd == -1 || fchdir( cwd ) == -1)) chdir( "/" );' in query
      and 'ios_dir_path[0] = 0;' in query.split('mutex_lock( &dir_mutex );')[1].split('mutex_unlock( &dir_mutex );')[0])
check('the path is set and cleared inside dir_mutex (the same lock that serialises the listing)',
      query.index('mutex_lock( &dir_mutex );') < query.index('ios_dir_read_by_path( fd )') < query.index('ios_dir_path[0] = 0;')
      < query.index('mutex_unlock( &dir_mutex );'))
check('get_cached_dir_data switches to the calling process\'s array before it asks the server for a slot',
      cached.index('ios_dir_cache_select()') < cached.index('SERVER_START_REQ( get_directory_cache_entry )'))
check('without WINE_IOS both new blocks are compiled out (every addition sits inside #ifdef WINE_IOS)',
      src.count('ios_dir_cache_select') == 2 and src.count('ios_dir_read_by_path') == 2
      and re.search(r'#ifdef WINE_IOS\n    if \(!ios_dir_cache_select\(\)\) return STATUS_NO_MEMORY;\n#endif', cached) is not None)

# ---- (a) the reader helper on the host
harness_a = r'''
#define _DARWIN_C_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <limits.h>
#include <unistd.h>
#include <dirent.h>
#include <pthread.h>
#include <sys/stat.h>
typedef int BOOL; typedef struct { unsigned long Information; } IO_STATUS_BLOCK; struct dir_data { unsigned int count, pos; };
#define TRUE 1
#define FALSE 0
''' + reader + r'''
static int fail;
#define EXPECT(c, ...) do { if (!(c)) { fail++; fprintf(stderr, "EXPECT failed line %d: ", __LINE__); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } } while (0)

static char *dirs[4];
static volatile int stop_mover;
/* another pseudo-process: open_fd() fchdir()s to a root directory and back, child launches and server connects do the same */
static void *mover(void *arg)
{
    int i = 0;
    (void)arg;
    while (!stop_mover) { if (chdir(dirs[2 + (i++ & 1)]) != 0) break; }
    return NULL;
}

/* the new read of NtQueryDirectoryFile: names from opendir( path ), entries stat'ed by absolute path; returns entries found, -1 when the path was not had */
static int list_by_path(const char *dir, unsigned int *gone)
{
    int fd = open(dir, O_RDONLY), n = 0;
    DIR *d;
    struct dirent *de;
    struct stat st;
    char full[PATH_MAX];

    if (fd < 0) return -2;
    if (!ios_dir_read_by_path(fd)) { close(fd); return -1; }
    d = opendir(ios_dir_path[0] ? ios_dir_path : ".");
    while ((de = readdir(d)))
    {
        const char *p;
        if (!strcmp(de->d_name, ".") || !strcmp(de->d_name, "..")) continue;
        p = ios_dir_entry_path(de->d_name, full, sizeof(full));
        if (lstat(p, &st) == -1) ios_dir_gone++; else n++;
    }
    closedir(d);
    *gone = ios_dir_gone;
    ios_dir_path[0] = 0;
    close(fd);
    return n;
}

/* the old read: fchdir( fd ), opendir( "." ), stat of each name relative to "." */
static int list_by_cwd(const char *dir, unsigned int *gone)
{
    int fd = open(dir, O_RDONLY), n = 0, cwd = open(".", O_RDONLY);
    DIR *d;
    struct dirent *de;
    struct stat st;

    *gone = 0;
    if (fchdir(fd) == -1) { close(fd); close(cwd); return -2; }
    d = opendir(".");
    while (d && (de = readdir(d)))
    {
        if (!strcmp(de->d_name, ".") || !strcmp(de->d_name, "..")) continue;
        if (lstat(de->d_name, &st) == -1) (*gone)++; else n++;
    }
    if (d) closedir(d);
    fchdir(cwd);
    close(cwd);
    close(fd);
    return n;
}

int main(int argc, char **argv)
{
    char root[PATH_MAX], real[PATH_MAX], tmp[PATH_MAX + 64];
    unsigned int gone = 0, old_gone_total = 0;
    int i, n, fd;
    pthread_t t;

    (void)argc;
    snprintf(root, sizeof(root), "%s", argv[1]);
    for (i = 0; i < 4; i++)
    {
        dirs[i] = malloc(PATH_MAX);
        snprintf(dirs[i], PATH_MAX, "%s/d%d", root, i);
        mkdir(dirs[i], 0755);
    }
    for (i = 0; i < 200; i++)   /* d0 holds 200 files, d1 two, the movers' directories others */
    {
        snprintf(tmp, sizeof(tmp), "%s/file%03d", dirs[0], i);
        close(open(tmp, O_CREAT | O_WRONLY, 0644));
    }
    for (i = 0; i < 2; i++)
    {
        snprintf(tmp, sizeof(tmp), "%s/other%d", dirs[1], i);
        close(open(tmp, O_CREAT | O_WRONLY, 0644));
    }
    for (i = 0; i < 20; i++)
    {
        snprintf(tmp, sizeof(tmp), "%s/mover%d", dirs[2], i);
        close(open(tmp, O_CREAT | O_WRONLY, 0644));
    }
    realpath(dirs[0], real);

    /* 1. the path of an open directory */
    fd = open(dirs[0], O_RDONLY);
    EXPECT(ios_dir_read_by_path(fd) == TRUE, "F_GETPATH on an open directory");
    EXPECT(!strcmp(ios_dir_path, real), "path %s != %s", ios_dir_path, real);
    EXPECT(ios_dir_gone == 0, "gone reset");
    {
        char buf[PATH_MAX];
        const char *p = ios_dir_entry_path("file007", buf, sizeof(buf));
        snprintf(tmp, sizeof(tmp), "%s/file007", real);
        EXPECT(p == buf && !strcmp(p, tmp), "entry path %s", p);
    }
    close(fd);
    ios_dir_path[0] = 0;

    /* 2. with no path set the entry is its own name (the old read's relative names) */
    {
        char buf[PATH_MAX];
        const char *nm = "file007";
        EXPECT(ios_dir_entry_path(nm, buf, sizeof(buf)) == nm, "no path: the name itself");
    }

    /* 3. a path that does not fit leaves the relative name */
    {
        char buf[16];
        const char *nm = "file007";
        fd = open(dirs[0], O_RDONLY);
        ios_dir_read_by_path(fd);
        EXPECT(ios_dir_entry_path(nm, buf, sizeof(buf)) == nm, "too long: the name itself");
        ios_dir_path[0] = 0;
        close(fd);
    }

    /* 4. a directory removed while open: F_GETPATH still answers its old name, but the path no longer names it, so the caller takes the
     *    fchdir read; the same when the name was made again as another directory */
    {
        char rm[PATH_MAX + 8], fill[PATH_MAX + 16];
        struct stat s1, s2;
        int k;
        snprintf(rm, sizeof(rm), "%s/gone", root);
        mkdir(rm, 0755);
        fd = open(rm, O_RDONLY);
        rmdir(rm);
        EXPECT(ios_dir_read_by_path(fd) == FALSE && ios_dir_path[0] == 0, "removed directory");
        close(fd);

        mkdir(rm, 0755);
        fd = open(rm, O_RDONLY);
        fstat(fd, &s1);
        rmdir(rm);
        for (k = 0; k < 3; k++) { snprintf(fill, sizeof(fill), "%s/fill%d", root, k); mkdir(fill, 0755); }
        mkdir(rm, 0755);
        stat(rm, &s2);
        if (s1.st_ino != s2.st_ino)
            EXPECT(ios_dir_read_by_path(fd) == FALSE && ios_dir_path[0] == 0, "removed and made again: another directory under the same name");
        else
            fprintf(stderr, "inode reused by this file system: replaced-directory case not asserted\n");
        close(fd);
    }

    /* 5. a directory renamed while open is found at its new path */
    {
        char a[PATH_MAX + 8], b[PATH_MAX + 8], rb[PATH_MAX];
        snprintf(a, sizeof(a), "%s/before", root);
        snprintf(b, sizeof(b), "%s/after", root);
        mkdir(a, 0755);
        fd = open(a, O_RDONLY);
        rename(a, b);
        realpath(b, rb);
        EXPECT(ios_dir_read_by_path(fd) == TRUE && !strcmp(ios_dir_path, rb), "renamed directory: %s", ios_dir_path);
        ios_dir_path[0] = 0;
        close(fd);
    }

    /* 6. the listing is complete while another thread keeps moving the process's cwd */
    stop_mover = 0;
    pthread_create(&t, NULL, mover, NULL);
    for (i = 0; i < 3000; i++)
    {
        n = list_by_path(dirs[0], &gone);
        EXPECT(n == 200 && gone == 0, "by path, round %d: %d entries, %u skipped", i, n, gone);
        if (n != 200) break;
        n = list_by_path(dirs[1], &gone);
        EXPECT(n == 2 && gone == 0, "by path (small dir), round %d: %d entries, %u skipped", i, n, gone);
        if (n != 2) break;
    }
    /* the old read, for contrast: its cwd is the movers' */
    for (i = 0; i < 300; i++) { list_by_cwd(dirs[0], &gone); old_gone_total += gone; }
    stop_mover = 1;
    pthread_join(t, NULL);
    fprintf(stderr, "old read under a moving cwd: %u of 300 x 200 entries skipped as gone (information only)\n", old_gone_total);

    printf("RESULT %s\n", fail ? "FAIL" : "OK");
    return fail ? 1 : 0;
}
'''
cc = os.environ.get('CC', 'cc')
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 'a.c'
    c.write_text(harness_a)
    exe = Path(tmp) / 'a'
    r = subprocess.run([cc, '-g', '-O1', '-Wall', '-Wno-unused-function', '-fsanitize=address,undefined', '-o', str(exe), str(c), '-lpthread'],
                       capture_output=True, text=True)
    check('the reader helpers compile on the host (-Wall, AddressSanitizer)', r.returncode == 0 and 'warning' not in r.stderr, r.stderr[-1500:])
    if r.returncode == 0:
        work = Path(tmp) / 'work'
        work.mkdir()
        env = dict(os.environ)
        env.pop('MADEIRA_DIR_BY_PATH', None)
        run = subprocess.run([str(exe), str(work)], capture_output=True, text=True, env=env, timeout=300)
        check('absolute path of an open directory, entry paths, fallbacks (removed or replaced directory / too long / no path), a renamed directory, '
              'and 3,000 complete listings while another thread moves the cwd',
              run.returncode == 0 and 'RESULT OK' in run.stdout, run.stderr[-1500:])
        info = [l for l in run.stderr.splitlines() if l.startswith('old read')]
        if info:
            print('     ' + info[0])
        # the switch off: nothing is read by path, the caller keeps the old fchdir read
        off = Path(tmp) / 'off.c'
        off.write_text(r'''
#define _DARWIN_C_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <limits.h>
#include <unistd.h>
#include <sys/stat.h>
typedef int BOOL; typedef struct { unsigned long Information; } IO_STATUS_BLOCK; struct dir_data { unsigned int count, pos; };
#define TRUE 1
#define FALSE 0
''' + reader + r'''
int main(int argc, char **argv)
{
    int fd = open(argv[1], O_RDONLY);
    char buf[PATH_MAX];
    BOOL r = ios_dir_read_by_path(fd);
    const char *nm = "x";
    printf("%d %d %d\n", r, ios_dir_path[0] != 0, ios_dir_entry_path(nm, buf, sizeof(buf)) == nm);
    return 0;
}
''')
        exe2 = Path(tmp) / 'off'
        r2 = subprocess.run([cc, '-O1', '-Wno-unused-function', '-o', str(exe2), str(off)], capture_output=True, text=True)
        if r2.returncode == 0:
            e0 = dict(os.environ, MADEIRA_DIR_BY_PATH='0')
            e1 = dict(os.environ, MADEIRA_DIR_BY_PATH='1')
            en = {k: v for k, v in os.environ.items() if k != 'MADEIRA_DIR_BY_PATH'}
            o0 = subprocess.run([str(exe2), tmp], capture_output=True, text=True, env=e0).stdout.split()
            o1 = subprocess.run([str(exe2), tmp], capture_output=True, text=True, env=e1).stdout.split()
            on = subprocess.run([str(exe2), tmp], capture_output=True, text=True, env=en).stdout.split()
            check('MADEIRA_DIR_BY_PATH=0 gives the old read (no path), "1" and unset give the new one',
                  o0 == ['0', '0', '1'] and o1 == ['1', '1', '0'] and on == ['1', '1', '0'], f'{o0} {o1} {on}')
        else:
            check('the switch harness compiles', False, r2.stderr[-800:])

# ---- (b) the per-process cache block on the host
harness_b = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
#include "wine/list.h"
typedef int BOOL; typedef uintptr_t ULONG_PTR;
#define TRUE 1
#define FALSE 0
typedef struct { struct { void *UniqueProcess; void *UniqueThread; } ClientId; } TEB;
static TEB the_teb; static int teb_null;
static TEB *NtCurrentTeb(void) { return teb_null ? NULL : &the_teb; }
struct dir_data { int serial; };
static struct dir_data **dir_data_cache;
static unsigned int dir_data_cache_size;
static pthread_mutex_t dir_mutex = PTHREAD_MUTEX_INITIALIZER;
#define mutex_lock pthread_mutex_lock
#define mutex_unlock pthread_mutex_unlock
static int freed, live;
static void free_dir_data(struct dir_data *d) { if (!d) return; freed++; live--; free(d); }
''' + cache + r'''
static int fail;
#define EXPECT(c, ...) do { if (!(c)) { fail++; fprintf(stderr, "EXPECT failed line %d: ", __LINE__); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } } while (0)

static void as_pid(unsigned int pid) { the_teb.ClientId.UniqueProcess = (void *)(uintptr_t)pid; }
/* what get_cached_dir_data does with the array after ios_dir_cache_select(): grow it and fill a slot */
static void put(unsigned int slot, int serial)
{
    if (slot >= dir_data_cache_size)
    {
        unsigned int size = slot + 8;
        struct dir_data **n = realloc(dir_data_cache, size * sizeof(*n));
        memset(n + dir_data_cache_size, 0, (size - dir_data_cache_size) * sizeof(*n));
        dir_data_cache = n;
        dir_data_cache_size = size;
    }
    if (dir_data_cache[slot]) free_dir_data(dir_data_cache[slot]);
    dir_data_cache[slot] = calloc(1, sizeof(struct dir_data));
    dir_data_cache[slot]->serial = serial;
    live++;
}

#define NPID 5
static int model[NPID][40];   /* serial per (pid index, slot), 0 = empty */
static unsigned int pid_of(int i) { return 100 + i * 7; }

int main(void)
{
    int i, step;
    unsigned int s;

    /* 1. two processes keep their arrays apart */
    as_pid(10);
    EXPECT(ios_dir_cache_select() && dir_data_cache == NULL && dir_data_cache_size == 0, "first select: empty array");
    put(3, 1001);
    as_pid(20);
    EXPECT(ios_dir_cache_select() && dir_data_cache == NULL && dir_data_cache_size == 0, "another process starts empty (the old shared array would hold pid 10's slot 3)");
    put(3, 2001);
    EXPECT(dir_data_cache[3]->serial == 2001, "pid 20 slot 3");
    as_pid(10);
    EXPECT(ios_dir_cache_select() && dir_data_cache_size >= 4 && dir_data_cache[3] && dir_data_cache[3]->serial == 1001, "pid 10 gets its own slot 3 back");
    EXPECT(ios_dir_cache_select(), "same pid again is a no-op");

    /* 2. a new process with the id of a dead one drops what the dead one left (server_init_process_done) */
    as_pid(20);
    madeira_dir_cache_flush_pid();      /* called from the new pid-20 process's first thread */
    EXPECT(freed == 1 && live == 1, "flush freed pid 20's one listing (freed=%d live=%d)", freed, live);
    EXPECT(ios_dir_cache_select() && dir_data_cache == NULL && dir_data_cache_size == 0, "pid 20 starts empty after the flush");
    as_pid(10);
    EXPECT(ios_dir_cache_select() && dir_data_cache[3] && dir_data_cache[3]->serial == 1001, "the flush did not touch pid 10");

    /* 3. flushing the current process resets the globals */
    madeira_dir_cache_flush_pid();
    EXPECT(dir_data_cache == NULL && dir_data_cache_size == 0 && freed == 2 && live == 0, "flush of the current pid");
    EXPECT(ios_dir_cache_select() && dir_data_cache == NULL, "and it starts empty");

    /* 4. pid 0 (no ClientId yet) is not flushed; no TEB at all selects pid 0 */
    as_pid(0);
    ios_dir_cache_select();
    put(1, 5);
    madeira_dir_cache_flush_pid();
    EXPECT(live == 1 && dir_data_cache && dir_data_cache[1], "pid 0 flush is a no-op");
    teb_null = 1;
    EXPECT(ios_dir_cache_select(), "no TEB behaves as pid 0");
    teb_null = 0;

    /* 5. random steps against a model: select / put / flush, five process ids */
    srand(12345);
    memset(model, 0, sizeof(model));
    for (i = 0; i < NPID; i++) { as_pid(pid_of(i)); madeira_dir_cache_flush_pid(); }
    for (step = 0; step < 5000; step++)
    {
        int p = rand() % NPID, op = rand() % 10, slot = rand() % 40;
        as_pid(pid_of(p));
        if (op < 6)
        {
            EXPECT(ios_dir_cache_select(), "select");
            put(slot, step + 1);
            model[p][slot] = step + 1;
        }
        else if (op < 9)
        {
            EXPECT(ios_dir_cache_select(), "select");
            for (s = 0; s < 40; s++)
            {
                int have = s < dir_data_cache_size && dir_data_cache[s] ? dir_data_cache[s]->serial : 0;
                EXPECT(have == model[p][s], "step %d pid %u slot %u: have %d want %d", step, pid_of(p), s, have, model[p][s]);
            }
        }
        else
        {
            madeira_dir_cache_flush_pid();
            memset(model[p], 0, sizeof(model[p]));
        }
        if (fail) break;
    }
    for (i = 0; i < NPID; i++) { as_pid(pid_of(i)); madeira_dir_cache_flush_pid(); }
    as_pid(0); madeira_dir_cache_flush_pid();
    printf("RESULT %s\n", fail ? "FAIL" : "OK");
    return fail ? 1 : 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 'b.c'
    c.write_text(harness_b)
    exe = Path(tmp) / 'b'
    r = subprocess.run([cc, '-g', '-O1', '-Wall', '-Wno-unused-function', '-fsanitize=address,undefined', '-I', str(root / 'wine/include'),
                        '-o', str(exe), str(c), '-lpthread'], capture_output=True, text=True)
    check('the per-process cache block compiles on the host against Wine\'s list.h (-Wall, AddressSanitizer)',
          r.returncode == 0 and 'warning' not in r.stderr, r.stderr[-1500:])
    if r.returncode == 0:
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
        check('arrays are kept apart per process id and come back intact; a flushed id starts empty; pid 0 is left alone; 5,000 random '
              'steps agree with the model; nothing leaks (AddressSanitizer)',
              run.returncode == 0 and 'RESULT OK' in run.stdout, run.stdout[-300:] + run.stderr[-1500:])

# ---- (d) BCD's thirteen file.c patch scripts after the fork's commits, in the workflow's order
order = ['guest-log', 'guest-log-tag', 'guest-log-service', 'guest-log-renpy', 'guest-log-trace', 'guest-log-redact', 'file-trace',
         'file-census', 'globalroot', 'app-local-dll', 'file-info-fstat', 'hide-host-version-plist', 'dir-storm']
wf_order = [m.group(1) for m in re.finditer(r'python3 tools/patch-wine-([a-z-]+)\.py wine/dlls/ntdll/unix/file\.c', wf)]
# tools/patch-wine-guest-log-secrets.py (W07) may sit after the account-name script; nothing else may be added or moved
check('the workflow applies exactly these thirteen scripts to file.c, in this order (and the sign-in-value script after the account names, when present)',
      [s_ for s_ in wf_order if s_ != 'guest-log-secrets'] == order and (('guest-log-secrets' not in wf_order) or
                                                                       wf_order.index('guest-log-secrets') == wf_order.index('guest-log-redact') + 1), str(wf_order))
order = wf_order
with tempfile.TemporaryDirectory() as t:
    f = Path(t) / 'file.c'
    f.write_text(src)
    bad = []
    for s_ in order:
        r = subprocess.run([sys.executable, str(root / 'tools' / f'patch-wine-{s_}.py'), str(f)], capture_output=True, text=True)
        if r.returncode:
            bad.append(s_ + ': ' + (r.stdout + r.stderr).strip()[-160:])
    check('all the workflow\'s file.c scripts apply on the Wine fork\'s file.c with the directory-read commits (none fails)', not bad, '\n     '.join(bad))
    patched = f.read_text()
    # night round 2026-11-11 (A2 S4): tools/patch-wine-dir-storm.py adds log-only timing to NtQueryDirectoryFile; take exactly its
    # additions out again before comparing, so every other line of the reads must still be the fork's.
    spec = importlib.util.spec_from_file_location('dir_storm', root / 'tools/patch-wine-dir-storm.py')
    ds = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ds)
    # (In a build the Wine checkout may already carry it when this runs, so the fork's blocks above are compared the same way.)
    def without_dir_storm(text):
        for new, old in ((ds.HELPER, ''), (ds.LOCALS_NEW, ds.LOCALS_OLD), (ds.START_NEW, ds.START_OLD), (ds.END_NEW, ds.END_OLD)):
            text = text.replace(new, old)
        return text
    patched = without_dir_storm(patched)
    check('and the patched file still holds the new reads and the cache block (the scripts touched neither)',
          all(without_dir_storm(b) in patched for b in (reader, cache, query)))

# ---- (e) the workflow
check('the workflow runs this check', 'python3 tests/host/check-dir-by-path.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
