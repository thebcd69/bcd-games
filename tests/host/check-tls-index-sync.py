#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Russell Barton
"""tls-index-sync: a module's TLS slot reaches the pool copy it runs from (virtual_ios.c, ios_tls_index_off / ios_tls_index_sync).

The loader stores a module's TLS slot in the image it sees (alloc_tls_slot: *AddressOfIndex = slot); an ARM64EC module runs from
its pool copy and read the file's 0 there. The protect-time sync copies those 4 bytes from the image into the copy it syncs.

Checks:
  1. the two production functions cut from virtual_ios.c, compiled with -Wall -Wextra -Werror and ASan/UBSan against a stub
     section test and a synthetic PE32+ image (the image and its copy as two buffers): a differing index is copied with one
     log line and nothing else in the copy changes; an equal index, a PE32 image, no TLS directory, a directory too small, an
     AddressOfIndex outside the image or in an executable section, and headers cut at every length leave the copy as it was;
  2. the protect-time sync calls it once for the image it syncs, after the anon-alias skip and before the ntdll data-range
     branch that may end the sync; CI runs this check.
NOT covered: the loader order (alloc_tls_slot, then the import protects, then DllMain), which only a device shows.
"""
from pathlib import Path
import os, shutil, struct, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/virtual_ios.c").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
ok = True


def check(what, cond, detail=""):
    global ok
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[:2000]) if detail and not cond else ""))
    ok = ok and bool(cond)


def cut(text, start, end="\n}\n"):
    at = text.index(start)
    return text[at:text.index(end, at) + len(end)]


# ---------------------------------------------------------------- 1. the functions on a synthetic image
off_fn = cut(src, "static int ios_tls_index_off( const unsigned char *img, size_t img_size, uintptr_t img_va, size_t *index_off )")
sync_fn = cut(src, "static void ios_tls_index_sync( uintptr_t pe_start, size_t img_size, char *rw_image )")

IMG = 0x3000
LFANEW = 0x80
TLS_RVA = 0x1000
INDEX_RVA = 0x2010
EXEC_LO, EXEC_HI = 0x400, 0x1000   # the stub's "executable section"


def image(magic=0x20b, nrva=16, tls_rva=TLS_RVA, tls_size=40, index_va_rva=INDEX_RVA, base=0):
    b = bytearray(IMG)
    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3c, LFANEW)
    b[LFANEW:LFANEW + 4] = b"PE\0\0"
    opt = LFANEW + 24
    struct.pack_into("<H", b, opt, magic)
    struct.pack_into("<I", b, opt + 108, nrva)
    struct.pack_into("<II", b, opt + 112 + 9 * 8, tls_rva, tls_size)
    struct.pack_into("<Q", b, TLS_RVA + 16, (base + index_va_rva) & (2 ** 64 - 1))   # AddressOfIndex, a VA (the harness adds the base)
    return b


harness = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define dprintf(fd, ...) (lines++, fprintf(stderr, __VA_ARGS__))
static int lines;
static int ios_img_off_is_exec( const void *img, size_t img_size, size_t off )
{ (void)img; (void)img_size; return off >= %(lo)d && off < %(hi)d; }
%(off_fn)s
%(sync_fn)s
static unsigned char *slurp( const char *p, size_t *n )
{
    FILE *f = fopen( p, "rb" ); unsigned char *b; long l;
    if (!f) exit( 2 );
    fseek( f, 0, SEEK_END ); l = ftell( f ); fseek( f, 0, SEEK_SET );
    b = malloc( l ? (size_t)l : 1 ); if (fread( b, 1, (size_t)l, f ) != (size_t)l) exit( 2 ); fclose( f ); *n = (size_t)l; return b;
}
int main( int argc, char **argv )
{
    /* argv[1]: the image; argv[2]: the copy; argv[3]: "sync" or "off" (with argv[4] = the length to parse);
       the image's own address is its VA (AddressOfIndex in the files is an RVA; main rebases it). */
    size_t n, m, off = 0, len;
    unsigned char *img = slurp( argv[1], &n ), *copy = slurp( argv[2], &m ), *before;
    uint64_t va;
    if (argc < 4 || n != m) return 2;
    memcpy( &va, img + %(tls)d + 16, 8 ); va += (uintptr_t)img; memcpy( img + %(tls)d + 16, &va, 8 );
    memcpy( &va, copy + %(tls)d + 16, 8 ); va += (uintptr_t)img; memcpy( copy + %(tls)d + 16, &va, 8 );
    if (!strcmp( argv[3], "off" ))
    {
        unsigned char *cut;
        len = (size_t)strtoul( argv[4], NULL, 0 );
        cut = malloc( len ? len : 1 ); memcpy( cut, img, len < n ? len : n );
        if (len >= %(tls)d + 24) { va = (uintptr_t)cut + %(index)d; memcpy( cut + %(tls)d + 16, &va, 8 ); }   /* a VA in the cut copy */
        {
            int got = ios_tls_index_off( cut, len, (uintptr_t)cut, &off );
            printf( "%%d %%zx\n", got, off );
        }
        free( cut ); free( img ); free( copy );
        return 0;
    }
    before = malloc( m ); memcpy( before, copy, m );
    ios_tls_index_sync( (uintptr_t)img, n, (char *)copy );
    {
        size_t i, diff = 0, first = (size_t)-1;
        for (i = 0; i < m; i++) if (copy[i] != before[i]) { diff++; if (first == (size_t)-1) first = i; }
        printf( "lines=%%d diff=%%zu first=%%zx idx=%%u\n", lines, diff, first == (size_t)-1 ? 0 : first,
                *(unsigned *)(copy + %(index)d) );
    }
    free( before ); free( img ); free( copy );
    return 0;
}
""" % {"lo": EXEC_LO, "hi": EXEC_HI, "off_fn": off_fn, "sync_fn": sync_fn, "tls": TLS_RVA, "index": INDEX_RVA}

if not cc:
    check("a C compiler for the host test", False, "no cc/clang/gcc")
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "h.c").write_text(harness)
        exe = t / "h"
        flags = ["-std=gnu11", "-Wall", "-Wextra", "-Werror", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
        r = subprocess.run([cc, *flags, str(t / "h.c"), "-o", str(exe)], capture_output=True, text=True)
        if r.returncode:
            r = subprocess.run([cc, *[f for f in flags if "sanitize" not in f], str(t / "h.c"), "-o", str(exe)],
                               capture_output=True, text=True)
        check("the two functions compile with -Wall -Wextra -Werror", r.returncode == 0, r.stderr)
        if r.returncode == 0:
            def run(img, copy, *mode):
                (t / "i").write_bytes(bytes(img)); (t / "c").write_bytes(bytes(copy))
                p = subprocess.run([str(exe), str(t / "i"), str(t / "c"), *(mode or ("sync",))], capture_output=True, text=True)
                return p.returncode, p.stdout.strip(), p.stderr

            live = image(); struct.pack_into("<I", live, INDEX_RVA, 12)
            copy = image()                                   # the file's 0
            rc, out, err = run(live, copy)
            check("a differing index: the 4 bytes are copied, one log line, nothing else changes",
                  rc == 0 and out == "lines=1 diff=1 first=%x idx=12" % INDEX_RVA and "[tls-index-sync]" in err
                  and "= 12 in its pool copy (it held 0)" in err, (rc, out, err))

            rc, out, err = run(live, live)
            check("an equal index: nothing is written, no line", rc == 0 and out == "lines=0 diff=0 first=0 idx=12", (rc, out, err))

            for what, kw in (("a PE32 image", dict(magic=0x10b)),
                             ("no TLS directory entry (NumberOfRvaAndSizes 9)", dict(nrva=9)),
                             ("no TLS directory (rva 0)", dict(tls_rva=0)),
                             ("a TLS directory smaller than IMAGE_TLS_DIRECTORY64", dict(tls_size=39)),
                             ("a TLS directory past the image", dict(tls_rva=IMG - 39)),
                             ("AddressOfIndex past the image", dict(index_va_rva=IMG - 3)),
                             ("AddressOfIndex below the image", dict(index_va_rva=-0x10)),
                             ("AddressOfIndex in an executable section", dict(index_va_rva=EXEC_LO + 0x10))):
                a, b = image(**kw), image(**kw)
                if kw.get("tls_rva", TLS_RVA) == TLS_RVA:
                    at = kw.get("index_va_rva", INDEX_RVA)
                    if 0 <= at <= IMG - 4:
                        struct.pack_into("<I", a, at, 12)
                rc, out, err = run(a, b)
                check(what + ": the copy stays as it was", rc == 0 and out.startswith("lines=0 diff=0 "), (rc, out, err))

            bad = []
            for n in list(range(0, LFANEW + 24 + 112 + 80 + 2)) + [TLS_RVA + 23, TLS_RVA + 39, INDEX_RVA + 3]:
                rc, out, err = run(live, copy, "off", str(n))
                if rc != 0 or not out.startswith("0 "):
                    bad.append((n, rc, out, err[-300:]))
            rc, out, err = run(live, copy, "off", str(INDEX_RVA + 4))
            check("headers cut at every length up to the end of AddressOfIndex are refused without a read past them (ASan)",
                  not bad and out == "1 %x" % INDEX_RVA, (bad[:3], out))

# ---------------------------------------------------------------- 2. the wiring
call = "ios_tls_index_sync( pe_start, pe_end - pe_start, (char *)ios_jit_rw_base_global + pool_offset );"
check("the protect-time sync calls it once, for the image it syncs", src.count(call) == 1)
at = src.find(call)
nt = src.rfind("NTSTATUS WINAPI NtProtectVirtualMemory(", 0, at)
alias = src.rfind("ios_jit_anon_alias_overlaps( base, size, &ov_b, &ov_e )", 0, at)
rng = src.find("if (ios_ntdll_data_sync_range_enabled())", at)
check("inside NtProtectVirtualMemory, after the anon-alias skip and before the ntdll data-range branch",
      nt > 0 and alias > nt and 0 < rng - at < 400 and src.find("\n}\n", nt) > at)
check("CI runs this check", "python3 tests/host/check-tls-index-sync.py" in wf)

sys.exit(0 if ok else 1)
