#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-bit thread that runs off its stack gets STATUS_STACK_OVERFLOW and a fingerprint, not 0x80000001 (night round 2026-11-11, agent G1 S3;
Assassin's Creed IV: Black Flag Y7XADV, build 630, a copy with DxWrapper): no Wine runs.

A 32-bit thread's stack is mapped by virtual_alloc_thread_stack inside the process's guest window: fully committed, with a no-access page and a
guard page at the bottom. The guard page's fault is served on the Mach exception thread by ios_virtual_handle_fault_for_thread, whose stack
helpers (is_inside_thread_stack_teb, grow_thread_stack_teb) knew only the thread's NATIVE stack (the comment said "no WOW branch: ARM64EC
guests are 64-bit"): the guard of a 32-bit stack was taken for a page that is not a stack, dropped, and STATUS_GUARD_PAGE_VIOLATION
(0x80000001) went to the guest, which ended with "Unhandled exception 0x80000001 ... Exception frame is not in stack limits" and nothing that
names the recursion. Stock Wine's is_inside_thread_stack has the WoW branch; this port's helpers get it with BCD's window base
(host = B + guest, B = the WOW_TEB's host address minus its guest Tib.Self), and the [stack-ovf] fingerprint gets a 32-bit sibling.

Held here:
  1. Source shape: the WoW branch comes after the native one; the three fields are converted with the window base derived from Tib.Self;
     grow_thread_stack_teb writes the WOW_TEB's Tib.StackLimit as a GUEST value; the callers still act only on a page with VPROT_GUARD
     (a touch 1 KB above the guard does nothing).
  2. The two real helpers, extracted from virtual_ios.c and compiled on the host with stand-ins for the vprot calls: the 32-bit guard page ends in
     STATUS_STACK_OVERFLOW with StackLimit lowered to a guest value and the right pages marked; a page higher up moves the guard one page down
     and returns success; the native stack, a process without a WOW_TEB and an address in neither stack behave as before. The ORIGINAL helpers (the
     text before the change, kept here) do not know the 32-bit stack: the check fails without the fix.
  3. The fingerprint ios_stack_ovf_wow32, extracted from signal_arm64_ios.c and run on this Mac against a constructed 32-bit window (TEB pair,
     PEB32 loader list with a DxWrapper-like module, an 8 MB stack full of one repeating return address): it prints the stack bounds in guest
     addresses, three samples that name dxwrapper.dll+0xa5b11 and its density; it prints nothing for a fault outside the 32-bit stack.
  4. The TEB32 / TEB offsets used (0x04, 0x08, 0x18, 0xe0c, WowTebOffset 0x2000) against the pinned wine headers when they are there.
  5. Auto-tune: the rules wow32-stack-overflow-known (noFix, the new line) and wow32-guard-page-fixed (fixedIn 652, the old signature) and the
     fixture from Y7XADV; CI runs this check.
Needs python3 and a C compiler."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    if not cond and detail:
        print(str(detail)[:2500])
    ok = ok and bool(cond)


vsrc = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ssrc = (R / "build/ntdll-unix/signal_arm64_ios.c").read_text()

# ---------------------------------------------------------------------------------------------------------------- 1. source shape
m_struct = re.search(r"struct thread_stack_info\n\{\n.*?\n\};\n", vsrc, re.S)
m_in = re.search(r"static BOOL is_inside_thread_stack_teb\( void \*ptr, struct thread_stack_info \*stack, TEB \*teb \)\n\{\n.*?\n\}\n", vsrc, re.S)
m_grow = re.search(r"static NTSTATUS grow_thread_stack_teb\( char \*page, struct thread_stack_info \*stack_info, TEB \*teb \)\n\{\n.*?\n\}\n", vsrc, re.S)
check("the three pieces are found in virtual_ios.c", m_struct and m_in and m_grow)
inside = m_in.group(0) if m_in else ""
grow = m_grow.group(0) if m_grow else ""
check("the struct has the window base", m_struct and "char  *wow_base;" in m_struct.group(0))
check("the native stack is tested first; the WoW branch follows",
      inside.find("stack->is_wow = FALSE;") < inside.find("get_wow_teb( teb )") and "if ((char *)ptr > stack->start && (char *)ptr <= stack->end) return TRUE;" in inside)
check("the WoW branch derives B from Tib.Self and converts the three fields with it",
      "stack->wow_base   = (char *)wow_teb - wow_teb->Tib.Self;" in inside
      and "stack->start      = stack->wow_base + wow_teb->DeallocationStack;" in inside
      and "stack->limit      = stack->wow_base + wow_teb->Tib.StackLimit;" in inside
      and "stack->end        = stack->wow_base + wow_teb->Tib.StackBase;" in inside
      and "stack->guaranteed = max( wow_teb->GuaranteedStackBytes, min_guaranteed );" in inside)
check("a process without a WOW_TEB (or a TEB32 with no Self) is not a 32-bit stack", "if (!wow_teb || !wow_teb->Tib.Self) return FALSE;" in inside)
check("grow_thread_stack_teb writes the WOW_TEB's StackLimit as a guest value and the native one as before",
      "if (stack_info->is_wow) get_wow_teb( teb )->Tib.StackLimit = (ULONG)(page - stack_info->wow_base);" in grow
      and "else teb->Tib.StackLimit = page;" in grow)
check("the fault service calls the helpers only for a page with VPROT_GUARD (a probe above the guard does nothing)",
      "if (vprot & VPROT_GUARD)\n    {\n        struct thread_stack_info stack_info;\n        if (!is_inside_thread_stack_teb( page, &stack_info, teb ))" in vsrc
      and "if ((get_host_page_vprot( page ) & VPROT_GUARD) &&\n            grow_thread_stack_teb( page, &stack_info, teb ))" in vsrc)

cc = shutil.which("clang") or shutil.which("cc")

# ----------------------------------------------------------------------------------------------- 2. the real helpers, compiled
ORIGINAL_INSIDE = '''static BOOL is_inside_thread_stack_teb( void *ptr, struct thread_stack_info *stack, TEB *teb )
{
    size_t min_guaranteed = max( page_size * (is_win64 ? 2 : 1), host_page_size );

    stack->start = teb->DeallocationStack;
    stack->limit = teb->Tib.StackLimit;
    stack->end   = teb->Tib.StackBase;
    stack->guaranteed = max( teb->GuaranteedStackBytes, min_guaranteed );
    stack->is_wow = FALSE;
    return ((char *)ptr > stack->start && (char *)ptr <= stack->end);
}
'''
ORIGINAL_GROW = '''static NTSTATUS grow_thread_stack_teb( char *page, struct thread_stack_info *stack_info, TEB *teb )
{
    NTSTATUS ret = 0;

    set_page_vprot_bits( page, host_page_size, VPROT_COMMITTED, VPROT_GUARD );
    mprotect_range( page, host_page_size, 0, 0 );
    if (page >= stack_info->start + host_page_size + stack_info->guaranteed)
    {
        set_page_vprot_bits( page - host_page_size, host_page_size, VPROT_COMMITTED | VPROT_GUARD, 0 );
        mprotect_range( page - host_page_size, host_page_size, 0, 0 );
    }
    else  /* inside guaranteed space -> overflow exception */
    {
        page = stack_info->start + host_page_size;
        set_page_vprot_bits( page, stack_info->guaranteed, VPROT_COMMITTED, VPROT_GUARD );
        mprotect_range( page, stack_info->guaranteed, 0, 0 );
        ret = STATUS_STACK_OVERFLOW;
    }
    teb->Tib.StackLimit = page;
    return ret;
}
'''
STRUCT_ORIG = "struct thread_stack_info\n{\n    char  *start;\n    char  *limit;\n    char  *end;\n    SIZE_T guaranteed;\n    BOOL   is_wow;\n};\n"

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int BOOL;
typedef unsigned long long SIZE_T;
typedef unsigned int ULONG;
typedef int NTSTATUS;
typedef uint8_t BYTE;
#define TRUE 1
#define FALSE 0
#define STATUS_STACK_OVERFLOW ((NTSTATUS)0xC00000FD)
#define VPROT_COMMITTED 0x01
#define VPROT_GUARD 0x02
typedef struct { struct { uint32_t StackBase, StackLimit, Self; } Tib; uint32_t DeallocationStack, GuaranteedStackBytes; } WOW_TEB;
typedef struct { struct { void *StackBase; void *StackLimit; } Tib; void *DeallocationStack; uint32_t GuaranteedStackBytes; int WowTebOffset; } TEB;
static size_t page_size = 4096, host_page_size = 16384;
static int is_win64 = 1;
#define max(a,b) ((a) > (b) ? (a) : (b))
static WOW_TEB *get_wow_teb( TEB *teb ) { return teb->WowTebOffset ? (WOW_TEB *)((char *)teb + teb->WowTebOffset) : NULL; }
static char log_[2048]; static int logn;
static void set_page_vprot_bits( char *page, size_t size, int set, int clear )
{ logn += snprintf( log_ + logn, sizeof(log_) - logn, "vprot(%llx,%zx,+%x,-%x);", (unsigned long long)(uintptr_t)page, size, set, clear ); }
static void mprotect_range( char *page, size_t size, int a, int b ) { (void)page; (void)size; (void)a; (void)b; }
@STRUCT@
@INSIDE@
@GROW@
static void reset( void ) { logn = 0; log_[0] = 0; }
int main( void )
{
    uintptr_t B = 0x7100000000ull;                               /* the process's window base */
    /* 32-bit thread: stack [0x400000, 0xc00000) in the window; [0x400000,0x404000) no access, [0x404000,0x408000) guard, StackLimit 0x408000 */
    char *buf = calloc( 1, 0x3000 );
    TEB *teb = (TEB *)buf;
    teb->WowTebOffset = 0x2000;
    WOW_TEB *w = (WOW_TEB *)(buf + 0x2000);
    uint32_t self = 0x00212000;                                  /* the WOW_TEB's guest address: B = host - Self */
    w->Tib.Self = self; w->Tib.StackBase = 0xc00000; w->Tib.StackLimit = 0x408000; w->DeallocationStack = 0x400000; w->GuaranteedStackBytes = 0;
    teb->Tib.StackBase = (void *)0x7000800000ull; teb->Tib.StackLimit = (void *)0x7000700000ull; teb->DeallocationStack = (void *)0x7000000000ull;
    uintptr_t realB = (uintptr_t)w - self;                       /* the helper's base, computed from the pair's own host address */
    struct thread_stack_info si;
    NTSTATUS st;
    int r;
    /* the thread of Y7XADV: the write at guest 0x407fb8 (host B+0x407fb8) hit the guard page */
    r = is_inside_thread_stack_teb( (void *)(realB + 0x404000), &si, teb );
    printf( "guard32 inside=%d is_wow=%d start=%llx limit=%llx end=%llx guaranteed=%llx base_ok=%d\n", r, si.is_wow,
            (unsigned long long)((uintptr_t)si.start - realB), (unsigned long long)((uintptr_t)si.limit - realB),
            (unsigned long long)((uintptr_t)si.end - realB), (unsigned long long)si.guaranteed, (uintptr_t)si.wow_base == realB );
    reset();
    st = grow_thread_stack_teb( (char *)(realB + 0x404000), &si, teb );
    printf( "guard32 grow=%x StackLimit=%x native_limit=%llx log=%s\n", (unsigned)st, w->Tib.StackLimit, (unsigned long long)(uintptr_t)teb->Tib.StackLimit, log_ );
    /* a stack whose guard sits higher: the touch moves the guard one page down and succeeds */
    w->Tib.StackLimit = 0x500000;
    r = is_inside_thread_stack_teb( (void *)(realB + 0x4fc000), &si, teb );
    reset();
    st = grow_thread_stack_teb( (char *)(realB + 0x4fc000), &si, teb );
    printf( "grow32 inside=%d grow=%x StackLimit=%x log=%s\n", r, (unsigned)st, w->Tib.StackLimit, log_ );
    /* an address in the stack's committed body is inside (the callers ask only for a VPROT_GUARD page) */
    r = is_inside_thread_stack_teb( (void *)(realB + 0x700000), &si, teb );
    printf( "body32 inside=%d is_wow=%d\n", r, si.is_wow );
    /* the native stack of the same thread: unchanged */
    r = is_inside_thread_stack_teb( (void *)0x7000010000ull, &si, teb );
    reset();
    st = grow_thread_stack_teb( (char *)0x7000010000ull, &si, teb );
    printf( "native inside=%d is_wow=%d grow=%x StackLimit=%llx wowlimit=%x\n", r, si.is_wow, (unsigned)st, (unsigned long long)(uintptr_t)teb->Tib.StackLimit, w->Tib.StackLimit );
    /* an address in neither */
    r = is_inside_thread_stack_teb( (void *)0x5000000000ull, &si, teb );
    printf( "neither inside=%d\n", r );
    /* a process without a WOW_TEB */
    TEB t64; memset( &t64, 0, sizeof(t64) );
    t64.Tib.StackBase = (void *)0x7000800000ull; t64.Tib.StackLimit = (void *)0x7000700000ull; t64.DeallocationStack = (void *)0x7000000000ull;
    r = is_inside_thread_stack_teb( (void *)(realB + 0x404000), &si, &t64 );
    printf( "no-wow inside=%d\n", r );
    return 0;
}
'''


def build(inside, grow_, struct):
    prog = HARNESS.replace("@STRUCT@", struct).replace("@INSIDE@", inside).replace("@GROW@", grow_)
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "t.c"
        c.write_text(prog)
        exe = Path(d) / "t"
        r = subprocess.run([cc, "-O0", "-Wno-pointer-to-int-cast", "-o", str(exe), str(c)], capture_output=True, text=True)
        if r.returncode:
            return None, r.stderr
        out = subprocess.run([str(exe)], capture_output=True, text=True)
        return dict(l.split(" ", 1) for l in out.stdout.strip().split("\n") if l), ""


def kv(s):
    return dict(p.split("=", 1) for p in s.split() if "=" in p)


if not cc:
    print("note: no C compiler; the compiled parts are skipped")
elif m_struct and m_in and m_grow:
    res, err = build(inside, grow, m_struct.group(0))
    check("the extracted helpers compile", res is not None, err)
    if res is not None:
        g = kv(res["guard32"].split("grow=")[0]) if "guard32" in res else {}
        # the first guard32 line is overwritten by the second print (dict keys): re-run lines in order
        out = subprocess.run
        lines = []
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / "t.c"
            c.write_text(HARNESS.replace("@STRUCT@", m_struct.group(0)).replace("@INSIDE@", inside).replace("@GROW@", grow))
            exe = Path(d) / "t"
            subprocess.run([cc, "-O0", "-Wno-pointer-to-int-cast", "-o", str(exe), str(c)], capture_output=True, text=True)
            lines = subprocess.run([str(exe)], capture_output=True, text=True).stdout.strip().split("\n")
        a = kv(lines[0])
        check("32-bit guard page: inside the 32-bit stack, is_wow, bounds converted with the base derived from Tib.Self (start 0x400000, limit 0x408000, end 0xc00000 in guest terms)",
              a.get("inside") == "1" and a.get("is_wow") == "1" and a.get("start") == "400000" and a.get("limit") == "408000" and a.get("end") == "c00000"
              and a.get("base_ok") == "1" and a.get("guaranteed") == "4000", lines[0])
        b = lines[1]
        check("...and the grow ends in STATUS_STACK_OVERFLOW (c00000fd) with the WOW_TEB's StackLimit lowered to the guest value 0x404000; the native TEB's StackLimit untouched",
              "grow=c00000fd" in b and "StackLimit=404000" in b and "native_limit=7000700000" in b, b)
        check("...the guard page itself is made committed and the guaranteed area stays committed (vprot calls on the pages of the guard)",
              "vprot(" in b and "+1,-2" in b, b)
        c2 = lines[2]
        check("a guard that sits higher moves one page down and succeeds (status 0), StackLimit = the page touched, as a guest value",
              "inside=1" in c2 and "grow=0" in c2 and "StackLimit=4fc000" in c2 and "+3,-0" in c2, c2)
        d_ = kv(lines[3])
        check("an address in the 32-bit stack's body is inside (the callers ask only for a VPROT_GUARD page)", d_.get("inside") == "1" and d_.get("is_wow") == "1", lines[3])
        e = kv(lines[4])
        check("the native stack of the same thread: inside, not wow, StackLimit written to the native TEB, the WOW_TEB's untouched (0x404000 from before)",
              e.get("inside") == "1" and e.get("is_wow") == "0" and e.get("StackLimit") == "7000010000" and e.get("wowlimit") == "4fc000", lines[4])
        check("an address in neither stack, and a process without a WOW_TEB, are not inside", "neither inside=0" in lines[5] and "no-wow inside=0" in lines[6], "\n".join(lines[5:7]))
    # the ORIGINAL helpers: the 32-bit guard page is not a stack
    res0, err0 = build(ORIGINAL_INSIDE, ORIGINAL_GROW, STRUCT_ORIG.replace("};", "};").rstrip() + "\n")
    if res0 is None:
        # the original struct has no wow_base: the harness prints si.wow_base, so give it a dummy
        res0, err0 = build(ORIGINAL_INSIDE, ORIGINAL_GROW, STRUCT_ORIG.replace("    BOOL   is_wow;\n", "    BOOL   is_wow;\n    char  *wow_base;\n"))
    check("the ORIGINAL helpers compile in the same harness", res0 is not None, err0)
    if res0 is not None:
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / "t.c"
            c.write_text(HARNESS.replace("@STRUCT@", STRUCT_ORIG.replace("    BOOL   is_wow;\n", "    BOOL   is_wow;\n    char  *wow_base;\n"))
                         .replace("@INSIDE@", ORIGINAL_INSIDE).replace("@GROW@", ORIGINAL_GROW))
            exe = Path(d) / "t"
            subprocess.run([cc, "-O0", "-Wno-pointer-to-int-cast", "-o", str(exe), str(c)], capture_output=True, text=True)
            ol = subprocess.run([str(exe)], capture_output=True, text=True).stdout.strip().split("\n")
        check("with the ORIGINAL helpers the 32-bit guard page is NOT inside any stack (the bug of Y7XADV): the check fails without the fix",
              kv(ol[0]).get("inside") == "0", ol[0])

# --------------------------------------------------------------------------------------------- 3. the fingerprint, run on a constructed window
mrd = re.search(r"static int ios_wow_rd\( uint64_t a, void \*out, size_t n \)\n\{\n.*?\n\}\n", ssrc, re.S)
mmods = re.search(r"static int ios_wow_mods\( uint64_t B, uint64_t teb64, struct ios_wow_mod \*mods \)\n\{\n.*?\n\}\n", ssrc, re.S)
mmat = re.search(r"static const struct ios_wow_mod \*ios_wow_mod_at\( uint32_t va, const struct ios_wow_mod \*mods, int n \)\n\{\n.*?\n\}\n", ssrc, re.S)
mfp = re.search(r"static int ios_stack_ovf_wow32\( uint64_t thread_teb, uint64_t fault_addr \)\n\{\n.*?\n\}\n", ssrc, re.S)
mstruct = re.search(r"#define IOS_WOW_MODS 160\nstruct ios_wow_mod \{ uint32_t base, size; char name\[32\]; \};\n", ssrc)
check("the fingerprint and its helpers are found in signal_arm64_ios.c", mrd and mmods and mmat and mfp and mstruct)
fp = mfp.group(0) if mfp else ""
check("the fingerprint reads the TEB32 through ios_wow_rd only (+0x18 Self, +0x04 StackBase, +0x08 StackLimit, +0xe0c DeallocationStack), B = TEB32 - Self",
      all(x in fp for x in ("ios_wow_rd( wteb + 0x18, &wself, 4 )", "ios_wow_rd( wteb + 0x04, &wsb, 4 )", "ios_wow_rd( wteb + 0x08, &wsl, 4 )",
                            "ios_wow_rd( wteb + 0xe0c, &wda, 4 )", "B = wteb - wself;")) and "*(uint" not in fp)
check("the line starts with the prefix the rule reads", '"[stack-ovf] rev=ml424 wow32 teb32=0x%llx' in fp)
check("it runs from the [stack-ovf] block when the 64-bit stack does not hold the fault",
      "if (!(sb > da && sb - da <= 0x4000000 && fault_addr > da && fault_addr < sb))\n                ios_stack_ovf_wow32( thread_teb, fault_addr );" in ssrc)

if cc and sys.platform == "darwin" and mrd and mmods and mmat and mfp and mstruct:
    prog = ("#include <mach/mach.h>\n#include <mach/mach_vm.h>\n#include <stdint.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n"
            "#include <sys/mman.h>\n#include <unistd.h>\n" + mstruct.group(0) + mrd.group(0) + mmods.group(0) + mmat.group(0) + mfp.group(0) + r'''
static void put32( uint8_t *R, uint32_t guest, uint32_t v ) { memcpy( R + guest, &v, 4 ); }
int main( int argc, char **argv )
{
    size_t size = 0x1000000;
    uint8_t *R = mmap( NULL, size, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 );
    uint64_t B = (uint64_t)(uintptr_t)R;
    uint32_t teb64 = 0x10000, teb32 = 0x12000, peb = 0x20000, ldr = 0x21000, head = ldr + 0x0c, e1 = 0x22000, e2 = 0x22100;
    int i;
    put32( R, teb32 + 0x04, 0xc00000 ); put32( R, teb32 + 0x08, 0x408000 ); put32( R, teb32 + 0x18, argc > 1 ? 0 : teb32 );
    put32( R, teb32 + 0xe0c, 0x400000 ); put32( R, teb32 + 0x30, peb );
    put32( R, peb + 0x0c, ldr );
    put32( R, head, e1 );                                              /* InLoadOrderModuleList.Flink */
    put32( R, e1 + 0, e2 ); put32( R, e1 + 0x18, 0x78a00000 ); put32( R, e1 + 0x20, 0x1d00000 );
    put32( R, e1 + 0x2c, 13 * 2 ); put32( R, e1 + 0x30, 0x23000 );
    put32( R, e2 + 0, head ); put32( R, e2 + 0x18, 0x7bc00000 ); put32( R, e2 + 0x20, 0x100000 );
    put32( R, e2 + 0x2c, 9 * 2 ); put32( R, e2 + 0x30, 0x23100 );
    { const char *n1 = "dxwrapper.dll", *n2 = "ntdll.dll"; for (i = 0; n1[i]; i++) R[0x23000 + 2 * i] = (uint8_t)n1[i]; for (i = 0; n2[i]; i++) R[0x23100 + 2 * i] = (uint8_t)n2[i]; }
    /* the recursion: eight words repeated over the whole stack (0xc00000 down to 0x408000): a return address into dxwrapper.dll, a frame pointer, ... */
    for (uint32_t a = 0x408000; a < 0xc00000; a += 32)
    {
        put32( R, a + 0, 0x78a95b11 ); put32( R, a + 4, a + 0x20 ); put32( R, a + 8, 0x78a95b11 ); put32( R, a + 12, 0x41 );
        put32( R, a + 16, 0x7bc10123 ); put32( R, a + 20, 0 ); put32( R, a + 24, 0x78a95b11 ); put32( R, a + 28, 0xdeadbeef );
    }
    uint64_t t64 = B + teb64;
    int r1 = ios_stack_ovf_wow32( t64, B + 0x407fb8 );                  /* the guard page of the stack */
    int r2 = ios_stack_ovf_wow32( t64, 0x7000010000ull );               /* an address outside the 32-bit stack */
    int r3 = ios_stack_ovf_wow32( t64, B + 0x3ff000 );                  /* below DeallocationStack */
    fprintf( stderr, "RESULT %d %d %d\n", r1, r2, r3 );
    return 0;
}
''')
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "t.c"
        c.write_text(prog)
        exe = Path(d) / "t"
        r = subprocess.run([cc, "-O1", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the extracted fingerprint compiles on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            out = subprocess.run([str(exe)], capture_output=True, text=True)
            err = out.stderr
            check("it reports the 32-bit stack's bounds in guest addresses (StackBase 0xc00000, StackLimit 0x408000, DeallocationStack 0x400000, reserve 0x800000)",
                  "[stack-ovf] rev=ml424 wow32 teb32=" in err and "StackBase=0xc00000 StackLimit=0x408000 DeallocationStack=0x400000 reserve=0x800000" in err, err[-1500:])
            check("...with the three samples naming dxwrapper.dll+0x95b11 (0x78a95b11 - 0x78a00000) and ntdll.dll, and the density of the repeated return address",
                  "dxwrapper.dll+0x95b11" in err and "ntdll.dll+0x10123" in err and "w0 density: 78a95b11 x" in err and "w1 density:" in err and "w2 density:" in err
                  and "(2 modules)" in err, err[-1500:])
            check("it returns 1 for the fault on the 32-bit stack and 0 for an address outside it and one below DeallocationStack, printing nothing for those",
                  "RESULT 1 0 0" in err and err.count("[stack-ovf] rev=ml424 wow32 teb32=") == 1, err[-600:])
            out2 = subprocess.run([str(exe), "noself"], capture_output=True, text=True)
            check("a TEB32 with no Self (not a 32-bit thread) is not reported", "RESULT 0 0 0" in out2.stderr and "[stack-ovf]" not in out2.stderr, out2.stderr[-600:])
else:
    print("note: the fingerprint run needs macOS and a C compiler; skipped")

# ------------------------------------------------------------------------------------------------------------- 4. offsets vs the wine headers
wine = Path(os.environ.get("WINE_SRC", R / "wine"))
if not (wine / "build-macos/include/config.h").exists():
    alt = Path.home() / "madeira-bcd/wine"
    if (alt / "build-macos/include/config.h").exists():
        wine = alt
if cc and (wine / "build-macos/include/config.h").exists() and (wine / "include/winternl.h").exists():
    off = ('#include "config.h"\n#include <stddef.h>\n#include <stdio.h>\n#define WIN32_NO_STATUS\n#include "windef.h"\n#include "winbase.h"\n#include "winternl.h"\n'
           'int main(void){printf("%zx %zx %zx %zx %zx %zx\\n",offsetof(TEB32,Tib.StackBase),offsetof(TEB32,Tib.StackLimit),offsetof(TEB32,Tib.Self),'
           'offsetof(TEB32,Peb),offsetof(TEB32,DeallocationStack),offsetof(TEB,WowTebOffset));return 0;}\n')
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "o.c"
        c.write_text(off)
        exe = Path(d) / "o"
        r = subprocess.run([cc, "-D__WINESRC__", "-DWINE_UNIX_LIB", "-include", str(wine / "build-macos/include/config.h"), "-I" + str(wine / "build-macos/include"),
                            "-I" + str(wine / "include"), "-o", str(exe), str(c)], capture_output=True, text=True)
        if r.returncode:
            print("note: the wine headers did not compile here; offsets not checked:", r.stderr[-200:])
        else:
            got = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
            check("TEB32 offsets of the fingerprint (StackBase 4, StackLimit 8, Self 0x18, Peb 0x30, DeallocationStack 0xe0c) and TEB.WowTebOffset 0x180c match the pinned wine headers",
                  got == ["4", "8", "18", "30", "e0c", "180c"], got)
else:
    print("note: no pinned wine headers; the TEB32 offsets are not compared (set WINE_SRC)")

# ------------------------------------------------------------------------------------------------------------------ 5. auto-tune and CI
rules = (R / "build/tools/autotune_rules.py").read_text()
check("rule wow32-stack-overflow-known exists (noFix, signal [stack-ovf] rev=ml424 wow32)",
      re.search(r'"id": "wow32-stack-overflow-known".{0,300}"noFix": True', rules, re.S) is not None and 's("[stack-ovf] rev=ml424 wow32")' in rules)
check("rule wow32-guard-page-fixed exists (fixedIn 652, the old signature: 0x80000001 + Exception frame is not in stack limits)",
      re.search(r'"id": "wow32-guard-page-fixed".{0,300}"fixedIn": 652', rules, re.S) is not None
      and 's("Unhandled exception 0x80000001")' in rules and 's("Exception frame is not in stack limits")' in rules)
check("the fixture of Y7XADV exists (matched wow32-guard-page-fixed, known), and the synthetic one for the build-652 report (wow32-stack-overflow-known)",
      all((R / "tests/fixtures/autotune" / n).exists() for n in ("ac4-wow32-guard-page-2026-11-06.log", "ac4-wow32-guard-page-2026-11-06.expect",
                                                                 "synthetic-wow32-stack-overflow-report.log", "synthetic-wow32-stack-overflow-report.expect"))
      and '"wow32-guard-page-fixed"' in (R / "tests/fixtures/autotune/ac4-wow32-guard-page-2026-11-06.expect").read_text()
      and '"wow32-stack-overflow-known"' in (R / "tests/fixtures/autotune/synthetic-wow32-stack-overflow-report.expect").read_text())
check("CI runs this check", "tests/host/check-wow-stack-guard.py" in (R / ".github/workflows/build-ipa.yml").read_text())
print("PASS: a 32-bit thread's stack guard is a stack guard" if ok else "FAILED")
sys.exit(0 if ok else 1)
