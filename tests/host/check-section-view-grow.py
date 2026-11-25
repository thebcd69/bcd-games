#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions of the idea and of the source anchors: willfaust/Madeira pull request #632 by Dragooon (tests/host/check-section-view-grow.py there).
"""section-view-grow (build/ntdll-unix/virtual_ios.c, build/wineserver/mapping_ios.c): a MEM_RESERVE view of a file section that is
longer than the file is mapped by extending the file, instead of STATUS_INVALID_VIEW_SIZE.

Path of Exile 2's LMDB caches (Shaders, Pipelines) map their whole map size (0x1900000000) over a data file of two pages; BCD's own log of
that game (build 625, KQBXZH) shows the two refusals as [nt-fail] NtMapViewOfSection status=c000001f. The change must run only where the
old code returned that status, so everything that works today is untouched.

1. Source anchors in virtual_map_section: the grow test is inside the branch that returned STATUS_INVALID_VIEW_SIZE (the only other
   remaining return of that status in the function is the fall-through), never for an image; the cap cannot overflow; the file is
   extended only after map_view reserved the range and before the file is mapped; a failed extension deletes the view and keeps the error;
   a commit in a view the code grew succeeds and any other file view keeps STATUS_ALREADY_COMMITTED.
2. The wineserver's map_view lets the section follow the file, before its range check, for file-backed non-image sections only.
3. The helper region (between the sectiongrow-test markers) is compiled on the host with stand-ins for Wine's types and driven on real
   files: a grown file reaches the end of the view and stays sparse; a file that is not the section's (another size, a pipe, an unlinked
   file, no descriptor) is refused and left alone; the file is never shortened; the table of 16 grown views refuses a 17th live one,
   reuses the slot of a view that is gone and the slot of the same base; the log keeps its first 16 lines, says CAPPED, names the end of the
   path and not the whole path, and warns at three quarters of a capped view; MADEIRA_SECTION_VIEW_GROW (default on, 0 turns it off,
   only for MEM_RESERVE) and MADEIRA_SECTION_VIEW_CAP_GB (default 16, 1-48) are read as documented.
4. The catalog overlay names both keys, the credit is in the three notices files, and the check is wired into the workflow.
5. With a wine checkout (WINE_SRC, default ./wine) and the iOS SDK, virtual_ios.c and mapping_ios.c still compile with the build's flags.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
virt = (root / "build/ntdll-unix/virtual_ios.c").read_text()
srv = (root / "build/wineserver/mapping_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ((" -- " + str(detail)[-1500:]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def body(text, start, end="\n}\n"):
    i = text.index(start)
    return text[i:text.index(end, i) + len(end)]


# ---- 1. virtual_map_section and allocate_virtual_memory -------------------------------------------------------------
ms = body(virt, "static unsigned int virtual_map_section(")
gate = ms.index("if (size > full_size - offset.QuadPart)")
ref = ms.index("return STATUS_INVALID_VIEW_SIZE;", gate)
grow_test = ms.index("ios_section_view_grow_ok( alloc_type )", gate)
check("the grow test sits inside the old refusal branch, before its return", gate < grow_test < ref
      and ms[gate:grow_test].count("{") == 1, ms[gate:ref][-600:])
check("only a non-image section is grown", "!image_info && ios_section_view_grow_ok( alloc_type )" in ms)
check("the refusal stays as the fall-through (one return of that status in the function)",
      ms.count("return STATUS_INVALID_VIEW_SIZE;") == 1 and "            else\n#endif\n            return STATUS_INVALID_VIEW_SIZE;" in ms)
check("the cap is applied without overflow (the offset is below the cap first)",
      "(ULONGLONG)offset.QuadPart < ios_section_view_cap()" in ms
      and "if (size > ios_section_view_cap() - (ULONGLONG)offset.QuadPart)" in ms
      and ms.index("offset.QuadPart < ios_section_view_cap()") < ms.index("size = ios_section_view_cap() - (ULONGLONG)offset.QuadPart"))
mv = ms.index("res = map_view( &view,")
gr = ms.index("ios_section_view_grow( unix_handle,")
mf = ms.index("map_file_into_view_ex( view, unix_handle")
check("the file is extended once the range is reserved and before the file is mapped", mv < gr < mf)
check("a refused extension deletes the view and keeps the status",
      "delete_view( view );\n        goto done;" in ms[gr:mf] and "(res = ios_section_view_grow(" in ms[gr - 40:gr + 60])
check("nothing runs when no grow was asked for", "if (ios_grow_to && (res = ios_section_view_grow(" in ms)
av = virt[virt.index("else  /* commit the pages */"):]
av = av[:av.index("SERVER_START_REQ")]
check("a commit in a grown view succeeds; every other file view keeps STATUS_ALREADY_COMMITTED",
      "ios_section_view_grown( view ) ? STATUS_SUCCESS : STATUS_ALREADY_COMMITTED" in av and av.count("STATUS_ALREADY_COMMITTED") == 1
      and "else if (view->protect & SEC_FILE)" in av)
check("both switches are read from the environment, grow by name and cap by name",
      'getenv( "MADEIRA_SECTION_VIEW_GROW" )' in virt and 'getenv( "MADEIRA_SECTION_VIEW_CAP_GB" )' in virt)
check("the code, the credit and the version marker are in the file",
      virt.count("madeira-bcd section-view-grow-v1") >= 5 and "Dragooon" in virt and "pull request #632" in virt)

# ---- 2. the wineserver ----------------------------------------------------------------------------------------------
mp = body(srv, "DECL_HANDLER(map_view)")
check("the section follows the file before the range check, for file-backed non-image sections only",
      mp.index("mapping->size = st.st_size;") < mp.index("req->start >= mapping->size ||")
      and "!(mapping->flags & SEC_IMAGE) && mapping->fd" in mp and "S_ISREG( st.st_mode )" in mp
      and "(mem_size_t)st.st_size > mapping->size" in mp and "madeira-bcd section-view-grow-v1" in mp)
check("it only looks at a request past the recorded size and never shrinks the section",
      "req->start + req->size > round_size( mapping->size, page_mask )" in mp[:mp.index("mapping->size = st.st_size;")]
      and mp.count("mapping->size =") == 1)

# ---- 3. the helper region on real files ------------------------------------------------------------------------------
begin = virt.index("/* sectiongrow-test:begin */")
end = virt.index("/* sectiongrow-test:end */")
region = virt[begin:end]
harness = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
typedef unsigned long SIZE_T;
typedef unsigned int ULONG;
typedef int LONG;
typedef unsigned long long ULONGLONG;
typedef uint64_t mem_size_t;
#define MEM_COMMIT  0x1000
#define MEM_RESERVE 0x2000
#define SEC_FILE 0x00800000
#define STATUS_SUCCESS 0
#define STATUS_INVALID_VIEW_SIZE 0xc000001f
struct file_view { void *base; SIZE_T size; unsigned int protect; };
static LONG InterlockedIncrement( volatile LONG *p ) { return __sync_add_and_fetch( p, 1 ); }
#define MAXLIVE 64
static struct file_view live[MAXLIVE];   /* the views the "process" has */
static struct file_view *find_view( const void *addr, size_t size )
{
    int i;
    for (i = 0; i < MAXLIVE; i++)
        if (live[i].base && (const char *)live[i].base <= (const char *)addr &&
            (const char *)addr + size <= (const char *)live[i].base + live[i].size) return &live[i];
    return NULL;
}
static void make_live( void *base, SIZE_T size )
{
    int i;
    for (i = 0; i < MAXLIVE; i++) if (!live[i].base) { live[i].base = base; live[i].size = size; live[i].protect = SEC_FILE; return; }
    abort();
}
static void kill_live( void *base ) { int i; for (i = 0; i < MAXLIVE; i++) if (live[i].base == base) memset( &live[i], 0, sizeof(live[i]) ); }
""" + region + r"""
#define R(name, val) printf( "RESULT %s=%lld\n", name, (long long)(val) )
static int make_file( const char *dir, const char *name, size_t size, int fill )
{
    char path[PATH_MAX];
    int fd;
    snprintf( path, sizeof(path), "%s/%s", dir, name );
    fd = open( path, O_RDWR | O_CREAT | O_TRUNC, 0644 );
    if (fd < 0) { perror( path ); exit( 2 ); }
    if (fill)
    {
        char buf[4096];
        size_t n;
        memset( buf, 0x5a, sizeof(buf) );
        for (n = 0; n < size; n += sizeof(buf)) if (write( fd, buf, sizeof(buf) ) != (ssize_t)sizeof(buf)) exit( 2 );
        if (ftruncate( fd, (off_t)size )) exit( 2 );
    }
    else if (ftruncate( fd, (off_t)size )) exit( 2 );
    return fd;
}
static long long fsize( int fd ) { struct stat st; fstat( fd, &st ); return st.st_size; }

int main( int argc, char **argv )
{
    const char *mode = argv[1], *dir = argc > 2 ? argv[2] : ".";
    if (!strcmp( mode, "switches" ))
    {
        R( "grow_ok_reserve", ios_section_view_grow_ok( MEM_RESERVE ) );
        R( "grow_ok_commit_only", ios_section_view_grow_ok( MEM_COMMIT ) );
        R( "grow_ok_both", ios_section_view_grow_ok( MEM_RESERVE | MEM_COMMIT ) );
        R( "cap_gb", ios_section_view_cap() >> 30 );
        return 0;
    }
    {
        int fd, p[2], i;
        unsigned int st;
        struct file_view v;
        /* a two-page file asked for 1 GB (LMDB's shape), the caller asked for 100 GB */
        fd = make_file( dir, "data.mdb", 8192, 0 );
        make_live( (void *)0x70000000, 1ull << 30 );
        st = ios_section_view_grow( fd, 8192, 1ull << 30, 100ull << 30, (void *)0x70000000, 1ull << 30 );
        R( "grow_status", st );
        R( "grow_size", fsize( fd ) );
        { struct stat s; fstat( fd, &s ); R( "grow_sparse", (long long)s.st_blocks * 512 < (8 << 20) ); }
        v.base = (void *)0x70000000; v.size = 1ull << 30; v.protect = SEC_FILE;
        R( "grown_yes", ios_section_view_grown( &v ) );
        v.size = 1ull << 20;
        R( "grown_other_size", ios_section_view_grown( &v ) );
        v.base = (void *)0x71000000; v.size = 1ull << 30;
        R( "grown_other_base", ios_section_view_grown( &v ) );
        close( fd );

        /* a file that is not this section's: another size */
        fd = make_file( dir, "other.bin", 8192, 0 );
        make_live( (void *)0x72000000, 1ull << 30 );
        R( "size_mismatch_status", ios_section_view_grow( fd, 4096, 1ull << 30, 1ull << 30, (void *)0x72000000, 1ull << 30 ) );
        R( "size_mismatch_file", fsize( fd ) );
        close( fd );
        /* a pipe */
        if (pipe( p )) return 2;
        R( "pipe_status", ios_section_view_grow( p[0], 0, 1ull << 30, 1ull << 30, (void *)0x73000000, 1ull << 30 ) );
        close( p[0] ); close( p[1] );
        /* a file with no name left */
        fd = make_file( dir, "gone.bin", 8192, 0 );
        { char path[PATH_MAX]; snprintf( path, sizeof(path), "%s/gone.bin", dir ); unlink( path ); }
        R( "unlinked_status", ios_section_view_grow( fd, 8192, 1ull << 30, 1ull << 30, (void *)0x74000000, 1ull << 30 ) );
        R( "unlinked_file", fsize( fd ) );
        close( fd );
        /* no descriptor */
        R( "nofd_status", ios_section_view_grow( -1, 8192, 1ull << 30, 1ull << 30, (void *)0x75000000, 1ull << 30 ) );
        /* grow only: a file longer than the end it is asked to reach is not cut */
        fd = make_file( dir, "long.bin", 1 << 20, 1 );
        make_live( (void *)0x76000000, 1 << 16 );
        R( "never_shrink_status", ios_section_view_grow( fd, 1 << 20, 1 << 16, 1 << 16, (void *)0x76000000, 1 << 16 ) );
        R( "never_shrink_file", fsize( fd ) );
        close( fd );
        /* a capped, nearly full view warns: 7.5 MB written, view of 8 MB, asked 1 GB */
        fd = make_file( dir, "full.mdb", 7864320, 1 );
        make_live( (void *)0x77000000, 8 << 20 );
        R( "warn_status", ios_section_view_grow( fd, 7864320, 8 << 20, 1ull << 30, (void *)0x77000000, 8 << 20 ) );
        close( fd );

        /* the table: the same base again takes its own slot; 16 live views fill it; the 17th is refused with the file untouched;
         * a view that is gone gives its slot back */
        memset( ios_grown_views, 0, sizeof(ios_grown_views) );
        memset( live, 0, sizeof(live) );
        for (i = 0; i < 16; i++)
        {
            char name[32];
            void *base = (void *)(0x100000000ull + (unsigned long long)i * 0x10000000ull);
            snprintf( name, sizeof(name), "t%d.mdb", i );
            fd = make_file( dir, name, 8192, 0 );
            make_live( base, 1 << 20 );
            if (ios_section_view_grow( fd, 8192, 1 << 20, 1 << 20, base, 1 << 20 )) R( "table_fill_failed", i );
            close( fd );
        }
        fd = make_file( dir, "again.mdb", 8192, 0 );
        R( "table_same_base", ios_section_view_grow( fd, 8192, 1 << 20, 1 << 20, (void *)0x100000000ull, 1 << 20 ) );
        close( fd );
        fd = make_file( dir, "t17.mdb", 8192, 0 );
        make_live( (void *)0x300000000ull, 1 << 20 );
        R( "table_17th_status", ios_section_view_grow( fd, 8192, 1 << 20, 1 << 20, (void *)0x300000000ull, 1 << 20 ) );
        R( "table_17th_file", fsize( fd ) );
        kill_live( (void *)0x120000000ull );   /* the view of slot 2 is unmapped */
        R( "table_reuse_status", ios_section_view_grow( fd, 8192, 1 << 20, 1 << 20, (void *)0x300000000ull, 1 << 20 ) );
        R( "table_reuse_file", fsize( fd ) );
        close( fd );
        v.base = (void *)0x300000000ull; v.size = 1 << 20; v.protect = SEC_FILE;
        R( "table_new_grown", ios_section_view_grown( &v ) );
        v.base = (void *)0x120000000ull;
        R( "table_old_grown", ios_section_view_grown( &v ) );
        v.base = (void *)0x130000000ull;
        R( "table_neighbour_grown", ios_section_view_grown( &v ) );
        /* a live entry whose address range is now another kind of view (no SEC_FILE) also gives its slot back */
        {
            int k;
            for (k = 0; k < MAXLIVE; k++) if (live[k].base == (void *)0x130000000ull) live[k].protect = 0;
        }
        fd = make_file( dir, "t18.mdb", 8192, 0 );
        make_live( (void *)0x310000000ull, 1 << 20 );
        R( "table_notfile_reuse_status", ios_section_view_grow( fd, 8192, 1 << 20, 1 << 20, (void *)0x310000000ull, 1 << 20 ) );
        close( fd );
        v.base = (void *)0x130000000ull; v.size = 1 << 20;
        R( "table_notfile_old_grown", ios_section_view_grown( &v ) );
    }
    return 0;
}
"""
env0 = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
cc = shutil.which("cc") or "cc"
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    (t / "sg.c").write_text(harness)
    b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-function", "-fsanitize=address,undefined",
                        "-fno-sanitize-recover=undefined", "-o", str(t / "sg"), str(t / "sg.c")], capture_output=True, text=True)
    check("the helper region compiles on the host without a warning", b.returncode == 0 and not b.stderr.strip(), b.stderr)
    if b.returncode == 0:
        asan = dict(env0, ASAN_OPTIONS="symbolize=0:detect_leaks=0")   # the symbolizer opens a password dialog on this Mac

        def run(mode, extra=None, d=None):
            e = dict(asan)
            e.update(extra or {})
            r = subprocess.run([str(t / "sg"), mode, str(d or t)], capture_output=True, text=True, env=e)
            res = dict(re.findall(r"^RESULT (\w+)=(-?\d+)$", r.stdout, re.M))
            return r, {k: int(v) for k, v in res.items()}

        # the switches
        for label, extra, want in (
                ("unset", {}, (1, 0, 1, 16)),
                ("GROW=1", {"MADEIRA_SECTION_VIEW_GROW": "1"}, (1, 0, 1, 16)),
                ("GROW= (empty)", {"MADEIRA_SECTION_VIEW_GROW": ""}, (1, 0, 1, 16)),
                ("GROW=0 turns it off", {"MADEIRA_SECTION_VIEW_GROW": "0"}, (0, 0, 0, 16)),
                ("CAP_GB=4", {"MADEIRA_SECTION_VIEW_CAP_GB": "4"}, (1, 0, 1, 4)),
                ("CAP_GB=1 and 48 are the limits", {"MADEIRA_SECTION_VIEW_CAP_GB": "48"}, (1, 0, 1, 48)),
                ("CAP_GB=49 is out of range", {"MADEIRA_SECTION_VIEW_CAP_GB": "49"}, (1, 0, 1, 16)),
                ("CAP_GB=0 is out of range", {"MADEIRA_SECTION_VIEW_CAP_GB": "0"}, (1, 0, 1, 16)),
                ("CAP_GB=abc is 16", {"MADEIRA_SECTION_VIEW_CAP_GB": "abc"}, (1, 0, 1, 16))):
            r, res = run("switches", extra)
            got = (res.get("grow_ok_reserve"), res.get("grow_ok_commit_only"), res.get("grow_ok_both"), res.get("cap_gb"))
            check("switches, " + label + ": MEM_RESERVE only, cap in GB", r.returncode == 0 and got == want, (got, want, r.stderr))

        # real files (a folder under the temp dir, so "the end of the path" is checkable)
        d = t / "Shaders"
        d.mkdir()
        r, res = run("files", d=d)
        err = r.stderr
        check("the harness runs clean under ASAN/UBSAN", r.returncode == 0, err)
        check("a two-page file asked for 1 GB (100 GB at the caller) is extended to the end of the view and stays sparse",
              res.get("grow_status") == 0 and res.get("grow_size") == 1 << 30 and res.get("grow_sparse") == 1, res)
        check("the grown view is remembered by base and size only",
              res.get("grown_yes") == 1 and res.get("grown_other_size") == 0 and res.get("grown_other_base") == 0, res)
        check("a file whose size is not the section's is refused and not touched",
              res.get("size_mismatch_status") == 0xc000001f and res.get("size_mismatch_file") == 8192, res)
        check("a pipe, an unlinked file and no descriptor are refused (the old status)",
              res.get("pipe_status") == 0xc000001f and res.get("unlinked_status") == 0xc000001f
              and res.get("unlinked_file") == 8192 and res.get("nofd_status") == 0xc000001f, res)
        check("the file is never shortened", res.get("never_shrink_status") == 0 and res.get("never_shrink_file") == 1 << 20, res)
        check("the table takes the same base again, refuses a 17th live view without touching its file",
              res.get("table_fill_failed") is None and res.get("table_same_base") == 0
              and res.get("table_17th_status") == 0xc000001f and res.get("table_17th_file") == 8192, res)
        check("a view that is gone (or no longer a file view) gives its slot to the next one; the old one is not remembered any more",
              res.get("table_reuse_status") == 0 and res.get("table_reuse_file") == 1 << 20 and res.get("table_new_grown") == 1
              and res.get("table_old_grown") == 0 and res.get("table_neighbour_grown") == 1
              and res.get("table_notfile_reuse_status") == 0 and res.get("table_notfile_old_grown") == 0, res)
        lines = [l for l in err.split("\n") if l.startswith("[section-grow]")]
        main = [l for l in lines if "WARNING" not in l]
        check("the first 16 cases are logged, later ones are not", len(main) == 16, len(main))
        check("a refusal says why", any("REFUSED" in l and "the file's size is not the section's" in l for l in main), main[:3])
        check("the 17th live view is refused for its reason", res.get("table_17th_status") == 0xc000001f)
        if sys.platform == "darwin":   # F_GETPATH is Darwin's
            check("the first line says the file was extended, that the view was CAPPED (100 GB asked) and what it covers",
                  main and "file extended to cover the view" in main[0] and "(asked 0x1900000000, CAPPED)" in main[0]
                  and "over Shaders/data.mdb (0x2000 bytes, " in main[0], main[:1])
            check("the log names the end of the path, not the container's path",
                  str(t) not in err and str(d) not in err and not any("/private" in l or "/var/" in l for l in lines),
                  [l for l in lines if "/var/" in l or "/private" in l][:1])
            warn = [l for l in lines if "WARNING" in l]
            check("a capped view that is three quarters full (7 MB of 8 MB) warns, by the end of its path",
                  len(warn) == 1 and "Shaders/full.mdb has 7 MB written of a 8 MB view" in warn[0], warn)
        else:
            print("note: not Darwin: the path and warning lines are checked on the Mac only")

# ---- 4. catalog, credits, workflow ----------------------------------------------------------------------------------
gen = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog overlay names both keys with their defaults (grow on, cap 16)",
      re.search(r'"env\.MADEIRA_SECTION_VIEW_GROW": \{[^}]*"kind": "bool", "default": "1"', gen, re.S) is not None
      and re.search(r'"env\.MADEIRA_SECTION_VIEW_CAP_GB": \{[^}]*"kind": "int", "default": "16"', gen, re.S) is not None)
notices = [root / "THIRD-PARTY-NOTICES.md", root / "app/Madeira/legal/THIRD-PARTY-NOTICES.md", root / "app/Madeira/licenses/THIRD-PARTY-NOTICES.txt"]
check("Dragooon and pull request #632 are named in all three notices files",
      all("Dragooon" in n.read_text() and "#632" in n.read_text() for n in notices), [n.name for n in notices])
check("the bundled long notice is the repository's, word for word", notices[0].read_text() == notices[1].read_text())
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the check is a workflow step", "python3 tests/host/check-section-view-grow.py" in wf)

# ---- 5. the real files, when a wine checkout and the iOS SDK are there --------------------------------------------------
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
xcrun = shutil.which("xcrun")
bld = next((p for p in (wine / "build-macos", wine / "build-arm64ec") if (p / "include/config.h").exists()), None)
if bld and xcrun and (wine / "dlls/ntdll/unix").is_dir():
    sdk = subprocess.run([xcrun, "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
    shims = root / "build/ntdll-unix/shims"
    cmd = [xcrun, "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2",
           "-fsyntax-only", "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
           "-include", str(bld / "include/config.h"), "-include", str(shims / "wine_ios_exit.h"), "-I" + str(shims),
           "-I" + str(bld / "dlls/ntdll"), "-I" + str(wine / "dlls/ntdll"), "-I" + str(wine / "dlls/ntdll/unix"),
           "-I" + str(bld / "include"), "-I" + str(wine / "include"), "-D__WINESRC__", "-DLTC_NO_PROTOTYPES", "-DLTC_SOURCE",
           "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", "-DWINE_UNIX_LIB", "-DWINE_IOS=1",
           "-Dget_thread_context=ntdll_get_thread_context", "-Dset_thread_context=ntdll_set_thread_context",
           str(root / "build/ntdll-unix/virtual_ios.c")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("virtual_ios.c compiles for iOS arm64 with build.sh's flags", r.returncode == 0 and "error:" not in r.stderr, r.stderr)
    new = [l for l in r.stderr.split("\n") if "warning:" in l and re.search(r"ios_(section_view|grown|grow_)|ios_grow", l)]
    check("and none of its warnings is in the new code", not new, new[:3])
    cmd = [xcrun, "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-fsyntax-only",
           "-include", str(bld / "include/config.h"), "-I" + str(wine / "server"), "-I" + str(bld / "server"),
           "-I" + str(bld / "include"), "-I" + str(wine / "include"), "-D__WINESRC__", "-D_GNU_SOURCE", "-DWINE_IOS=1",
           "-Wno-implicit-function-declaration", "-Wno-int-conversion", str(root / "build/wineserver/mapping_ios.c")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("mapping_ios.c compiles for iOS arm64 against Wine's server headers", r.returncode == 0 and "error:" not in r.stderr, r.stderr)
else:
    print("note: no wine checkout or iOS SDK here; virtual_ios.c and mapping_ios.c are compiled by the build only")

print("PASS: section-view-grow" if ok else "FAILED: section-view-grow")
sys.exit(0 if ok else 1)
