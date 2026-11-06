#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_SWP_LSE2 = 1 (default off): a misaligned SWP{A}{L}{H} that stays inside one 16-byte granule, on a host with FEAT_LSE2
(ml2190, upstream Madeira #518 b0742a7).

Mono rewrites a call's target in its own RWX code with an x86 XCHG; FEX runs it as SWPAL on an address that is not naturally aligned, the
page is the RX view of an alias, and the Mach store emulator (ml626) refuses the swap as unaligned: the guest gets c0000005. Upstream's
The Escapists 2 (32-bit) dies of it (Mono's handler retries, ml461 counts 2,000 identical redeliveries). With LSE2 a misaligned atomic
inside one 16-byte granule neither alignment-faults nor loses single-copy atomicity, so the same hardware SWPAL on the RW alias is the
genuine swap. In BCD's archive the 27 logs with the refusal are different: the 20 64-bit ones (Cuphead, Blasphemous, KTANE) are healthy
sessions of 100-500 s of play that recover in the guest, so the new path would change games that work today, and the switch stays off.

This host is not arm64, so the hardware instruction cannot run here: the production helper is cut from the source and compiled
(1) for arm64 (clang -target), where the check reads the three SWPAL forms back from the object file, and (2) for the host with only its
asm statements and the sysctl read replaced (each replacement is asserted), to run its decision on real memory. The decoder case is
cut from the source and run with that helper. Checks:
  - the aligned path above it is the builtin exchange, unchanged, and keeps the Mono bridge capture; the new branch has no capture;
  - the key is on only for exactly "1"; off (unset, 0, anything else) a misaligned swap is refused with nothing written and the old
    REFUSING line, byte for byte, even on an LSE2 host, inside the granule;
  - on: a misaligned swap inside one 16-byte granule, sizes 2 and 4 and 8 bytes, every offset: the value lands, the neighbours do not
    change, Rt gets the zero-extended old value, Rt = 31 discards it, Rs = 31 stores zero, Rs = Rt works;
  - on: a swap that crosses a 16-byte boundary, a host without LSE2 and x29/x30 operands are refused with nothing written, same text;
  - the handled swap logs "LSE2 SWPAL for unaligned atomic ... (ml2190)", the first four only, like the refusal always did.
Needs python3 and a C compiler.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
virt = (root / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


helper = source[source.index("static int ios_swp_lse2_on(void)"):source.index("/* ml938/ml939")]
case_start = source.index("                    else if ((insn & 0x3F20FC00) == 0x38208000)")
case_end = source.index("                    /* FEX's native backpatch lock uses CASAL on the pool RX", case_start)
case = source[case_start:case_end]

check("the sysctl header is included", "#include <sys/sysctl.h>" in source)
check("the helper decides on the granule: LSE2 host, non-null address, offset in the 16-byte granule plus the size within 16",
      "if (!ios_host_lse2 || !rw_addr || (rw_addr & 15) + nbytes > 16) return 0;" in helper
      and 'sysctlbyname( "hw.optional.arm.FEAT_LSE2", &v, &len, NULL, 0 )' in helper)
check("the helper uses the hardware swaps with acquire/release, 16, 32 and 64 bit",
      all(t in helper for t in ('swpalh %w1, %w0, [%2]', 'swpal %w1, %w0, [%2]', 'swpal %1, %0, [%2]')))
check("the aligned path is the builtin exchange, as before, with the Mono bridge capture for the 64-bit swap",
      "__atomic_exchange_n((uint64_t *)rw_addr,           in, __ATOMIC_SEQ_CST)" in case
      and "ios_mono_bridge_capture( state.__x[18], state.__x[28]," in case
      and case.count("ios_mono_bridge_capture(") == 1)
unaligned = case[case.index("if (rw_addr & align_mask)"):case.index("else\n                        {\n                            uint64_t in = (rs == 31)")]
check("the new branch has no bridge capture and refuses FP/LR operands (the Darwin __x array holds x0..x28)",
      "ios_mono_bridge_capture" not in unaligned and "(rs < 29 || rs == 31) && (rt < 29 || rt == 31)" in unaligned)
check("the key is read as exactly 1 and gates the helper call, after the register test",
      'const char *e = getenv( "MADEIRA_SWP_LSE2" );\n    return e && e[0] == \'1\' && !e[1];' in source
      and "const int lse2 = regs_ok && ios_swp_lse2_on() && ios_mach_swp_lse2( rw_addr, size_lg2, in, &old );" in unaligned)
check("nothing in virtual_ios.c is needed any more", "ios_wow_addr_in_live_window" not in virt and "ios_wow_addr_in_live_window" not in source)

cc = shutil.which("cc") or shutil.which("clang")
if not cc:
    print("note: no host C compiler; the helper and the case were not compiled")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # (1) arm64 object: the three instructions
        arm = tmp / "arm.c"
        arm.write_text("#include <stdint.h>\n#include <stddef.h>\n#include <stdlib.h>\n#include <sys/sysctl.h>\n"
                       + helper.replace("static int ios_mach_swp_lse2", "int ios_mach_swp_lse2").replace("static int ios_swp_lse2_on", "int ios_swp_lse2_on"))
        r = subprocess.run([cc, "-target", "arm64-apple-macos13", "-O1", "-Wall", "-Werror", "-c", str(arm), "-o", str(tmp / "arm.o")],
                           capture_output=True, text=True)
        if r.returncode != 0 and re.search(r"unknown target|unsupported|unable to find|no such file", r.stderr, re.I):
            print("note: this compiler or SDK cannot target arm64; the instruction forms were not read back")
        else:
            check("the helper compiles for arm64", r.returncode == 0, r.stderr[-1500:])
            if r.returncode == 0:
                dis = subprocess.run(["xcrun", "otool", "-tvV", str(tmp / "arm.o")], capture_output=True, text=True).stdout
                check("the object file holds swpalh (w, w), swpal (w, w) and swpal (x, x), the value first, the old value second",
                      re.search(r"swpalh\s+w\d+, w\d+, \[x\d+\]", dis) and re.search(r"swpal\s+w\d+, w\d+, \[x\d+\]", dis)
                      and re.search(r"swpal\s+x\d+, x\d+, \[x\d+\]", dis), dis[-800:])

        # (2) host harness: the asm replaced by the builtin exchange, the sysctl by a switch
        host = helper
        n_before = host.count("asm volatile(")
        host, n1 = re.subn(r'asm volatile\("\.arch_extension lse\\n\\tswpalh %w1, %w0, \[%2\]"\s*:\s*"=&r"\(o\) : "r"\(v\), "r"\(rw_addr\) : "memory"\);',
                           "o = __atomic_exchange_n( (uint16_t *)rw_addr, (uint16_t)v, __ATOMIC_SEQ_CST );", host)
        host, n2 = re.subn(r'asm volatile\("\.arch_extension lse\\n\\tswpal %w1, %w0, \[%2\]"\s*:\s*"=&r"\(o\) : "r"\(v\), "r"\(rw_addr\) : "memory"\);',
                           "o = __atomic_exchange_n( (uint32_t *)rw_addr, (uint32_t)v, __ATOMIC_SEQ_CST );", host)
        host, n3 = re.subn(r'asm volatile\("\.arch_extension lse\\n\\tswpal %1, %0, \[%2\]"\s*:\s*"=&r"\(o\) : "r"\(in\), "r"\(rw_addr\) : "memory"\);',
                           "o = __atomic_exchange_n( (uint64_t *)rw_addr, in, __ATOMIC_SEQ_CST );", host)
        host, n4 = re.subn(r'if \(sysctlbyname\( "hw.optional.arm.FEAT_LSE2", &v, &len, NULL, 0 \)\) v = 0;',
                           "(void)len; v = test_lse2;", host)
        check("the host harness replaces exactly the three asm statements and the sysctl read", (n_before, n1, n2, n3, n4) == (3, 1, 1, 1, 1),
              (n_before, n1, n2, n3, n4))
        code = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/sysctl.h>

static int test_lse2;
''' + host + r'''
/* the general-purpose part of Darwin's arm_thread_state64_t */
struct thread_state { uint64_t __x[29], __fp, __lr, __sp, __pc; };

static int bridge_calls;
void ios_mono_bridge_capture( unsigned long long teb, unsigned long long frame, unsigned long long pc, unsigned long long addr )
{ (void)teb; (void)frame; (void)pc; (void)addr; bridge_calls++; }

static int route( uint32_t insn, uintptr_t rw_addr, uintptr_t fault_addr, struct thread_state *st )
{
    struct thread_state state = *st;
    int emulated = 0;

    if (0) {}
''' + case + r'''
    *st = state;
    return emulated;
}

static uint32_t swp( unsigned size, unsigned a, unsigned l, unsigned rs, unsigned rn, unsigned rt )
{
    return 0x38208000u | size << 30 | a << 23 | l << 22 | rs << 16 | rn << 5 | rt;
}

static unsigned char mem[64] __attribute__((aligned(64)));
static int fails;
#define T(c) do { if (!(c)) { printf( "FAIL line %d: %s\n", __LINE__, #c ); fails++; } } while (0)

static void fill( void ) { memset( mem, 0xa5, sizeof(mem) ); }
static int others_intact( unsigned off, unsigned nbytes )
{
    for (unsigned j = 0; j < sizeof(mem); j++)
        if ((j < off || j >= off + nbytes) && mem[j] != 0xa5) return 0;
    return 1;
}

int main( int argc, char **argv )
{
    const uintptr_t far = 0x7000000000ull;      /* a 64-bit process address */
    struct thread_state st;
    int off_mode = argc > 1 && argv[1][0] == '0';

    test_lse2 = 1;
    if (off_mode)
    {
        /* the key is not exactly 1: a misaligned swap inside one granule on an LSE2 host is refused, as before */
        const char *others[] = { NULL, "", "0", "2", "11", "true", " 1", "1x" };
        unsigned v;
        for (v = 0; v < sizeof(others) / sizeof(others[0]); v++)
        {
            if (others[v]) setenv( "MADEIRA_SWP_LSE2", others[v], 1 ); else unsetenv( "MADEIRA_SWP_LSE2" );
            fill(); memset( &st, 0, sizeof(st) ); st.__x[4] = 0x11223344u;
            T( route( swp( 2, 1, 1, 4, 24, 4 ), (uintptr_t)(mem + 3), far + 3, &st ) == 0 );
            for (unsigned j = 0; j < 64; j++) T( mem[j] == 0xa5 );
            T( st.__x[4] == 0x11223344u );
        }
        /* the aligned path is not the key's business */
        unsetenv( "MADEIRA_SWP_LSE2" );
        fill(); bridge_calls = 0; memset( &st, 0, sizeof(st) ); st.__x[7] = 0x99u;
        T( route( swp( 3, 1, 1, 7, 6, 9 ), (uintptr_t)(mem + 8), far + 8, &st ) == 1 && bridge_calls == 1 );
        puts( fails ? "FAILED" : "ok: key off: refused as before, even inside one granule on an LSE2 host" );
        return fails != 0;
    }
    setenv( "MADEIRA_SWP_LSE2", "1", 1 );
    /* 1: the first four calls log: handled (4 bytes at offset 3), refused across 16, refused without LSE2, refused with x29 */
    fill(); memset( &st, 0, sizeof(st) ); st.__x[4] = 0x11223344u;
    memcpy( mem + 3, "\x01\x02\x03\x04", 4 );
    T( route( swp( 2, 1, 1, 4, 24, 4 ), (uintptr_t)(mem + 3), far + 3, &st ) == 1 );   /* the Escapists 2 swap: swpal w4, w4, [x24] */
    { uint32_t now; memcpy( &now, mem + 3, 4 ); T( now == 0x11223344u ); T( st.__x[4] == 0x04030201u ); }
    T( others_intact( 3, 4 ) );
    fill(); st.__x[4] = 1; T( route( swp( 2, 1, 1, 4, 24, 4 ), (uintptr_t)(mem + 14), far + 14, &st ) == 0 );   /* 14 + 4 > 16 */
    for (unsigned j = 0; j < 64; j++) T( mem[j] == 0xa5 );
    test_lse2 = 0; ios_host_lse2 = -1;
    st.__x[4] = 1; T( route( swp( 2, 1, 1, 4, 24, 4 ), (uintptr_t)(mem + 3), far + 3, &st ) == 0 );
    for (unsigned j = 0; j < 64; j++) T( mem[j] == 0xa5 );
    test_lse2 = 1; ios_host_lse2 = -1;
    fill(); st.__x[4] = 1; T( route( swp( 2, 1, 1, 29, 24, 4 ), (uintptr_t)(mem + 3), far + 3, &st ) == 0 );   /* FP as Rs */
    for (unsigned j = 0; j < 64; j++) T( mem[j] == 0xa5 );

    /* 2: sizes 2, 4 and 8, every misaligned offset that stays in one granule, with the model */
    for (unsigned lg = 1; lg <= 3; lg++)
        for (unsigned off = 1; off < 64; off++)
        {
            unsigned n = 1u << lg;
            int aligned = (off & (n - 1)) == 0;
            int inside = ((off & 15) + n) <= 16;
            uint64_t oldv = 0, newv = 0x0123456789abcdefull, mask = lg == 3 ? ~0ull : ((1ull << (8 * n)) - 1);
            if (off + n > 64 || aligned) continue;
            fill();
            for (unsigned k = 0; k < n; k++) mem[off + k] = (unsigned char)(0x10 + k);
            memcpy( &oldv, mem + off, n );
            memset( &st, 0, sizeof(st) ); st.__x[7] = newv; st.__x[9] = 0xdeadbeefdeadbeefull;
            int r = route( swp( lg, 1, 1, 7, 6, 9 ), (uintptr_t)(mem + off), far + off, &st );
            T( r == (inside ? 1 : 0) );
            if (inside)
            {
                uint64_t now = 0; memcpy( &now, mem + off, n );
                T( now == (newv & mask) ); T( st.__x[9] == oldv ); T( others_intact( off, n ) );
            }
            else
            {
                for (unsigned j = 0; j < 64; j++) T( j >= off && j < off + n ? mem[j] == (unsigned char)(0x10 + (j - off)) : mem[j] == 0xa5 );
                T( st.__x[9] == 0xdeadbeefdeadbeefull );
            }
        }
    /* 3: register forms: Rt = 31 discards, Rs = 31 stores zero, Rs = Rt, FP/LR refused */
    fill(); memset( &st, 0, sizeof(st) ); st.__x[7] = 0x55667788u; st.__x[2] = 0x7777u;
    T( route( swp( 2, 1, 1, 7, 6, 31 ), (uintptr_t)(mem + 5), far + 5, &st ) == 1 );
    { uint32_t now; memcpy( &now, mem + 5, 4 ); T( now == 0x55667788u ); }
    T( st.__sp == 0 && st.__x[2] == 0x7777u );
    fill(); T( route( swp( 2, 1, 1, 31, 6, 2 ), (uintptr_t)(mem + 5), far + 5, &st ) == 1 );
    { uint32_t now; memcpy( &now, mem + 5, 4 ); T( now == 0 ); T( st.__x[2] == 0xa5a5a5a5u ); }
    fill(); st.__x[3] = 0x31323334u; T( route( swp( 2, 1, 1, 3, 6, 3 ), (uintptr_t)(mem + 5), far + 5, &st ) == 1 );
    { uint32_t now; memcpy( &now, mem + 5, 4 ); T( now == 0x31323334u ); T( st.__x[3] == 0xa5a5a5a5u ); }
    fill(); T( route( swp( 2, 1, 1, 29, 6, 1 ), (uintptr_t)(mem + 5), far + 5, &st ) == 0 );
    T( route( swp( 2, 1, 1, 1, 6, 30 ), (uintptr_t)(mem + 5), far + 5, &st ) == 0 );
    for (unsigned j = 0; j < 64; j++) T( mem[j] == 0xa5 );
    /* 4: the aligned path is untouched and still captures the 64-bit swap for the Mono bridge */
    fill(); bridge_calls = 0; memset( &st, 0, sizeof(st) ); st.__x[7] = 0x99u;
    T( route( swp( 3, 1, 1, 7, 6, 9 ), (uintptr_t)(mem + 8), far + 8, &st ) == 1 && bridge_calls == 1 );
    { uint64_t now; memcpy( &now, mem + 8, 8 ); T( now == 0x99u ); T( st.__x[9] == 0xa5a5a5a5a5a5a5a5ull ); }
    /* the misaligned 64-bit swap does not capture */
    fill(); bridge_calls = 0; st.__x[7] = 0x77u;
    T( route( swp( 3, 1, 1, 7, 6, 9 ), (uintptr_t)(mem + 1), far + 1, &st ) == 1 && bridge_calls == 0 );
    /* a byte swap is never "unaligned" and keeps the aligned path */
    fill(); st.__x[7] = 0x42; T( route( swp( 0, 1, 1, 7, 6, 9 ), (uintptr_t)(mem + 3), far + 3, &st ) == 1 && mem[3] == 0x42 );
    puts( fails ? "FAILED" : "ok: with the key on, misaligned swaps inside one granule complete; everything else is refused as before" );
    return fails != 0;
}
'''
        c = tmp / "t.c"
        c.write_text(code)
        exe = tmp / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-Wno-unused-variable", "-o", str(exe), str(c)],
                           capture_output=True, text=True)
        check("the helper and the decoder case compile on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            run = subprocess.run([str(exe), "1"], capture_output=True, text=True, timeout=120)
            print("     " + run.stdout.strip().replace("\n", "\n     "))
            check("key on: the swap logic holds on real memory", run.returncode == 0, run.stdout)
            lines = [l for l in run.stderr.splitlines() if "unaligned atomic" in l]
            check("key on: the first four logs, in order: handled, refused across 16 bytes, refused without LSE2, refused with an FP operand",
                  len(lines) == 4
                  and re.fullmatch(r"\[swp-emul\] ml626 #1 LSE2 SWPAL for unaligned atomic: insn=0xb8e48304 size=4 addr=0x7000000003 rw=0x[0-9a-f]+ \(ml2190\)", lines[0])
                  and all(re.fullmatch(r"\[swp-emul\] ml626 #%d REFUSING unaligned atomic: insn=0x[0-9a-f]{8} size=4 addr=0x[0-9a-f]+ rw=0x[0-9a-f]+" % (i + 2), l)
                          for i, l in enumerate(lines[1:])), lines)
            run = subprocess.run([str(exe), "0"], capture_output=True, text=True, timeout=120)
            print("     " + run.stdout.strip().replace("\n", "\n     "))
            check("key off (unset, 0, anything but exactly 1): the swap is refused, nothing written", run.returncode == 0, run.stdout)
            lines = [l for l in run.stderr.splitlines() if "unaligned atomic" in l]
            check("key off: the refusal line is the old one, byte for byte (the first four are logged, as always)",
                  len(lines) == 4 and all(re.fullmatch(r"\[swp-emul\] ml626 #%d REFUSING unaligned atomic: insn=0xb8e48304 size=4 addr=0x7000000003 rw=0x[0-9a-f]+" % (i + 1), l)
                                         for i, l in enumerate(lines)), lines)

rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune keeps the 32-bit rule that reads the refusal (a refusal with the key off prints the old text)",
      '"id": "wow-rwx-plain-unaligned-atomic"' in rules and 'REFUSING unaligned atomic' in rules)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog documents the key, off by default", re.search(r'"env\.MADEIRA_SWP_LSE2": \{[^}]*"default": "0"', cat, re.S) is not None)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a list may set the key, 0 or 1", '"MADEIRA_SWP_LSE2",' in pol and 'case "MADEIRA_SWP_LSE2":' in pol)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-swp-lse2.py" in wf)
print("PASS: misaligned SWPAL on LSE2" if ok else "FAILED: misaligned SWPAL on LSE2")
sys.exit(0 if ok else 1)
