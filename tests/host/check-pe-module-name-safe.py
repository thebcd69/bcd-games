#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The emulator export resolver names modules fault-safely (round 626, agent G2 S13; Tomb Raider Game of the Year GR49ZZ, build 623).

ios_resolve_fex_exports (build/ntdll-unix/virtual_ios.c) named every ios_jit_mappings[] entry with ios_pe_module_name's plain loads.
An entry whose image the game had unmapped (its refused netapi32/Kerberos loads, "[image-reload] unmapped image") keeps its pe_base on
purpose; 1.3 s later the game's 64-bit helper started, the resolver read that base and took a BUS fault no guest handler could take.
Now the loop skips entries marked unmapped and names the others with ios_pe_module_name_safe (every read through mach_vm_read_overwrite
into a buffer). The other callers of ios_pe_module_name are unchanged.

Checks: the loop's first statement skips a null, empty or unmapped entry, then names it safely; the safe reader and the original,
cut from virtual_ios.c and compiled on the host (macOS), give the same name for a readable fake PE (and "?" for a bad header, a PE32
header, a name without a NUL inside the image), and the safe one answers "?" for an unmapped page and a name on an unmapped next page
without a signal; ios_pe_module_name itself is untouched; auto-tune's fixedIn rule and its fixture; CI.
"""
from pathlib import Path
import json, os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
SRC = (root / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def cut(sig):
    i = SRC.index(sig)
    while SRC.index(";", i) < SRC.index("{", i):
        i = SRC.index(sig, i + 1)
    j = SRC.index("{", i)
    depth = 0
    for k in range(j, len(SRC)):
        if SRC[k] == "{":
            depth += 1
        elif SRC[k] == "}":
            depth -= 1
            if depth == 0:
                return SRC[i:k + 1]
    raise SystemExit("unbalanced " + sig)


res = cut("void ios_resolve_fex_exports( void )")
loop = res[res.index("for (i = 0; i < ios_jit_mapping_count; i++)"):]
stmts = [l.strip() for l in loop.splitlines()[2:12] if l.strip() and not l.strip().startswith(("/*", "*"))]
check("the resolver skips a null, empty or unmapped entry before any read, then names the others fault-safely",
      "if (!ios_jit_mappings[i].pe_base || !ios_jit_mappings[i].size || ios_jit_mappings[i].unmapped) continue;" in stmts
      and stmts.index("if (!ios_jit_mappings[i].pe_base || !ios_jit_mappings[i].size || ios_jit_mappings[i].unmapped) continue;")
      < stmts.index("nm = ios_pe_module_name_safe( ios_jit_mappings[i].pe_base, ios_jit_mappings[i].size, nb, sizeof(nb) );")
      and "ios_pe_module_name(" not in loop[:loop.index("rip_cb = ios_pe_find_export(")], stmts)
region = SRC[SRC.index("/* safe-module-name-test:begin"):SRC.index("/* safe-module-name-test:end */")]
orig = cut("const char *ios_pe_module_name( const void *image_base, size_t image_size )")
check("ios_pe_module_name itself is unchanged (its other callers keep their behaviour)",
      "const unsigned char *b = image_base;" in orig and "if (b[0] != 'M' || b[1] != 'Z') return \"?\";" in orig)
sread = cut("static int ios_safe_read( uintptr_t addr, void *out, size_t len )")
HARNESS = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
''' + sread + "\n" + region + "\n" + orig + r'''
#define PG 0x4000
static unsigned char *page( int n ) { return mmap( NULL, (size_t)n * PG, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0 ); }
static void make_pe( unsigned char *b, uint16_t magic, uint32_t name_rva, const char *name )
{
    uint32_t lfanew = 0x80, exp_rva = 0x200;
    memset( b, 0, 0x400 );
    b[0] = 'M'; b[1] = 'Z'; memcpy( b + 0x3c, &lfanew, 4 );
    b[0x80] = 'P'; b[0x81] = 'E'; memcpy( b + 0x80 + 0x18, &magic, 2 ); memcpy( b + 0x80 + 0x88, &exp_rva, 4 );
    memcpy( b + exp_rva + 0x0c, &name_rva, 4 );
    if (name) memcpy( b + name_rva, name, strlen( name ) + 1 );
}
int main( void )
{
    char buf[72];
    unsigned char *a = page( 1 ), *gone = page( 1 ), *two = page( 2 );
    int fails = 0;
#define T(c) do { if (!(c)) { printf( "FAIL line %d: %s\n", __LINE__, #c ); fails++; } } while (0)
    make_pe( a, 0x20b, 0x300, "xtajit64.dll" );
    T( !strcmp( ios_pe_module_name_safe( a, PG, buf, sizeof(buf) ), "xtajit64.dll" ) );
    T( !strcmp( ios_pe_module_name( a, PG ), "xtajit64.dll" ) );                               /* the same name as the original */
    make_pe( a, 0x10b, 0x300, "x86.dll" );                                                     /* PE32: "?" in both */
    T( !strcmp( ios_pe_module_name_safe( a, PG, buf, sizeof(buf) ), "?" ) && !strcmp( ios_pe_module_name( a, PG ), "?" ) );
    a[0] = 'X';                                                                                /* not MZ */
    T( !strcmp( ios_pe_module_name_safe( a, PG, buf, sizeof(buf) ), "?" ) && !strcmp( ios_pe_module_name( a, PG ), "?" ) );
    make_pe( a, 0x20b, PG - 8, NULL ); memset( a + PG - 8, 'A', 8 );                           /* no NUL inside the image */
    T( !strcmp( ios_pe_module_name_safe( a, PG, buf, sizeof(buf) ), "?" ) && !strcmp( ios_pe_module_name( a, PG ), "?" ) );
    T( !strcmp( ios_pe_module_name_safe( a, 0x80, buf, sizeof(buf) ), "?" ) );                /* too small */
    T( !strcmp( ios_pe_module_name_safe( NULL, PG, buf, sizeof(buf) ), "?" ) );
    munmap( gone, PG );                                                                         /* the GR49ZZ case: the image is gone */
    T( !strcmp( ios_pe_module_name_safe( gone, PG, buf, sizeof(buf) ), "?" ) );
    make_pe( two, 0x20b, PG + 0x10, NULL );                                                     /* the name on the next page ... */
    munmap( two + PG, PG );                                                                     /* ... which is gone */
    T( !strcmp( ios_pe_module_name_safe( two, 2 * PG, buf, sizeof(buf) ), "?" ) );
    puts( fails ? "FAILED" : "ok: the same names, \"?\" without a signal where the original would fault" );
    return fails != 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if cc and sys.platform == "darwin":
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the safe reader, the original and ios_safe_read compile on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print("     " + run.stdout.strip().replace("\n", "\n     "))
            check("same names as the original; \"?\" for an unmapped image or name page, no signal", run.returncode == 0, run.stdout + run.stderr)
else:
    print("note: not on macOS or no C compiler; the reader was not run")

rules = (root / "build/tools/autotune_rules.py").read_text()
r = rules[rules.index('"id": "fex-exports-stale-image-read"'):] if '"id": "fex-exports-stale-image-read"' in rules else ""
r = r[:r.index("\n    },")] if r else ""
check("auto-tune knows the crash of the builds before it (fixedIn 626)", '"fixedIn": 626' in r and "[image-reload] unmapped image" in r, r[:300])
exp = root / "tests/fixtures/autotune/tomb-raider-goty-stale-image-bus-2026-10-30.expect"
check("the fixture is there and expects the known verdict", exp.is_file() and "fex-exports-stale-image-read" in json.loads(exp.read_text()).get("matched", []))
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-pe-module-name-safe.py" in wf)
print("PASS: fault-safe module names" if ok else "FAILED: fault-safe module names")
sys.exit(0 if ok else 1)
