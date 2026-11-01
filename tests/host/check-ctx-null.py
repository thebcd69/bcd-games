#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""GetThreadContext / SetThreadContext with a NULL CONTEXT answer STATUS_ACCESS_VIOLATION (round 626, agent A1 S2).

Tony Hawk's Pro Skater 1+2 (HSDNPN, NK0SRY build 623; NZECAK build 578) calls GetThreadContext(handle, NULL) in its Demonware lookup.
Windows answers STATUS_ACCESS_VIOLATION; the shipped ARM64EC ntdll.dll's NtGetContextThread wrapper reads CONTEXT.ContextFlags itself
(`ldr w8, [x20, #0x30]` at RVA 0x5bba4) and the process ended with exit code 3. The file is a tracked binary, so the Mach exception
handler (build/ntdll-unix/signal_arm64_ios.c, block 3.5) answers for it: only for a fault at a NULL-class address at exactly that load
(or NtSetContextThread's `ldr w10, [x1, #0x44]` at RVA 0x5cd20), recognised by its own words, the prologue before it and the common
return after it, the wrapper returns STATUS_ACCESS_VIOLATION from its epilogue (ios_ctx_null_resume).

Checks: ios_ctx_null_resume, cut from the source and compiled on the host, run against the real words of the shipped
app/Madeira/arm64ec-windows/ntdll.dll: both loads with a NULL CONTEXT resume at their common return; a different fault address, a
non-NULL register, an address of 64 KB or more, another pc, an unreadable word and a changed prologue or epilogue word all answer 0
(the old path); the handler block runs only for EXC_BAD_ACCESS below 64 KB, sets x0 to STATUS_ACCESS_VIOLATION and the pc, and logs
at most four times; auto-tune's fixedIn rule and its fixture; CI.
"""
from pathlib import Path
import json, shutil, struct, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
SRC = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
DLL = root / "app/Madeira/arm64ec-windows/ntdll.dll"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


region = SRC[SRC.index("/* ctx-null-test:begin"):SRC.index("/* ctx-null-test:end */")]
blk = SRC[SRC.index("/* 3.5 (madeira-bcd, round 626, agent A1 S2)"):SRC.index("/* 3.4. Unity IL2CPP GC-marked vtable pointer")]
check("the handler block runs only for EXC_BAD_ACCESS at an address below 64 KB, with the CONTEXT registers of the two wrappers",
      "if (!handled && req->exception == EXC_BAD_ACCESS && (uint64_t)fault_addr < 0x10000)" in blk
      and "ios_ctx_null_resume( ctx_pc, (uint64_t)fault_addr, state.__x[20], state.__x[1]," in blk)
check("it answers STATUS_ACCESS_VIOLATION from the wrapper's return and logs at most four times",
      "state.__x[0] = 0xC0000005;" in blk and "__darwin_arm_thread_state64_set_pc_fptr( state, (void *)(uintptr_t)resume );" in blk
      and "handled = 1;" in blk and "< 4)" in blk)

# the shipped DLL's .text, by RVA
data = DLL.read_bytes()
lfanew = struct.unpack_from("<I", data, 0x3c)[0]
nsec = struct.unpack_from("<H", data, lfanew + 6)[0]
optsz = struct.unpack_from("<H", data, lfanew + 20)[0]
sec0 = lfanew + 24 + optsz
text = None
for k in range(nsec):
    name, vsize, va, rawsz, rawptr = struct.unpack_from("<8sIIII", data, sec0 + 40 * k)
    if name.rstrip(b"\0") == b".text":
        text = (va, data[rawptr:rawptr + rawsz])
check("the shipped ntdll.dll has a .text section", text is not None)
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned char *g_text; static size_t g_len; static uint64_t g_base;   /* g_base: where .text sits in this fake address space */
static int g_poison = -1;                                                    /* a word index read as "changed" */
static int rd( uint64_t a, uint32_t *out )
{
    if (a < g_base || a + 4 > g_base + g_len) return 0;
    memcpy( out, g_text + (a - g_base), 4 );
    if (g_poison >= 0 && a == g_base + (uint64_t)g_poison) *out ^= 1;
    return 1;
}
''' + region + r'''
int main( int argc, char **argv )
{
    FILE *f = fopen( argv[1], "rb" ); uint64_t text_rva = strtoull( argv[2], NULL, 16 ); int which, fails = 0;
    uint64_t get_pc, set_pc;
    fseek( f, 0, SEEK_END ); g_len = (size_t)ftell( f ); fseek( f, 0, SEEK_SET );
    g_text = malloc( g_len ); if (fread( g_text, 1, g_len, f ) != g_len) return 2; fclose( f );
    g_base = 0x155c40000ull - (0x5bba4 - text_rva) + 0x53ba4;   /* any base; the host pc of the logs ends in ...3ba4 */
    get_pc = g_base + (0x5bba4 - text_rva); set_pc = g_base + (0x5cd20 - text_rva);
#define T(c) do { if (!(c)) { printf( "FAIL line %d: %s\n", __LINE__, #c ); fails++; } } while (0)
    which = -1; T( ios_ctx_null_resume( get_pc, 0x30, 0, 0x1234, rd, &which ) == get_pc + 0x2d8 && which == 0 );
    which = -1; T( ios_ctx_null_resume( set_pc, 0x44, 0x1234, 0, rd, &which ) == set_pc + 0x534 && which == 1 );
    T( !ios_ctx_null_resume( get_pc, 0x38, 0, 0, rd, &which ) );             /* another field: not this load's address */
    T( !ios_ctx_null_resume( get_pc, 0x30, 0x10, 0, rd, &which ) );          /* the register does not give that address */
    T( !ios_ctx_null_resume( get_pc, 0x10030, 0x10000, 0, rd, &which ) );    /* not a NULL-class address */
    T( !ios_ctx_null_resume( get_pc + 4, 0x30, 0, 0, rd, &which ) );         /* another instruction */
    T( !ios_ctx_null_resume( 0x10, 0x30, 0, 0, rd, &which ) );               /* a pc nobody can read */
    g_poison = (int)(0x5bb88 - text_rva); T( !ios_ctx_null_resume( get_pc, 0x30, 0, 0, rd, &which ) );   /* a prologue word differs */
    g_poison = (int)(0x5be8c - text_rva); T( !ios_ctx_null_resume( get_pc, 0x30, 0, 0, rd, &which ) );   /* an epilogue word differs */
    g_poison = (int)(0x5cd18 - text_rva); T( !ios_ctx_null_resume( set_pc, 0x44, 0, 0, rd, &which ) );
    g_poison = (int)(0x5d25c - text_rva); T( !ios_ctx_null_resume( set_pc, 0x44, 0, 0, rd, &which ) );
    puts( fails ? "FAILED" : "ok: both wrappers resume at their return for a NULL CONTEXT; everything else answers 0" );
    return fails != 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if cc and text:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "text.bin").write_bytes(text[1])
        c = Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("ios_ctx_null_resume compiles on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            run = subprocess.run([str(exe), str(Path(tmp) / "text.bin"), "%x" % text[0]], capture_output=True, text=True)
            print("     " + run.stdout.strip().replace("\n", "\n     "))
            check("against the shipped ntdll.dll's words: the two NULL-CONTEXT loads resume, nothing else does", run.returncode == 0, run.stdout)
else:
    print("note: no host C compiler or no .text; the matcher was not run")

rules = (root / "build/tools/autotune_rules.py").read_text()
r = rules[rules.index('"id": "get-context-thread-null-context"'):] if '"id": "get-context-thread-null-context"' in rules else ""
r = r[:r.index("\n    },")] if r else ""
check("auto-tune knows the crash of the builds before it (fixedIn 626)", '"fixedIn": 626' in r and "hinsn=0xb9403288" in r, r[:300])
exp = root / "tests/fixtures/autotune/thps12-getcontext-null-2026-10-30.expect"
check("the fixture is there and expects the known verdict", exp.is_file() and "get-context-thread-null-context" in json.loads(exp.read_text()).get("matched", []))
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-ctx-null.py" in wf)
print("PASS: NULL CONTEXT answer" if ok else "FAILED: NULL CONTEXT answer")
sys.exit(0 if ok else 1)
