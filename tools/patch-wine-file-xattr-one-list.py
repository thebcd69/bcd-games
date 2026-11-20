#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A file's attributes from ONE list of its extended attributes instead of two (engine review 2026-11-18).

get_file_info (a file by name: every directory entry a listing returns, every attribute query) and fd_get_file_info (a file by
descriptor) read two extended attributes of the same file one after the other: the reparse tag (user.WINEREPARSE), then the DOS
attributes (user.DOSATTRIB). On Apple each read first lists the file's attribute names (xattr_exists: "getxattr() is significantly
slower than listxattr()"), so every file costs two listxattr of the same file, and on iOS each is a path lookup the sandbox checks.

Where it shows: Valve's CEG job of a BCD Dock start. The Steam Client Service lists c:\\windows\\system32 (401-447 names, links into
the app) again and again, about 14 listings a second, for as long as the job runs: "[dir-storm] r652 10s: calls=1531
ms-in-calls=9112 (91% of the window) ... names=426 dir=.../drive_c/windows/system32" in seven logs of builds 653-665 (3XMZCZ Call of
Duty: Black Ops 221 s inside listings, AZXX6X Hitman: Absolution 161 s, FQ738T Spec Ops: The Line 71 s), and the job ends with result
55 at 101.5-102.5 s or 3 at 60.0 s. [thread-sample] of the service's thread: 21 samples in listxattr <- xattr_get, 13 in stat and 6
in lstat <- get_file_info: half of a listing is the two lists. In the fleet's CPU profile listxattr <- xattr_get is 0.5 % of all
sampled time (64 logs), 1.6 % in builds 640 and later.

With this, one list answers both names: madeira_xattr_pair_list lists once and records for each name what xattr_exists would have
said (listed; or the list did not fit, ERANGE, so the value is asked for as before) and the errno it would have left;
madeira_xattr_pair_get then reads the value only of a listed name. The values, the return values and errno are those of the two
separate reads; only the second list is gone. env.MADEIRA_XATTR_ONE_LIST = 0 (read once) lists once per name again.

Applied to the wine checkout (wine/dlls/ntdll/unix/file.c) by CI before ntdll-unix is built; the submodule is not committed to.
Idempotent (marker "madeira-bcd: madeira_xattr_pair"); every anchor is checked before the file is written.
Usage: patch-wine-file-xattr-one-list.py [path/to/wine/dlls/ntdll/unix/file.c]
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/file.c"
src = open(path).read()
MARKER = "madeira-bcd: madeira_xattr_pair"
if MARKER in src:
    print("patch-wine-file-xattr-one-list: already patched")
    sys.exit(0)


def fail(what):
    sys.exit("patch-wine-file-xattr-one-list: " + what)


HELPER = r'''
#ifdef WINE_IOS
/* ''' + MARKER + r''' (tools/patch-wine-file-xattr-one-list.py, engine review 2026-11-18). get_file_info and fd_get_file_info read
 * two extended attributes of one file, the reparse tag and then the DOS attributes, and each read listed the file's attribute names
 * first (xattr_exists): two lists of the same file for every directory entry and every attribute query. One list answers both.
 * has[n] is what xattr_exists would have returned for name n (listed; or the list did not fit, ERANGE: ask for the value as before),
 * err[n] the errno it would have left. MADEIRA_XATTR_ONE_LIST=0 lists once per name again. */
struct madeira_xattr_pair { int valid, has[2], err[2]; };

static int madeira_xattr_one_list(void)
{
    static int on = -1;

    if (on < 0)
    {
        const char *e = getenv( "MADEIRA_XATTR_ONE_LIST" );
        on = !(e && e[0] == '0' && !e[1]);
    }
    return on;
}

static void madeira_xattr_pair_list( const char *path, int filedes, const char *name0, const char *name1,
                                     struct madeira_xattr_pair *pair )
{
    char xattrs[1024];
    ssize_t i = 0, ret;

    pair->valid = 0;
    if (!madeira_xattr_one_list()) return;
    if (path)
        ret = listxattr( path, xattrs, sizeof(xattrs), 0 );
    else
        ret = flistxattr( filedes, xattrs, sizeof(xattrs), 0 );
    pair->valid = 1;
    if (ret == -1)
    {
        pair->has[0] = pair->has[1] = errno == ERANGE;
        pair->err[0] = pair->err[1] = errno;
        return;
    }
    pair->has[0] = pair->has[1] = 0;
    pair->err[0] = pair->err[1] = ENOATTR;
    while (i < ret)
    {
        if (!strcmp( name0, &xattrs[i] )) pair->has[0] = 1;
        if (!strcmp( name1, &xattrs[i] )) pair->has[1] = 1;
        i += strlen(&xattrs[i]) + 1;
    }
}

/* xattr_get (path) or xattr_fget (no path) for name number n of a listed pair */
static int madeira_xattr_pair_get( const char *path, int filedes, const struct madeira_xattr_pair *pair, int n,
                                   const char *name, void *value, size_t size )
{
    if (!pair->valid)
        return path ? xattr_get( path, name, value, size ) : xattr_fget( filedes, name, value, size );
    if (!pair->has[n])
    {
        errno = pair->err[n];
        return -1;
    }
    return path ? getxattr( path, name, value, size, 0, 0 ) : fgetxattr( filedes, name, value, size, 0, 0 );
}
#endif
'''

# the helper goes right after xattr_fget (it calls xattr_get and xattr_fget)
sig = "static int xattr_fget( int filedes, const char *name, void *value, size_t size )\n{"
if src.count(sig) != 1:
    fail("xattr_fget anchor not found")
if src.count("static BOOL xattr_exists( const char **path, int *filedes, const char *name )") != 1:
    fail("xattr_exists anchor not found (the Apple pre-check this patch folds into one list)")
end = src.index("\n}\n", src.index(sig)) + 3

EDITS = (
    # (function signature, first read, second read, the pair's file argument)
    ("static int fd_get_file_info( HANDLE handle, int fd, unsigned int options,",
     "    attr_len = xattr_fget( fd, XATTR_REPARSE, buffer, sizeof(buffer) );\n",
     "    attr_len = xattr_fget( fd, SAMBA_XATTR_DOS_ATTRIB, attr_data, sizeof(attr_data)-1 );\n",
     "NULL, fd"),
    ("static int get_file_info( const char *path, struct stat *st, ULONG *attr, ULONG *reparse_tag )",
     "    attr_len = xattr_get( path, XATTR_REPARSE, buffer, sizeof(buffer) );\n",
     "    attr_len = xattr_get( path, SAMBA_XATTR_DOS_ATTRIB, attr_data, sizeof(attr_data)-1 );\n",
     "path, -1"),
)
out = src[:end] + HELPER + src[end:]
for signature, first, second, who in EDITS:
    if out.count(signature) != 1:
        fail("anchor not found: " + signature.split("(")[0].strip())
    a = out.index(signature)
    b = out.index("\n}\n", a)
    body = out[a:b]
    if body.count(first) != 1 or body.count(second) != 1 or body.index(first) > body.index(second):
        fail("the two attribute reads of %s are not where they were" % signature.split("(")[0].split()[-1])
    body = body.replace(first,
                        "#ifdef WINE_IOS\n"
                        "    struct madeira_xattr_pair madeira_xp;\n"
                        "    madeira_xattr_pair_list( %s, XATTR_REPARSE, SAMBA_XATTR_DOS_ATTRIB, &madeira_xp );\n"
                        "    attr_len = madeira_xattr_pair_get( %s, &madeira_xp, 0, XATTR_REPARSE, buffer, sizeof(buffer) );\n"
                        "#else\n%s#endif\n" % (who, who, first))
    body = body.replace(second,
                        "#ifdef WINE_IOS\n"
                        "    attr_len = madeira_xattr_pair_get( %s, &madeira_xp, 1, SAMBA_XATTR_DOS_ATTRIB, attr_data, sizeof(attr_data)-1 );\n"
                        "#else\n%s#endif\n" % (who, second))
    out = out[:a] + body + out[b:]

open(path, "w").write(out)
print("patch-wine-file-xattr-one-list: get_file_info and fd_get_file_info list a file's extended attributes once for both names "
      "(MADEIRA_XATTR_ONE_LIST=0 lists once per name)")
