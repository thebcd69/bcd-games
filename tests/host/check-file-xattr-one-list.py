#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A file's attributes from one list of its extended attributes instead of two (tools/patch-wine-file-xattr-one-list.py); no Wine runs.

get_file_info and fd_get_file_info read the reparse tag and then the DOS attributes of the same file, and on Apple each read lists
the file's attribute names first: two listxattr per directory entry and per attribute query. Valve's CEG job of a Dock start lists
c:\\windows\\system32 about 14 times a second for as long as it runs ("[dir-storm] ... (91% of the window) ... names=426", seven logs
of builds 653-665) and half of each listing is those two lists ([thread-sample] of 3XMZCZ: 21 samples in listxattr, 13 in stat, 6 in
lstat).

1. The patch script on a skeleton and on a copy of wine's dlls/ntdll/unix/file.c (the submodule or $WINE_SRC; never the original):
   the second run is a no-op, a moved anchor fails by name, both functions list once and read each name through the pair, the
   original reads stay for other platforms, and no other attribute read changes. A checkout CI has already patched is checked as is.
2. On an Apple host (the build runner): the real functions are cut out of the patched file and compiled twice, with WINE_IOS (the
   one-list path) and without it (the original statements), and both are run on real files of this machine: plain files, files that
   carry user.DOSATTRIB, user.WINEREPARSE or both, an unparsable and an empty value, a file whose attribute names do not fit the list
   buffer (ERANGE) with and without the two names, directories, a dot file, links to all of these, a dangling link, a missing path,
   and descriptors (with and without FILE_OPEN_REPARSE_POINT, and a closed one). The return value, errno on failure, struct stat, the
   attributes, the reparse tag and the warnings about an attribute that could not be read (the one thing the errno of a missing
   attribute decides) are identical; the old path makes two lists per file and the new one makes one; the value reads are the same
   in number. With MADEIRA_XATTR_ONE_LIST=0 the new build makes the old calls.
3. CI applies the patch before ntdll-unix is built and runs this check; the settings catalog documents the switch.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "tools/patch-wine-file-xattr-one-list.py"
WINE = Path(os.environ.get("WINE_SRC", ROOT / "wine"))
REAL = WINE / "dlls/ntdll/unix/file.c"
CI = (ROOT / ".github/workflows/build-ipa.yml").read_text(encoding="utf-8")
CAT = (ROOT / "app/Madeira/ConfigCatalog.generated.swift").read_text(encoding="utf-8")
MARKER = "madeira-bcd: madeira_xattr_pair"

failures = []


def check(cond, what, detail=""):
    print(("PASS " if cond else "FAIL ") + what + (("\n     " + detail) if (detail and not cond) else ""))
    if not cond:
        failures.append(what)


def func_body(src, signature):
    i = src.find(signature)
    if i < 0:
        return ""
    j = src.find("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    return ""


def apply(path):
    return subprocess.run([sys.executable, str(PATCH), str(path)], capture_output=True, text=True)


SKELETON = """static BOOL xattr_exists( const char **path, int *filedes, const char *name )
{
    return TRUE;
}

static int xattr_get( const char *path, const char *name, void *value, size_t size )
{
    return getxattr( path, name, value, size, 0, 0 );
}

static int xattr_fget( int filedes, const char *name, void *value, size_t size )
{
    return fgetxattr( filedes, name, value, size, 0, 0 );
}

static int fd_get_file_info( HANDLE handle, int fd, unsigned int options,
                             struct stat *st, ULONG *attr, ULONG *reparse_tag )
{
    attr_len = xattr_fget( fd, XATTR_REPARSE, buffer, sizeof(buffer) );
    if (attr_len >= 0) *attr |= FILE_ATTRIBUTE_REPARSE_POINT;

    attr_len = xattr_fget( fd, SAMBA_XATTR_DOS_ATTRIB, attr_data, sizeof(attr_data)-1 );
    return ret;
}

static int get_file_info( const char *path, struct stat *st, ULONG *attr, ULONG *reparse_tag )
{
    attr_len = xattr_get( path, XATTR_REPARSE, buffer, sizeof(buffer) );
    if (attr_len >= 0) *attr |= FILE_ATTRIBUTE_REPARSE_POINT;

    attr_len = xattr_get( path, SAMBA_XATTR_DOS_ATTRIB, attr_data, sizeof(attr_data)-1 );
    return ret;
}

static NTSTATUS get_reparse_point( HANDLE handle, REPARSE_DATA_BUFFER *buffer, ULONG *size )
{
    if ((size = xattr_fget( fd, XATTR_REPARSE, data, MAXIMUM_REPARSE_DATA_BUFFER_SIZE )) < 0)
        return errno_to_status( errno );
}
"""


def pins(patched, what):
    for sig, who in (("static int fd_get_file_info( HANDLE handle, int fd, unsigned int options,", "NULL, fd"),
                     ("static int get_file_info( const char *path, struct stat *st, ULONG *attr, ULONG *reparse_tag )", "path, -1")):
        b = func_body(patched, sig)
        name = sig.split("(")[0].split()[-1]
        check(b.count("madeira_xattr_pair_list( %s, XATTR_REPARSE, SAMBA_XATTR_DOS_ATTRIB, &madeira_xp );" % who) == 1 and
              b.count("madeira_xattr_pair_get( %s, &madeira_xp, 0, XATTR_REPARSE, buffer, sizeof(buffer) );" % who) == 1 and
              b.count("madeira_xattr_pair_get( %s, &madeira_xp, 1, SAMBA_XATTR_DOS_ATTRIB, attr_data, sizeof(attr_data)-1 );" % who) == 1 and
              b.index("madeira_xattr_pair_list(") < b.index("&madeira_xp, 0,") < b.index("&madeira_xp, 1,"),
              "%s: %s lists once and reads both names through the pair" % (what, name))
        check(b.count("#ifdef WINE_IOS\n") == 2 and b.count("#else\n") == 2 and
              len(re.findall(r"#else\n    attr_len = xattr_f?get\( (?:fd|path), (?:XATTR_REPARSE|SAMBA_XATTR_DOS_ATTRIB), ", b)) == 2,
              "%s: %s keeps the two original reads for every other platform" % (what, name))
    h = patched[patched.index(MARKER):]
    h = h[:h.index("\n#endif\n")]
    check(patched.count(MARKER) == 1 and patched.count("struct madeira_xattr_pair { int valid, has[2], err[2]; };") == 1 and
          h.count("listxattr( path, xattrs, sizeof(xattrs), 0 )") == 1 and h.count("flistxattr( filedes, xattrs, sizeof(xattrs), 0 )") == 1 and
          'getenv( "MADEIRA_XATTR_ONE_LIST" )' in h and "on = !(e && e[0] == '0' && !e[1]);" in h,
          "%s: the helper lists once (by path or by descriptor) and its switch is on unless it is exactly 0" % what)
    check(patched.index("static int xattr_fget( int filedes, const char *name, void *value, size_t size )") < patched.index(MARKER) <
          patched.index("static int fd_get_file_info( HANDLE handle, int fd, unsigned int options,"),
          "%s: the helper comes after xattr_fget and before its first user" % what)


with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    sk = t / "skeleton.c"
    sk.write_text(SKELETON)
    r1 = apply(sk)
    once = sk.read_text()
    r2 = apply(sk)
    check(r1.returncode == 0 and r2.returncode == 0 and "already patched" in r2.stdout and sk.read_text() == once,
          "the skeleton is patched once; a second run changes nothing", r1.stderr + r2.stderr)
    if r1.returncode == 0:
        pins(once, "skeleton")
        check(func_body(once, "static NTSTATUS get_reparse_point(") == func_body(SKELETON, "static NTSTATUS get_reparse_point("),
              "skeleton: another reader of one attribute (get_reparse_point) is left alone")
    for what, old, new in (("xattr_fget", "static int xattr_fget( int filedes,", "static int xattr_fgetX( int filedes,"),
                           ("xattr_exists", "static BOOL xattr_exists(", "static BOOL xattr_existsX("),
                           ("fd_get_file_info's first read", "    attr_len = xattr_fget( fd, XATTR_REPARSE, buffer, sizeof(buffer) );",
                            "    attr_len = xattr_fget( fd, XATTR_REPARSE, buffer, sizeof(buffer) ) ;"),
                           ("get_file_info's second read", "    attr_len = xattr_get( path, SAMBA_XATTR_DOS_ATTRIB, attr_data, sizeof(attr_data)-1 );",
                            "    attr_len = xattr_get( path, SAMBA_XATTR_DOS_ATTRIB, attr_data, sizeof(attr_data) );")):
        bad = t / "bad.c"
        bad.write_text(SKELETON.replace(old, new))
        r = apply(bad)
        check(r.returncode != 0 and bad.read_text() == SKELETON.replace(old, new), "a moved anchor (%s) fails and writes nothing" % what,
              r.stdout + r.stderr)

    real_src = None
    if REAL.exists():
        orig = REAL.read_text()
        cp = t / "file.c"
        cp.write_text(orig)
        r = apply(cp)
        patched = cp.read_text()
        already = MARKER in orig
        check(r.returncode == 0, "the patch applies to wine's file.c" + (" (already patched by CI)" if already else ""), r.stdout + r.stderr)
        if r.returncode == 0:
            pins(patched, "wine")
            if not already:
                check(patched.count("xattr_fget( fd, XATTR_REPARSE, data, MAXIMUM_REPARSE_DATA_BUFFER_SIZE )") ==
                      orig.count("xattr_fget( fd, XATTR_REPARSE, data, MAXIMUM_REPARSE_DATA_BUFFER_SIZE )") and
                      len(re.findall(r"\bxattr_f?get\(", patched)) == len(re.findall(r"\bxattr_f?get\(", orig)) + 2,
                      "wine: every other attribute read is unchanged (the two new calls are the helper's own fall-back)")
            real_src = patched
    else:
        print("note: no wine checkout (%s); set WINE_SRC to check the real file.c" % REAL)

    # ---- behaviour: the same functions with and without WINE_IOS, on real files ----
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if real_src and cc and sys.platform == "darwin":
        pieces = []
        for sig in ("static BOOL xattr_exists( const char **path, int *filedes, const char *name )",
                    "static int xattr_get( const char *path, const char *name, void *value, size_t size )",
                    "static int xattr_fget( int filedes, const char *name, void *value, size_t size )"):
            body = func_body(real_src, sig)
            check(bool(body), "extracted: " + sig.split("(")[0])
            pieces.append(body)
        a = real_src.rindex("#ifdef WINE_IOS", 0, real_src.index(MARKER))
        pieces.append(real_src[a:real_src.index("\n#endif\n", a) + 8])
        for sig in ("static BOOL is_hidden_file( const char *name )",
                    "static inline ULONG get_file_attributes( const struct stat *st )",
                    "static int parse_samba_dos_attrib_data( char *data, int len )",
                    "static BOOL fd_is_mount_point( int fd, const struct stat *st )",
                    "static int fd_get_file_info( HANDLE handle, int fd, unsigned int options,",
                    "static int get_file_info( const char *path, struct stat *st, ULONG *attr, ULONG *reparse_tag )"):
            body = func_body(real_src, sig)
            check(bool(body), "extracted: " + sig.split("(")[0])
            pieces.append(body)
        unit = r'''
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <unistd.h>
#define HAVE_SYS_XATTR_H 1
#define XATTR_ADDITIONAL_OPTIONS 1
typedef void *HANDLE;
typedef unsigned int ULONG;
typedef int BOOL;
#define TRUE 1
#define FALSE 0
#define FILE_ATTRIBUTE_READONLY 0x1
#define FILE_ATTRIBUTE_HIDDEN 0x2
#define FILE_ATTRIBUTE_SYSTEM 0x4
#define FILE_ATTRIBUTE_DIRECTORY 0x10
#define FILE_ATTRIBUTE_ARCHIVE 0x20
#define FILE_ATTRIBUTE_REPARSE_POINT 0x400
#define XATTR_ATTRIBS_MASK (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
#define IO_REPARSE_TAG_MOUNT_POINT 0xA0000003
#define IO_REPARSE_TAG_LX_SYMLINK 0xA000001D
#define FILE_OPEN_REPARSE_POINT 0x00200000
#define MAXIMUM_REPARSE_DATA_BUFFER_SIZE (16 * 1024)
#define XATTR_USER_PREFIX "user."
#define XATTR_USER_PREFIX_LEN (sizeof(XATTR_USER_PREFIX) - 1)
#define SAMBA_XATTR_DOS_ATTRIB XATTR_USER_PREFIX "DOSATTRIB"
#define XATTR_REPARSE XATTR_USER_PREFIX "WINEREPARSE"
#define FIXME(...) do {} while (0)
#define debugstr_a(s) (s)
static BOOL show_dot_files;
unsigned int NAME(lists), NAME(reads), NAME(warns);
#define WARN(...) do { NAME(warns)++; } while (0)     /* the one thing the errno of a missing attribute decides */
static ssize_t c_listxattr( const char *p, char *b, size_t s, int o ) { NAME(lists)++; return listxattr( p, b, s, o ); }
static ssize_t c_flistxattr( int f, char *b, size_t s, int o ) { NAME(lists)++; return flistxattr( f, b, s, o ); }
static ssize_t c_getxattr( const char *p, const char *n, void *v, size_t s, u_int32_t pos, int o ) { NAME(reads)++; return getxattr( p, n, v, s, pos, o ); }
static ssize_t c_fgetxattr( int f, const char *n, void *v, size_t s, u_int32_t pos, int o ) { NAME(reads)++; return fgetxattr( f, n, v, s, pos, o ); }
#define listxattr c_listxattr
#define flistxattr c_flistxattr
#define getxattr c_getxattr
#define fgetxattr c_fgetxattr
@PIECES@
int NAME(by_name)( const char *path, struct stat *st, ULONG *attr, ULONG *tag ) { return get_file_info( path, st, attr, tag ); }
int NAME(by_fd)( int fd, unsigned int options, struct stat *st, ULONG *attr, ULONG *tag ) { return fd_get_file_info( NULL, fd, options, st, attr, tag ); }
'''.replace("@PIECES@", "\n".join(pieces))
        (t / "new.c").write_text("#define WINE_IOS 1\n#define NAME(x) new_##x\n" + unit)
        (t / "old.c").write_text("#define NAME(x) old_##x\n" + unit)
        (t / "main.c").write_text(r'''
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <unistd.h>
typedef unsigned int ULONG;
extern unsigned int old_lists, old_reads, old_warns, new_lists, new_reads, new_warns;
int old_by_name( const char *, struct stat *, ULONG *, ULONG * ), new_by_name( const char *, struct stat *, ULONG *, ULONG * );
int old_by_fd( int, unsigned int, struct stat *, ULONG *, ULONG * ), new_by_fd( int, unsigned int, struct stat *, ULONG *, ULONG * );
static int bad, cases, off;
static unsigned int tot_old, tot_new, tot_warns;
static void same( const char *what, const char *path, int fd, unsigned int options )
{
    struct stat a, b;
    ULONG attr_a = 0x7777, attr_b = 0x5555, tag_a = 0x1111, tag_b = 0x2222;
    int ra, rb, ea, eb;
    unsigned int la, lb, ga, gb, wa, wb;

    memset( &a, 0x11, sizeof(a) ); memset( &b, 0x11, sizeof(b) );
    old_lists = old_reads = old_warns = new_lists = new_reads = new_warns = 0;
    errno = 0; ra = path ? old_by_name( path, &a, &attr_a, &tag_a ) : old_by_fd( fd, options, &a, &attr_a, &tag_a ); ea = errno;
    errno = 0; rb = path ? new_by_name( path, &b, &attr_b, &tag_b ) : new_by_fd( fd, options, &b, &attr_b, &tag_b ); eb = errno;
    la = old_lists; lb = new_lists; ga = old_reads; gb = new_reads; wa = old_warns; wb = new_warns;
    cases++; tot_old += la; tot_new += lb; tot_warns += wa;
    if (ra != rb || (ra == -1 && ea != eb) || attr_a != attr_b || (ra == 0 && (tag_a != tag_b || memcmp( &a, &b, sizeof(a) ))) || ga != gb ||
        wa != wb ||
        (off ? lb != la : (lb > 1 || (la == 2) != (lb == 1) || (la == 0) != (lb == 0))))
    {
        printf( "FAIL: %s: ret %d/%d errno %d/%d attr %x/%x tag %x/%x lists %u/%u value reads %u/%u warnings %u/%u\n", what, ra, rb, ea, eb,
                attr_a, attr_b, tag_a, tag_b, la, lb, ga, gb, wa, wb );
        bad = 1;
    }
    else printf( "PASS: %s: ret %d attr %#x tag %#x, lists %u -> %u, value reads %u\n", what, ra, attr_a, ra ? 0 : tag_a, la, lb, ga );
}
static void mk( const char *dir, const char *name, const char *dos, int dos_len, const char *tag, int tag_len, int many, char *path )
{
    int fd, i;
    snprintf( path, 1024, "%s/%s", dir, name );
    fd = open( path, O_CREAT | O_RDWR, 0644 );
    if (fd < 0 || write( fd, "0123456789", 10 ) != 10) { perror( path ); exit( 2 ); }
    for (i = 0; i < many; i++)    /* 40 names of 60 characters: the list of names is longer than xattr_exists's 1024 bytes */
    {
        char n[80];
        snprintf( n, sizeof(n), "user.padding-padding-padding-padding-padding-padding-%04d", i );
        if (fsetxattr( fd, n, "x", 1, 0, 0 )) { perror( "fsetxattr" ); exit( 3 ); }
    }
    if (dos && fsetxattr( fd, "user.DOSATTRIB", dos, dos_len, 0, 0 )) { perror( "fsetxattr" ); exit( 3 ); }
    if (tag && fsetxattr( fd, "user.WINEREPARSE", tag, tag_len, 0, 0 )) { perror( "fsetxattr" ); exit( 3 ); }
    close( fd );
}
int main( int argc, char **argv )
{
    char dir[] = "/tmp/xattr1XXXXXX", p[16][1024], link[1024], sub[1024], dot[1024];
    static const char *names[] = { "plain.bin", "hidden.bin", "system-hidden-reparse.bin", "reparse.bin", "garbage.bin", "empty-value.bin",
                                   "many.bin", "many-both.bin", "short-tag.bin" };
    int i, fd, closed;

    off = argc > 1;
    if (!mkdtemp( dir )) return 2;
    mk( dir, names[0], NULL, 0, NULL, 0, 0, p[0] );
    mk( dir, names[1], "0x2", 3, NULL, 0, 0, p[1] );
    mk( dir, names[2], "0x6", 3, "\x03\x00\x00\xa0rest", 8, 0, p[2] );
    mk( dir, names[3], NULL, 0, "\x0c\x00\x00\xa0", 4, 0, p[3] );
    mk( dir, names[4], "zz", 2, NULL, 0, 0, p[4] );
    mk( dir, names[5], "", 0, NULL, 0, 0, p[5] );
    mk( dir, names[6], NULL, 0, NULL, 0, 40, p[6] );
    mk( dir, names[7], "0x4", 3, "\x03\x00\x00\xa0", 4, 40, p[7] );
    mk( dir, names[8], "0x2", 3, "\x01\x02", 2, 0, p[8] );     /* a tag shorter than a ULONG is no reparse point */
    for (i = 0; i < 9; i++) same( names[i], p[i], -1, 0 );
    snprintf( sub, sizeof(sub), "%s/sub", dir ); mkdir( sub, 0755 );
    same( "a directory", sub, -1, 0 );
    setxattr( sub, "user.DOSATTRIB", "0x2", 3, 0, 0 );
    same( "a hidden directory", sub, -1, 0 );
    same( "the parent directory", dir, -1, 0 );
    mk( dir, ".dotfile", NULL, 0, NULL, 0, 0, dot );
    same( "a dot file (hidden by its name)", dot, -1, 0 );
    for (i = 0; i < 9; i++)
    {
        char what[64];
        snprintf( link, sizeof(link), "%s/link-%d", dir, i ); symlink( p[i], link );
        snprintf( what, sizeof(what), "a link to %s", names[i] );
        same( what, link, -1, 0 );
    }
    snprintf( link, sizeof(link), "%s/link-dir", dir ); symlink( sub, link );
    same( "a link to a directory (a reparse point)", link, -1, 0 );
    snprintf( link, sizeof(link), "%s/dangling", dir ); symlink( "/nonexistent/target", link );
    same( "a dangling link", link, -1, 0 );
    snprintf( link, sizeof(link), "%s/missing", dir );
    same( "a missing path", link, -1, 0 );
    chmod( p[1], 0 );
    same( "a file without any permission", p[1], -1, 0 );
    chmod( p[1], 0644 );
    for (i = 0; i < 9; i++)
    {
        char what[64];
        fd = open( p[i], O_RDONLY );
        snprintf( what, sizeof(what), "descriptor of %s", names[i] );
        same( what, NULL, fd, 0 );
        snprintf( what, sizeof(what), "descriptor of %s, as a reparse point", names[i] );
        same( what, NULL, fd, 0x00200000 );
        close( fd );
    }
    fd = open( sub, O_RDONLY | O_DIRECTORY );
    same( "descriptor of a directory", NULL, fd, 0 );
    same( "descriptor of a directory, as a reparse point (mount-point check)", NULL, fd, 0x00200000 );
    closed = dup( fd ); close( closed ); close( fd );
    same( "a closed descriptor", NULL, closed, 0 );
    same( "descriptor -1", NULL, -1, 0 );
    printf( "TOTAL %d cases: %u lists before, %u now (%u warnings about an attribute that could not be read, the same both ways)\n", cases,
            tot_old, tot_new, tot_warns );
    {
        char cmd[1100];
        snprintf( cmd, sizeof(cmd), "rm -rf '%s'", dir );
        if (strlen( dir ) > 12 && !strncmp( dir, "/tmp/xattr1", 11 )) system( cmd );
    }
    return bad;
}
''')
        flags = ["-std=gnu11", "-O1", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-sign-compare",
                 "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
        b = subprocess.run([cc] + flags + [str(t / "new.c"), str(t / "old.c"), str(t / "main.c"), "-o", str(t / "h")],
                           capture_output=True, text=True)
        check(b.returncode == 0, "the real functions compile on this host with and without WINE_IOS", b.stderr[-2500:])
        if not b.returncode:
            env = dict(os.environ)
            env.pop("MADEIRA_XATTR_ONE_LIST", None)
            r = subprocess.run([str(t / "h")], capture_output=True, text=True, env=env, timeout=120)
            print("\n".join("    " + l for l in r.stdout.strip().splitlines()))
            m = re.search(r"TOTAL (\d+) cases: (\d+) lists before, (\d+) now", r.stdout)
            check(r.returncode == 0 and m is not None and int(m.group(1)) == 48,
                  "one list answers every case as two did: the same result, errno, struct stat, attributes, reparse tag and value reads",
                  r.stderr[-800:])
            if m:
                check(int(m.group(2)) == 2 * int(m.group(3)) and int(m.group(3)) >= 40,
                      "the lists are halved (%s before, %s now)" % (m.group(2), m.group(3)))
            for what, tag in (("a hidden file", "PASS: hidden.bin: ret 0 attr 0x22 tag 0, lists 2 -> 1, value reads 1"),
                              ("both attributes", "PASS: system-hidden-reparse.bin: ret 0 attr 0x426 tag 0xa0000003, lists 2 -> 1, value reads 2"),
                              ("a plain file (no value read at all)", "PASS: plain.bin: ret 0 attr 0x20 tag 0, lists 2 -> 1, value reads 0"),
                              ("names that do not fit the list buffer (both values asked for, as before)",
                               "PASS: many-both.bin: ret 0 attr 0x424 tag 0xa0000003, lists 2 -> 1, value reads 2"),
                              ("a missing path (no list at all)", "PASS: a missing path: ret -1 attr 0 tag 0, lists 0 -> 0, value reads 0")):
                check(tag in r.stdout, "expected outcome: " + what)
            r0 = subprocess.run([str(t / "h"), "off"], capture_output=True, text=True, env=dict(env, MADEIRA_XATTR_ONE_LIST="0"), timeout=120)
            m0 = re.search(r"TOTAL (\d+) cases: (\d+) lists before, (\d+) now", r0.stdout)
            check(r0.returncode == 0 and m0 is not None and m0.group(2) == m0.group(3) and int(m0.group(1)) == 48,
                  "MADEIRA_XATTR_ONE_LIST=0: the new build makes the old calls (%s lists both ways)" % (m0.group(2) if m0 else "?"),
                  (r0.stdout[-600:] + r0.stderr[-600:]))
    elif real_src and cc:
        print("note: not an Apple host; the compiled comparison (listxattr, flistxattr) runs on the macOS build runner")

i_patch = CI.find("python3 tools/patch-wine-file-xattr-one-list.py wine/dlls/ntdll/unix/file.c")
i_build = CI.find("- name: Build ntdll-unix")
check(0 <= i_patch < i_build, "CI patches wine's file.c before ntdll-unix is built")
check("python3 tests/host/check-file-xattr-one-list.py" in CI and
      CI.find("python3 tests/host/check-file-xattr-one-list.py") < i_patch, "CI runs this check, before the patch step")
check(re.search(r'key: "env.MADEIRA_XATTR_ONE_LIST", title: "[^"]+", kind: \.bool, defaultValue: "1"', CAT) is not None,
      "the catalog documents env.MADEIRA_XATTR_ONE_LIST (default 1)")

print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
