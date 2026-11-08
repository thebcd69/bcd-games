#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_ALIAS_HONOR_PROT = 1 (default off): the [wr-strip] heal leaves an executable alias alone (step 1 of upstream Madeira's
alias-store rework, 0db1b82, for CoreCLR/Mono; Stardew Valley).

An executable alias (the JIT pool's RX view, the low pool, or the RX view of an anonymous RWX allocation) is read-only on the host by
design. The SIGBUS handler's [wr-strip] heal took a guest store to it for iOS stripping the write bit, and mprotect()ed the page RWX:
FEX never saw the write, never dropped its translation, and a .NET runtime that patches code it has run kept running the old code
(Stardew Valley: 9 of 9 recent sessions end after 2-38 s with play=0s; the heal is in 118 archive logs). With the key on such a fault is
not healed. Off, nothing changes: the decision below is the old one for every input.

The production helpers and the decision are cut from build/ntdll-unix/signal_arm64_ios.c and run on the host:
  - the key is on only for exactly "1";
  - an address is an executable alias inside the pool's RX view (and not at its end), in the low pool, or when the anonymous alias
    lookup knows it; any other address is not;
  - for every combination of (write fault, Wine's expected protection, host protection), key off gives exactly the old `stripped`; key on
    gives the old `stripped` for an address that is not an alias and 0 for one that is; the log text names the new case.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


a = src.index("static int ios_alias_honor_prot_on(void)")
helpers = src[a:src.index("#endif", a)]
dec_start = src.index("int aliased = ios_alias_honor_prot_on()")
decision = src[dec_start:src.index("++wr_seen;", dec_start)]
check("the decision lines are the old condition with !aliased added",
      "int stripped = (is_write && (want & PROT_WRITE) && !aliased &&\n                                    host_prot >= 0 && !(host_prot & VM_PROT_WRITE));" in decision)
check("the log line names the new case and keeps the old texts",
      'aliased ? "(intentional alias protection, MADEIRA_ALIAS_HONOR_PROT)" :' in src
      and 'stripped ? "<== HOST STRIPPED WRITE" : "(not a strip)"' in src and "rev=ml552" in src)
heal = src[src.index("if (stripped)\n                    {\n                        enum { WR_HPAGE"):][:700]
check("the heal itself is untouched", "if (!mprotect( hp, WR_HPAGE, want ))" in heal and "restored prot=%d on %p" in heal)

cc = shutil.which("cc") or shutil.which("clang")
if not cc:
    print("note: no host C compiler; the helpers were not run")
else:
    code = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define PROT_WRITE 2
#define VM_PROT_WRITE 2

static char pool[1];
void *ios_jit_rx_base_global = (void *)0x600000000ull;
size_t ios_jit_pool_size_global = 0x10000000ull;
static uintptr_t low_lo = 0x1000000, low_hi = 0x1400000;
int ios_jit_low_contains( uintptr_t a ) { return a >= low_lo && a < low_hi; }
uintptr_t ios_jit_anon_alias_lookup( uintptr_t a ) { return (a >= 0x7100000000ull && a < 0x7100100000ull) ? 0x7000000000ull + (a - 0x7100000000ull) : 0; }
''' + helpers + r'''
static int old_stripped( int is_write, int want, int host_prot )
{
    return (is_write && (want & PROT_WRITE) && host_prot >= 0 && !(host_prot & VM_PROT_WRITE));
}
static int new_stripped( uintptr_t si_addr, int is_write, int want, int host_prot )
{
    ''' + decision.replace("(uintptr_t)siginfo->si_addr", "si_addr") + r'''
    (void)aliased;
    return stripped;
}
int main( void )
{
    int fails = 0;
    uintptr_t addrs[] = { 0x600000000ull, 0x60fffffffull, 0x610000000ull - 1, 0x610000000ull, 0x5ffffffffull, 0x1000000, 0x13fffff, 0x1400000,
                          0x7100000000ull, 0x71000fffffull, 0x7100100000ull, 0x140001000ull, 0 };
    int expect_alias[] = { 1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 0 };
    unsigned i, v;
    const char *vals[] = { NULL, "", "0", "2", "11", "true", " 1", "1x", "1" };
#define T(c) do { if (!(c)) { printf( "FAIL line %d: %s\n", __LINE__, #c ); fails++; } } while (0)
    for (v = 0; v < sizeof(vals) / sizeof(vals[0]); v++)
    {
        if (vals[v]) setenv( "MADEIRA_ALIAS_HONOR_PROT", vals[v], 1 ); else unsetenv( "MADEIRA_ALIAS_HONOR_PROT" );
        T( ios_alias_honor_prot_on() == (vals[v] && !strcmp( vals[v], "1" )) );
    }
    for (i = 0; i < sizeof(addrs) / sizeof(addrs[0]); i++) T( ios_addr_in_exec_alias( addrs[i] ) == expect_alias[i] );
    for (v = 0; v < 2; v++)
    {
        if (v) setenv( "MADEIRA_ALIAS_HONOR_PROT", "1", 1 ); else unsetenv( "MADEIRA_ALIAS_HONOR_PROT" );
        for (i = 0; i < sizeof(addrs) / sizeof(addrs[0]); i++)
        {
            int w, want, host;
            for (w = 0; w < 2; w++)
                for (want = 0; want <= 7; want++)
                    for (host = -1; host <= 7; host++)
                    {
                        int got = new_stripped( addrs[i], w, want, host ), old = old_stripped( w, want, host );
                        if (v && expect_alias[i]) T( got == 0 );
                        else T( got == old );
                    }
        }
    }
    puts( fails ? "FAILED" : "ok: key off = the old decision for every input; key on spares exactly the executable aliases" );
    return fails != 0;
}
'''
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "t.c"
        exe = Path(d) / "t"
        c.write_text(code)
        r = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Werror", "-Wno-unused-variable", "-Wno-unused-function", "-o", str(exe), str(c)],
                           capture_output=True, text=True)
        check("the helpers and the decision compile on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            print("     " + run.stdout.strip().replace("\n", "\n     "))
            check("the decision holds for every input", run.returncode == 0, run.stdout)

rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune has the Stardew / CoreCLR rule that turns the key on", '"id": "alias-write-protection-coreclr"' in rules
      and '"lines": ["env.MADEIRA_ALIAS_HONOR_PROT = 1"]' in rules)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog documents the key, off by default", re.search(r'"env\.MADEIRA_ALIAS_HONOR_PROT": \{[^}]*"default": "0"', cat, re.S) is not None)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a list may set the key, 0 or 1", '"MADEIRA_ALIAS_HONOR_PROT",' in pol and 'case "MADEIRA_ALIAS_HONOR_PROT":' in pol)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-alias-honor-prot.py" in wf)
print("PASS: alias honor-protection (step 1)" if ok else "FAILED: alias honor-protection (step 1)")
sys.exit(0 if ok else 1)
