#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The exit and stack-overflow reports name modules fault-safely (round 626, agent G2 S6; NieR:Automata 8FWJCP, build 623).

NtTerminateProcess's [term-stack] ml660 scan (build/ntdll-unix/process_ios.c) and the [stack-ovf] report (signal_arm64_ios.c) named the
module of every code-like stack word with plain loads at the module record's base. A record can outlive the readable mapping of its
image: in 8FWJCP the program ended itself (exit code 0x35, Steam not found), the scan read an unmapped uxtheme.dll record, BCD's own
code faulted twice and the clean exit became c0000005 (the same in eight Rockstar Launcher logs of build 572). Both reports now read
through the kernel (ios_diag_module_name, ios_ovf_module_name) and say "(unmapped record)" where the old reads would have faulted.

Checks: neither report reads the module record with a plain load any more and both use the helpers; ios_diag_module_name with
ios_unw_read, cut from process_ios.c and compiled on the host (macOS), gives the old labels for readable images (the export name,
"(exe)" without one, "?" for no PE) and "(unmapped record)" for an unmapped page without a signal, and keeps a name on its own page
when the next page is gone; the [stack-ovf] twin is the same function under another name; auto-tune's fixedIn rule and fixture; CI.
"""
from pathlib import Path
import json, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
P = (root / "build/ntdll-unix/process_ios.c").read_text()
S = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def cut(src, sig):
    i = src.index(sig)
    while src.index(";", i) < src.index("{", i):
        i = src.index(sig, i + 1)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise SystemExit("unbalanced " + sig)


ts = P[P.index('dprintf(2, "[term-stack] ml660 sp+%03x: %s %llx  %.32s+0x%llx\\n",') - 400:P.index('dprintf(2, "[term-stack] ml660 sp+%03x: %s %llx  %.32s+0x%llx\\n",')]
check("[term-stack] names the module through ios_diag_module_name (no plain load of the record)",
      "const char *mname = ios_diag_module_name( mbase, msz, nb, sizeof(nb) );" in ts and "*(const unsigned int *)(mb + 0x3c)" not in P)
ov = S[S.index('dprintf( 2, "[stack-ovf]   w%d +%03x: %llx  %.32s+0x%llx\\n",') - 900:S.index('dprintf( 2, "[stack-ovf]   w%d +%03x: %llx  %.32s+0x%llx\\n",')]
check("[stack-ovf] names the module through ios_ovf_module_name (no plain load of the record)",
      "mname = ios_ovf_module_name( mbase, msz, nb, sizeof(nb) );" in ov and "*(const unsigned int *)(mb + 0x3c)" not in S)
diag = cut(P, "static const char *ios_diag_module_name( uint64_t mbase, unsigned long long msz, char *out, unsigned int cap )")
ovf = cut(S, "static const char *ios_ovf_module_name( uint64_t mbase, unsigned long long msz, char *out, unsigned int cap )")
check("the [stack-ovf] helper is the same function", diag.replace("ios_diag_module_name", "X").replace("ios_unw_rd32( ", "R4( ").replace("ios_unw_read( ", "RD( ")
      .replace(", &e_lf )", ", &e_lf, 4 )").replace(", &exp_rva )", ", &exp_rva, 4 )").replace(", &name_rva )", ", &name_rva, 4 )").replace("R4( ", "RD( ")
      .split("{", 1)[1].replace(" ", "").replace("/*thename'sownpageonly*/", "")
      == ovf.replace("ios_ovf_module_name", "X").replace("ios_ovf_read( ", "RD( ").split("{", 1)[1].replace(" ", ""))
unw = cut(P, "static int ios_unw_read( uint64_t addr, void *buf, unsigned int len )")
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
''' + unw + "\nstatic int ios_unw_rd32( uint64_t a, unsigned int *v ) { return ios_unw_read( a, v, 4 ); }\n" + diag + r'''
#define PG 0x4000
/* 16 KB-aligned blocks, as on iOS (this Mac may have 4 KB pages) */
static unsigned char *pages( int n ) { uintptr_t m = (uintptr_t)mmap( NULL, (size_t)(n + 1) * PG, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0 ); return (unsigned char *)((m + PG - 1) & ~(uintptr_t)(PG - 1)); }
static void pe( unsigned char *b, int with_export, unsigned int name_rva, const char *name )
{
    unsigned int lf = 0x80, exp = 0x200;
    memset( b, 0, 0x400 ); b[0] = 'M'; b[1] = 'Z'; memcpy( b + 0x3c, &lf, 4 );
    if (with_export) { memcpy( b + lf + 0x88, &exp, 4 ); memcpy( b + exp + 0x0c, &name_rva, 4 ); }
    if (name) memcpy( b + name_rva, name, strlen( name ) + 1 );
}
/* the reports' old expression, for the labels on readable images */
static const char *old_name( const unsigned char *mb, unsigned long long msz )
{
    unsigned int e_lf, exp_rva, name_rva;
    if (mb[0] == 'M' && mb[1] == 'Z' && (e_lf = *(const unsigned int *)(mb + 0x3c)) < 0x1000 &&
        (exp_rva = *(const unsigned int *)(mb + e_lf + 0x88)) && exp_rva < msz &&
        (name_rva = *(const unsigned int *)(mb + exp_rva + 0x0c)) && name_rva < msz)
        return (const char *)(mb + name_rva);
    else if (mb[0] == 'M' && mb[1] == 'Z') return "(exe)";
    return "?";
}
int main( void )
{
    char nb[40];
    int fails = 0;
    unsigned char *a = pages( 1 ), *gone = pages( 1 ), *two = pages( 2 );
#define T(c) do { if (!(c)) { printf( "FAIL line %d: %s\n", __LINE__, #c ); fails++; } } while (0)
    pe( a, 1, 0x300, "uxtheme.dll" );
    T( !strcmp( ios_diag_module_name( (uintptr_t)a, PG, nb, sizeof(nb) ), "uxtheme.dll" ) && !strcmp( old_name( a, PG ), "uxtheme.dll" ) );
    pe( a, 0, 0, NULL );                                                         /* an exe without an export directory */
    T( !strcmp( ios_diag_module_name( (uintptr_t)a, PG, nb, sizeof(nb) ), "(exe)" ) && !strcmp( old_name( a, PG ), "(exe)" ) );
    a[0] = 0;                                                                    /* not a PE */
    T( !strcmp( ios_diag_module_name( (uintptr_t)a, PG, nb, sizeof(nb) ), "?" ) && !strcmp( old_name( a, PG ), "?" ) );
    munmap( gone, PG );                                                          /* 8FWJCP: the record's image is gone */
    T( !strcmp( ios_diag_module_name( (uintptr_t)gone, PG, nb, sizeof(nb) ), "(unmapped record)" ) );
    pe( two, 1, PG - 12, "short.dll" );                                          /* a name near the end of its page ... */
    munmap( two + PG, PG );                                                      /* ... and the next page gone */
    T( !strcmp( ios_diag_module_name( (uintptr_t)two, 2 * PG, nb, sizeof(nb) ), "short.dll" ) );
    puts( fails ? "FAILED" : "ok: the old labels where readable, \"(unmapped record)\" without a signal where not" );
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
        check("the helper and ios_unw_read compile on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print("     " + run.stdout.strip().replace("\n", "\n     "))
            check("the old labels where readable; \"(unmapped record)\" without a signal", run.returncode == 0, run.stdout + run.stderr)
else:
    print("note: not on macOS or no C compiler; the helper was not run")

rules = (root / "build/tools/autotune_rules.py").read_text()
r = rules[rules.index('"id": "term-stack-unmapped-module-record"'):] if '"id": "term-stack-unmapped-module-record"' in rules else ""
r = r[:r.index("\n    },")] if r else ""
check("auto-tune knows the crash of the builds before it (fixedIn 626)", '"fixedIn": 626' in r and "[term-stack] ml660" in r, r[:300])
exp = root / "tests/fixtures/autotune/nier-automata-term-stack-unmapped-2026-10-31.expect"
check("the fixture is there and expects the known verdict", exp.is_file() and "term-stack-unmapped-module-record" in json.loads(exp.read_text()).get("matched", []))
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-diag-module-name.py" in wf)
print("PASS: fault-safe report names" if ok else "FAILED: fault-safe report names")
sys.exit(0 if ok else 1)
