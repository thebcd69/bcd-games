#!/usr/bin/env python3
"""Size, index and end-of-file queries from one fstat (tools/patch-wine-file-info-fstat.py);
no Wine runs.

NtQueryInformationFile answered FileStandardInformation (GetFileSizeEx),
FileInternalInformation and FileEndOfFileInformation through fd_get_file_info: an fstat
and then two extended-attribute reads (each an flistxattr first on Apple) for attributes
these classes never use. madeira-d3d12's shader cache asks for the size of every file it
reads, ~940 a second in Horizon Zero Dawn's menu (build 493, 2026-10-11).

1. Patches a skeleton and, when present, a copy of wine's dlls/ntdll/unix/file.c (the
   submodule or $WINE_SRC; never the original): the second run is a no-op, a missing
   anchor fails, the three cases call madeira_fd_stat_only under WINE_IOS and keep
   fd_get_file_info otherwise, no other case changes, and fill_file_info's three cases do
   not read the attributes. A checkout that CI has already patched is checked as is.
2. Compiles the real fd_get_file_info, its helpers and the patched madeira_fd_stat_only on
   this host (Linux xattrs; the Apple-only flistxattr pre-check is the same "does it
   exist" question) and runs both on real descriptors - a file, an empty file, a
   directory, a file and a directory carrying user.DOSATTRIB and user.WINEREPARSE where
   the filesystem allows it, a closed descriptor - with and without
   FILE_OPEN_REPARSE_POINT: the same return value, the same errno on failure and the same
   struct stat. With MADEIRA_FILE_INFO_FULL=1 it is fd_get_file_info itself (attributes
   included).
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "tools/patch-wine-file-info-fstat.py"
WINE = Path(os.environ.get("WINE_SRC", ROOT / "wine"))
REAL = WINE / "dlls/ntdll/unix/file.c"
CI = (ROOT / ".github/workflows/build-ipa.yml").read_text(encoding="utf-8")
CAT = (ROOT / "app/Madeira/ConfigCatalog.generated.swift").read_text(encoding="utf-8")

failures = []


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what)
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


skeleton = """static int fd_get_file_info( HANDLE handle, int fd, unsigned int options,
                             struct stat *st, ULONG *attr, ULONG *reparse_tag )
{
    return fstat( fd, st );
}

static NTSTATUS fill_file_info( const struct stat *st, ULONG attr, void *ptr,
                                FILE_INFORMATION_CLASS class )
{
    switch (class)
    {
    case FileStandardInformation:
        info->EndOfFile.QuadPart = st->st_size;
        break;
    case FileInternalInformation:
        info->IndexNumber.QuadPart = st->st_ino;
        break;
    case FileEndOfFileInformation:
        info->EndOfFile.QuadPart = st->st_size;
        break;
    }
    return 0;
}

/******************************************************************************
 *              NtQueryInformationFile   (NTDLL.@)
 */
NTSTATUS WINAPI NtQueryInformationFile( HANDLE handle, IO_STATUS_BLOCK *io,
                                        void *ptr, LONG len, FILE_INFORMATION_CLASS class )
{
    switch (class)
    {
    case FileBasicInformation:
        if (fd_get_file_info( handle, fd, options, &st, &attr, NULL ) == -1)
            status = errno_to_status( errno );
        break;
    case FileStandardInformation:
        {
            FILE_STANDARD_INFORMATION *info = ptr;

            if (fd_get_file_info( handle, fd, options, &st, &attr, NULL ) == -1)
                status = errno_to_status( errno );
        }
        break;
    case FileInternalInformation:
        if (fd_get_file_info( handle, fd, options, &st, &attr, NULL ) == -1)
            status = errno_to_status( errno );
        break;
    case FileEndOfFileInformation:
        if (fd_get_file_info( handle, fd, options, &st, &attr, NULL ) == -1)
            status = errno_to_status( errno );
        break;
    }
    return status;
}
"""

with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    sk = t / "skeleton.c"
    sk.write_text(skeleton)
    r1 = apply(sk)
    once = sk.read_text()
    r2 = apply(sk)
    check(r1.returncode == 0 and r2.returncode == 0 and "already patched" in r2.stdout and sk.read_text() == once,
          "the skeleton is patched once; a second run changes nothing")
    check(once.count("if (madeira_fd_stat_only( handle, fd, options, &st, &attr ) == -1)") == 3 and
          once.count("#else\n") >= 3,
          "the three cases call madeira_fd_stat_only under WINE_IOS")
    basic = func_body(once, "NTSTATUS WINAPI NtQueryInformationFile(")
    check(basic.find("case FileBasicInformation:\n        if (fd_get_file_info(") > 0,
          "FileBasicInformation (which needs the attributes) is left alone")
    bad = t / "bad.c"
    bad.write_text(skeleton.replace("    case FileInternalInformation:\n", "    case FileInternalInformationX:\n"))
    r = apply(bad)
    check(r.returncode != 0 and "anchor not found" in (r.stderr + r.stdout), "a moved anchor fails loudly")
    uses = t / "uses.c"
    uses.write_text(skeleton.replace("info->IndexNumber.QuadPart = st->st_ino;", "info->IndexNumber.QuadPart = attr;"))
    r = apply(uses)
    check(r.returncode != 0 and "uses the attributes" in (r.stderr + r.stdout),
          "the patch refuses a fill_file_info that reads the attributes for these classes")

    real_src = None
    if REAL.exists():
        orig = REAL.read_text()
        cp = t / "file.c"
        cp.write_text(orig)
        r = apply(cp)
        patched = cp.read_text()
        already = "madeira-bcd: madeira_fd_stat_only" in orig
        check(r.returncode == 0, "the patch applies to wine's file.c" + (" (already patched by CI)" if already else ""))
        q = func_body(patched, "NTSTATUS WINAPI NtQueryInformationFile( HANDLE handle, IO_STATUS_BLOCK *io,")
        check(q.count("madeira_fd_stat_only( handle, fd, options, &st, &attr ) == -1") == 3,
              "wine's NtQueryInformationFile answers the three classes through madeira_fd_stat_only")
        if not already:
            check(patched.replace("\n", "").count("fd_get_file_info(") == orig.replace("\n", "").count("fd_get_file_info(") + 1,
                  "every other fd_get_file_info call is unchanged (plus the switch's own call)")
        ff = func_body(patched, "static NTSTATUS fill_file_info( const struct stat *st, ULONG attr, void *ptr,")
        for cls in ("FileStandardInformation", "FileInternalInformation", "FileEndOfFileInformation"):
            i = ff.find("    case %s:" % cls)
            j = ff.find("break;", i)
            check(i > 0 and "attr" not in ff[i:j], "fill_file_info's %s case does not read the attributes" % cls)
        real_src = patched
    else:
        print("note: no wine checkout (%s); set WINE_SRC to check the real file.c" % REAL)

    # ---- behaviour: the real fd_get_file_info against madeira_fd_stat_only ----
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if real_src and cc:
        pieces = []
        xe = func_body(real_src, "static BOOL xattr_exists( const char **path, int *filedes, const char *name )")
        check(bool(xe), "extracted: static BOOL xattr_exists (the Apple pre-check)")
        pieces.append("#ifdef __APPLE__\n" + xe + "\n#endif")
        # Round 670: in the build, file.c already carries tools/patch-wine-file-xattr-one-list.py (applied before this check), whose
        # fd_get_file_info calls the one-list helpers: they come along, exactly as the patch wrote them (their #ifdef WINE_IOS block).
        mark = "/* madeira-bcd: madeira_xattr_pair"
        if mark in real_src:
            start = real_src.rindex("#ifdef WINE_IOS\n", 0, real_src.index(mark))
            end = real_src.index("\n#endif\n", start) + len("\n#endif\n")
            helper = real_src[start:end]
            check("madeira_xattr_pair_get(" in helper and helper.count("#ifdef") == 1,
                  "extracted: the one-list helpers of the xattr patch (build order)")
        else:
            helper = None
        sigs = ["static inline ULONG get_file_attributes( const struct stat *st )",
                "static int parse_samba_dos_attrib_data( char *data, int len )",
                "static BOOL fd_is_mount_point( int fd, const struct stat *st )",
                "static int xattr_fget( int filedes, const char *name, void *value, size_t size )",
                "static int fd_get_file_info( HANDLE handle, int fd, unsigned int options,",
                "static int madeira_fd_stat_only( HANDLE handle, int fd, unsigned int options, struct stat *st, ULONG *attr )"]
        if helper:   # the helpers read through xattr_get and xattr_fget: after both, before fd_get_file_info
            sigs.insert(sigs.index("static int xattr_fget( int filedes, const char *name, void *value, size_t size )"),
                        "static int xattr_get( const char *path, const char *name, void *value, size_t size )")
            sigs.insert(sigs.index("static int fd_get_file_info( HANDLE handle, int fd, unsigned int options,"), helper)
        for sig in sigs:
            if sig is helper:
                pieces.append(helper)
                continue
            body = func_body(real_src, sig)
            check(bool(body), "extracted: " + sig.split("(")[0])
            pieces.append(body)
        harness = r'''
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <unistd.h>
#define HAVE_SYS_XATTR_H 1
#define WINE_IOS 1
#ifdef __APPLE__
#define XATTR_ADDITIONAL_OPTIONS 1   /* fgetxattr/fsetxattr take position and options here */
#define FSETX( fd, n, v, s ) fsetxattr( fd, n, v, s, 0, 0 )
#else
#define FSETX( fd, n, v, s ) fsetxattr( fd, n, v, s, 0 )
#endif
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
#define FILE_OPEN_REPARSE_POINT 0x00200000
#define MAXIMUM_REPARSE_DATA_BUFFER_SIZE (16 * 1024)
#define XATTR_USER_PREFIX "user."
#define XATTR_USER_PREFIX_LEN (sizeof(XATTR_USER_PREFIX) - 1)
#define SAMBA_XATTR_DOS_ATTRIB XATTR_USER_PREFIX "DOSATTRIB"
#define XATTR_REPARSE XATTR_USER_PREFIX "WINEREPARSE"
#define WARN(...) do {} while (0)
#define FIXME(...) do {} while (0)
@PIECES@
static int bad;
static void same( int fd, unsigned int options, const char *what )
{
    struct stat a, b;
    ULONG attr_a = 0, attr_b = 0x55;
    int ra, rb, ea, eb;
    memset( &a, 0x11, sizeof(a) ); memset( &b, 0x11, sizeof(b) );
    errno = 0; ra = fd_get_file_info( NULL, fd, options, &a, &attr_a, NULL ); ea = errno;
    errno = 0; rb = madeira_fd_stat_only( NULL, fd, options, &b, &attr_b ); eb = errno;
    if (ra != rb || (ra == -1 && ea != eb) || (ra == 0 && memcmp( &a, &b, sizeof(a) )) || (ra == 0 && attr_b != 0))
    {
        printf( "FAIL: %s (ret %d/%d errno %d/%d attr %x)\n", what, ra, rb, ea, eb, attr_b );
        bad = 1;
    }
    else printf( "PASS: %s: same %s\n", what, ra ? "failure and errno" : "struct stat" );
}
int main( int argc, char **argv )
{
    char dir[] = "/tmp/fstatXXXXXX", path[4096];
    int f1, f2, d1, d2, closed, xattrs;
    if (!mkdtemp( dir )) return 2;
    snprintf( path, sizeof(path), "%s/a.bin", dir ); f1 = open( path, O_CREAT | O_RDWR, 0644 );
    if (write( f1, "0123456789", 10 ) != 10) return 2;
    snprintf( path, sizeof(path), "%s/empty", dir ); f2 = open( path, O_CREAT | O_RDWR, 0444 );
    snprintf( path, sizeof(path), "%s/sub", dir ); mkdir( path, 0755 ); d1 = open( path, O_RDONLY | O_DIRECTORY );
    d2 = open( dir, O_RDONLY | O_DIRECTORY );
    xattrs = !FSETX( f1, SAMBA_XATTR_DOS_ATTRIB, "0x2", 3 ) && !FSETX( d1, XATTR_REPARSE, "\x03\x00\x00\xa0", 4 );
    closed = dup( f1 ); close( closed );
    if (argc > 1)   /* MADEIRA_FILE_INFO_FULL=1: it is fd_get_file_info */
    {
        struct stat a, b; ULONG x = 0, y = 0;
        fd_get_file_info( NULL, f1, 0, &a, &x, NULL );
        madeira_fd_stat_only( NULL, f1, 0, &b, &y );
        printf( "%s: MADEIRA_FILE_INFO_FULL=1 gives fd_get_file_info's attributes (%x)\n", x == y ? "PASS" : "FAIL", y );
        return x != y;
    }
    same( f1, 0, "a file" );
    same( f1, FILE_OPEN_REPARSE_POINT, "a file opened as a reparse point" );
    same( f2, 0, "an empty read-only file" );
    same( d1, 0, "a directory" );
    same( d1, FILE_OPEN_REPARSE_POINT, "a directory opened as a reparse point (mount-point check)" );
    same( d2, FILE_OPEN_REPARSE_POINT, "the parent directory as a reparse point" );
    same( closed, 0, "a closed descriptor" );
    same( -1, 0, "descriptor -1" );
    printf( "%s: user xattrs %s on this filesystem\n", "NOTE", xattrs ? "set" : "not supported" );
    return bad;
}
'''.replace("@PIECES@", "\n".join(pieces))
        (t / "h.c").write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Wno-unused-function", "-Wno-unused-variable",
                            "-Wno-sign-compare", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            str(t / "h.c"), "-o", str(t / "h")], capture_output=True, text=True)
        check(b.returncode == 0, "the real fd_get_file_info and madeira_fd_stat_only compile on this host" +
              ("" if not b.returncode else ": " + b.stderr[-1500:]))
        if not b.returncode:
            env = dict(os.environ)
            env.pop("MADEIRA_FILE_INFO_FULL", None)
            r = subprocess.run([str(t / "h")], capture_output=True, text=True, env=env, timeout=60)
            print("\n".join("    " + l for l in r.stdout.strip().splitlines()))
            check(r.returncode == 0, "fstat alone answers every case as fd_get_file_info does" +
                  ("" if not r.returncode else ": " + r.stderr[-800:]))
            r = subprocess.run([str(t / "h"), "full"], capture_output=True, text=True,
                               env=dict(env, MADEIRA_FILE_INFO_FULL="1"), timeout=60)
            print("\n".join("    " + l for l in r.stdout.strip().splitlines()))
            check(r.returncode == 0, "MADEIRA_FILE_INFO_FULL=1 restores fd_get_file_info")

i_patch = CI.find("python3 tools/patch-wine-file-info-fstat.py wine/dlls/ntdll/unix/file.c")
i_build = CI.find("- name: Build ntdll-unix")
check(0 <= i_patch < i_build, "CI patches wine's file.c before ntdll-unix is built")
check("python3 tests/host/check-file-info-fstat.py" in CI, "CI runs this check")
check(re.search(r'key: "env.MADEIRA_FILE_INFO_FULL", title: "[^"]+", kind: \.bool, defaultValue: "0"', CAT) is not None,
      "the catalog documents env.MADEIRA_FILE_INFO_FULL (kill switch, default 0)")

print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
