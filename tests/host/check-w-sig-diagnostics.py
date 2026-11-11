#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The log-only diagnostics of the night round 2026-11-11 (worker W-SIG): each one is compiled on the host from the REAL source and run against memory this check builds.

The diagnostics (every one log only, off unless its key is set or already on a path that ends the session; nothing here changes what a game does):
  G3 S4   [x64-fault]      the 64-bit counterpart of [x86-fault] (ios_x86_fault_diag.h ios_x64_fault_av): registers, the RIP's module identity, 96 bytes at each
                           register's object, a second level. Crash Bandicoot N. Sane Trilogy SWC63G, Dark Deception PDWDVD and TFV0ZV.
  T1 S10, G3 S10   the 32-bit report's page state (vm_region_recurse), the head of the 64 KB allocation and a second read of the objects after 50 ms
                           (ios_x86f_page_state / ios_x86f_alloc_start / ios_x86f_reread_report). CrossCode 33JVE2 and E6TF4Q.
  G1 S10, D S13    [spin-code]     the bytes at the one address a 32-bit thread spins at, and the jumps through import slots followed, with the import named
                           (ios_x86_spin_code, called from server_ios.c's [rip-profile]). Assassin's Creed: Brotherhood 8T7MA0, ZA3QRV; Isaac CMXRG6.
  G8 S4, D S16     [term-code]     the code at a 32-bit process's own NtTerminateProcess (process_ios.c ios_t32_code_rows). Resident Evil 5 FG7VCN.
  G2 S17           [fault-peek]    MADEIRA_FAULT_PEEK's list parser (signal_arm64_ios.c ios_fault_peek_parse). Modern Warfare 2 (2009) 2MAH7J.
  G2 S10           [noexec-who] alias   the three views of the RIP's 16 KB page (ios_noexec_alias_views). Black Mesa 8V90T6.
  A4 S6            [teb-uaf]       the ring of the last 16 frees of the thread-reap hand-off (thread_ios.c ios_teb_free_lookup). Ryse 70HE81.
and, by source shape (they run in threads or handlers a host check cannot start): G11 S5 [x86-live] at the ILL (signal_arm64_ios.c), G9 S7 [badrip] slot bytes,
G6 S13 [xp-names] stacks, T1 S9 MADEIRA_SPIN_DUMP_REGS, G2 S16 MADEIRA_STUCK_WAIT_DIAG (wineserver), the hooks of server_ios.c, and the auto-tune rules and fixtures.
Needs python3 and a C compiler on a Mac (arm64 or x86_64: arm64 thread state is stood in for)."""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True
NT = R / "build/ntdll-unix"


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    if not cond and detail:
        print(str(detail)[:3000])
    ok = ok and bool(cond)


def cut(text, signature, closer="\n}\n"):
    a = text.index(signature)
    b = text.index(closer, a)
    return text[a:b + len(closer)]


sig = (NT / "signal_arm64_ios.c").read_text()
thr = (NT / "thread_ios.c").read_text()
prc = (NT / "process_ios.c").read_text()
srv = (NT / "server_ios.c").read_text()
hdr = (NT / "ios_x86_fault_diag.h").read_text()
wsd = (R / "build/wineserver/fd_ios.c").read_text()
rules = (R / "build/tools/autotune_rules.py").read_text()
wf = (R / ".github/workflows/build-ipa.yml").read_text()
fixdir = R / "tests/fixtures/autotune"

# ------------------------------------------------------------------------------------------------------------------ source shape
check("G11 S5: the ILL report prints the guest registers by the ARM64EC mapping, capped at 3",
      "ILL diag: [x86-live] guest RIP(x6)=0x%llx RAX=0x%llx" in sig and "if (__sync_add_and_fetch( &ill_live_n, 1 ) <= 3)" in sig
      and "REGn_sig(8, context)" in sig and "REGn_sig(27, context)" in sig and "REGn_sig(23, context)" in sig)
check("G9 S7: [badrip] prints the producing load's slot, 40 bytes from EA-16, through the fault-safe reader",
      "slot EA=0x%llx, 40 bytes from EA-16:" in sig and "ios_ajan2_read( ea - 16, around, sizeof(around) )" in sig)
check("G6 S13: [xp-names] gets the stack of a native thread above 50 % of a core, once, 8 threads at most, never the sampler itself; the frames are copied while the thread is held and "
      "named after it runs",
      "pt + et >= 0.5 * dt_ms" in srv and "static void ios_xp_native_stack( mach_port_t th, uint64_t tid, double busy_pct )" in srv
      and "if (th == pthread_mach_thread_np( pthread_self() )) return;" in srv and "static uint64_t done[8];" in srv
      and srv.index("thread_resume( th );", srv.index("static void ios_xp_native_stack")) < srv.index("ios_ts_sym( pcs[i]", srv.index("static void ios_xp_native_stack")))
check("T1 S9: MADEIRA_SPIN_DUMP_REGS (default off) makes each [spin] look print 0x80 bytes at RDI, RBX and RSI",
      'getenv( "MADEIRA_SPIN_DUMP_REGS" );   /* 1:' in srv and "if (ios_spin_dump_regs_on())" in srv and '{ "RDI", st.__x[26] }, { "RBX", st.__x[27] }, { "RSI", st.__x[25] }' in srv)
check("G1 S10 / D S13: the profile calls [spin-code] for a 32-bit top bucket with 90 % of the RUNNING samples, with the TEB of a RUNNING sampled thread",
      "(unsigned long long)rb[0].n * 100 >= 90ull * rtotal" in srv and "ios_x86_spin_code( run_teb ? run_teb : rp_teb, (uint32_t)rb[0].base );" in srv
      and "run_teb = tg_teb[t];" in srv and "tg_teb[ntg] = teb_k;" in srv)
check("G8 S4 / D S16: [term-code] only with the dump key, 3 a process, 64 bytes at EIP and 192 before / 32 after the first return address of the program's image",
      'if (ios_x86_fault_switch() && __sync_add_and_fetch( &code_n, 1 ) <= 3)' in prc and 'ios_t32_code_rows( win, "at EIP", eip, 0, 64 );' in prc
      and 'ios_t32_code_rows( win, "ret", mret, 192, 32 );' in prc)
check("G2 S16: the wineserver reads MADEIRA_STUCK_WAIT_DIAG once; desktop mode still dumps queues and waits, the new key only the waits, every 10 s, nothing else changes",
      'getenv("MADEIRA_STUCK_WAIT_DIAG");   /* 1:' in wsd and "stuck_on = (sw && *sw == '1' && !sw[1]);" in wsd and "if (qdump_on || stuck_on)" in wsd
      and "if (qdump_on) ios_dump_msg_queues();" in wsd and wsd.count("ios_dump_stuck_waits();") == 1 and 'getenv("MADEIRA_DESKTOP")' in wsd)
check("G2 S17: MADEIRA_FAULT_PEEK is read once (literal getenv with the catalog note), prints only for a 64-bit program's first bad access in JIT code, once per process, 4 processes at most",
      'getenv( "MADEIRA_FAULT_PEEK" );   /* <RVA-hex>:<length>' in sig and "if (!ios_av_args_read( thread_teb + IOS_WN32_TEB_WOWOFF, &woff, 4 ) || woff != 0) return;" in sig
      and "if (ndone >= 4)" in sig and "ios_fault_peek( &state, thread_teb, req->exception );" in sig)
check("G3 S4: the 64-bit report hooks the same Mach UNHANDLED path as [rwx-fault], for a bad access only, behind the same key (MADEIRA_X86_FAULT_DUMP); a 32-bit process is left to its own report",
      "if (req->exception == EXC_BAD_ACCESS) ios_x64_fault_av( &state, thread_teb, (uint64_t)fault_addr );" in sig
      and "if (!ios_x86_fault_enabled() || !ios_av_args_pc_is_jit( pc ) || !thread_teb) return;" in hdr and "woff != 0) return;    /* a 32-bit process has its own report */" in hdr)
check("A4 S6: the ring is fed only by the raw hand-off's free (before virtual_free_teb), and the fault reports ask it (Mach UNHANDLED and the foreign-fault decline)",
      thr.count("ios_teb_free_note( teb );") == 1 and "ios_teb_free_note( teb );   /* A4 S6: log only */\n            virtual_free_teb( teb );" in thr
      and "ios_teb_uaf_report( (uintptr_t)fault_addr );   /* A4 S6 */" in sig and "ios_teb_uaf_report( (uintptr_t)addr );" in sig)
check("G2 S10: the alias views line follows [noexec-who] in the same capped note", "ios_noexec_who( addr, rip, (unsigned int)tid );\n    ios_noexec_alias_views( addr );" in sig)
check("the CI steps for this check and the four new checks of this worker exist",
      all(("python3 tests/host/%s.py" % n) in wf for n in ("check-w-sig-diagnostics", "check-bus-handler-safe-reads", "check-store-undecoded-report", "check-wow-stack-guard", "check-exit-pool")))

# ------------------------------------------------------------------------------------------------------------------ the harness
cc = shutil.which("clang") or shutil.which("cc")
if not cc or sys.platform != "darwin":
    print("note: the compiled runs need a Mac and a C compiler; skipped")
    print("PASS (source shape only)" if ok else "FAILED")
    sys.exit(0 if ok else 1)

peek_parse = cut(sig, "static int ios_fault_peek_parse( const char *spec, uint64_t *rva, unsigned *len, int max )\n{")
noexec_alias = cut(sig, "static void ios_noexec_alias_views( uint64_t addr )\n{")
t32_read = cut(prc, "static int ios_t32_read( uint64_t addr, void *buf, size_t len )\n{")
t32_rows = cut(prc, "static void ios_t32_code_rows( uint64_t win, const char *what, uint32_t at, unsigned before, unsigned after )\n{")
ring_a = thr.index("struct ios_teb_free_rec {")
ring_b = thr.index("static void ios_teb_free_note( TEB *teb )")
ring_lookup = cut(thr, "int ios_teb_free_lookup( uintptr_t addr, char *out, size_t outsz )\n{")
ring_decl = thr[ring_a:ring_b]

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stddef.h>
#include <time.h>
#include <pthread.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
/* stand-ins: arm64 thread state, the engine's JIT bookkeeping, a TEB whose WowTebOffset is at 0x180c */
typedef struct { uint64_t __x[29]; uint64_t __fp, __lr, __sp, __pc; uint32_t __cpsr; } arm_thread_state64_t;
#define __darwin_arm_thread_state64_get_pc(s) ((s).__pc)
#define STDERR_FILENO 2
void *ios_jit_rx_base_global; size_t ios_jit_pool_size_global;
static int ios_jit_low_contains( uintptr_t a ) { (void)a; return 0; }
static uint64_t g_mod_base, g_mod_size;                       /* the one "registered 64-bit module" */
static const char *ios_pe_module_name( uint64_t base ) { (void)base; return "game.exe"; }
unsigned long long ios_jit_module_base_for_va( unsigned long long va, unsigned long long *size_out )
{ if (g_mod_base && va >= g_mod_base && va < g_mod_base + g_mod_size) { *size_out = g_mod_size; return g_mod_base; } return 0; }
static uint64_t g_exact_rip;
uint64_t ios_native_rip_from_hostpc( uint64_t a, uint64_t b, const char **why ) { (void)a; (void)b; *why = "stub"; return g_exact_rip; }
int ios_dump_fault_region_report( void *addr ) { (void)addr; return 1; }
void ios_x86f_alloc_ring_dump( uint32_t fault ) { (void)fault; }
#define IOS_WN32_TEB_WOWOFF 0x180c
#include "ios_av_args_diag.h"
#include "ios_wow_null_diag.h"
#include "ios_int3_x86_diag.h"
#include "ios_x86_fault_diag.h"
typedef uintptr_t ULONG_PTR;
/* the extracted pieces */
@PEEK@
static int ios_ajan2_read( uint64_t addr, void *buf, size_t len ) { return ios_av_args_read( addr, buf, len ); }
static uintptr_t g_alias_rw, g_alias_rx;
uintptr_t ios_jit_anon_alias_lookup( uintptr_t a ) { (void)a; return g_alias_rw; }
uintptr_t ios_jit_anon_alias_lookup_rx( uintptr_t a ) { (void)a; return g_alias_rx; }
@NOEXEC@
@T32READ@
@T32ROWS@
@RINGDECL@
@RINGLOOKUP@

static uint8_t *window(void)
{
    /* a 4 GB-aligned window: the spin report derives the window base from the TEB's address */
    void *want = (void *)0x2000000000ULL;
    uint8_t *w = mmap( want, 0x4000000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 );
    return w == want ? w : NULL;
}
static void put32( uint8_t *W, uint32_t g, uint32_t v ) { memcpy( W + g, &v, 4 ); }
static void put16( uint8_t *W, uint32_t g, uint16_t v ) { memcpy( W + g, &v, 2 ); }
static void put_ascii( uint8_t *W, uint32_t g, const char *s ) { memcpy( W + g, s, strlen( s ) + 1 ); }
static void put_wide( uint8_t *W, uint32_t g, const char *s ) { size_t i; for (i = 0; s[i]; i++) put16( W, g + 2 * i, (unsigned char)s[i] ); }

/* a 32-bit process in the window: TEB64 at 0x10000 (WowTebOffset 0x2000), TEB32 at 0x12000, PEB32 at 0x20000, three modules */
static void build_process( uint8_t *W )
{
    uint32_t ldr = 0x21000, head = ldr + 0x0c;
    struct { uint32_t base, size; const char *name; uint32_t e; } mods[3] = { { 0x400000, 0x400000, "game.exe", 0x22000 }, { 0x1000000, 0x100000, "winmm.asi", 0x22100 },
                                                                            { 0x2000000, 0x100000, "kernel32.dll", 0x22200 } };
    int i;

    put32( W, 0x10000 + 0x180c, 0x2000 );
    put32( W, 0x12000 + 0x18, 0x12000 ); put32( W, 0x12000 + 0x30, 0x20000 );
    put32( W, 0x20000 + 0x0c, ldr );
    put32( W, head, mods[0].e );
    for (i = 0; i < 3; i++)
    {
        put32( W, mods[i].e, i < 2 ? mods[i + 1].e : head );
        put32( W, mods[i].e + 0x18, mods[i].base ); put32( W, mods[i].e + 0x20, mods[i].size );
        put32( W, mods[i].e + 0x2c, (uint32_t)(strlen( mods[i].name ) * 2) ); put32( W, mods[i].e + 0x30, 0x23000 + i * 0x100 );
        put_wide( W, 0x23000 + i * 0x100, mods[i].name );
    }
    /* winmm.asi: a PE32 with one import descriptor (kernel32.dll: Sleep, GetTickCount); its IAT is at RVA 0x2300 */
    {
        uint32_t b = 0x1000000, nt = b + 0x80;
        put16( W, b, 0x5a4d ); put32( W, b + 0x3c, 0x80 ); put32( W, nt, 0x4550 ); put16( W, nt + 0x18, 0x10b );
        put32( W, nt + 0x80, 0x2000 ); put32( W, nt + 0x84, 0x100 );                 /* import directory */
        put32( W, b + 0x2000 + 0, 0x2100 ); put32( W, b + 0x2000 + 12, 0x2200 ); put32( W, b + 0x2000 + 16, 0x2300 );   /* OFT, Name, FirstThunk */
        put32( W, b + 0x2100, 0x2400 ); put32( W, b + 0x2104, 0x2410 ); put32( W, b + 0x2108, 0 );
        put32( W, b + 0x2300, 0x1000500 ); put32( W, b + 0x2304, 0x2000100 );          /* the IAT: slot 0 -> the hook stub, slot 1 -> kernel32 */
        put_ascii( W, b + 0x2200, "kernel32.dll" ); put16( W, b + 0x2400, 0 ); put_ascii( W, b + 0x2402, "Sleep" );
        put16( W, b + 0x2410, 0 ); put_ascii( W, b + 0x2412, "GetTickCount" );
    }
    /* the spin: game.exe+0x1000 is jmp [winmm.asi IAT slot 0]; the hook stub at winmm.asi+0x500 is jmp [IAT slot 1]; kernel32+0x100 is ret 4 */
    { const uint8_t j0[6] = { 0xff, 0x25, 0x00, 0x23, 0x00, 0x01 }, j1[6] = { 0xff, 0x25, 0x04, 0x23, 0x00, 0x01 }, rt[3] = { 0xc2, 0x04, 0x00 };
      memcpy( W + 0x401000, j0, 6 ); memcpy( W + 0x1000500, j1, 6 ); memcpy( W + 0x2000100, rt, 3 ); }
}

int main( int argc, char **argv )
{
    const char *mode = argc > 1 ? argv[1] : "";
    uint8_t *W = window();

    if (!strcmp( mode, "peek" ))
    {
        uint64_t rva[8]; unsigned len[8]; int n, i;
        const char *specs[] = { "0x1429b00:128,0x2714a0:64,0x204500:48,0x1b4ab68:16,0x1b4f300:16", "1429b00:128", "1:1,2:256", "1:257", "1:0", "zz:1", "1:2,", "1:2,3", "", "1:1,2:2,3:3,4:4,5:5,6:6,7:7,8:8,9:9" };
        for (i = 0; i < (int)(sizeof(specs) / sizeof(specs[0])); i++)
        {
            n = ios_fault_peek_parse( specs[i], rva, len, 8 );
            printf( "[%d]=%d", i, n );
            if (n > 0) printf( " first=%llx:%u last=%llx:%u", (unsigned long long)rva[0], len[0], (unsigned long long)rva[n - 1], len[n - 1] );
            printf( "\n" );
        }
        return 0;
    }
    if (!W) { printf( "skip: no 4 GB-aligned window here\n" ); return 0; }
    build_process( W );
    if (!strcmp( mode, "spin" ))
    {
        setenv( "MADEIRA_X86_FAULT_DUMP", "1", 1 );
        uint64_t teb = (uint64_t)(uintptr_t)W + 0x10000;
        ios_x86_spin_code( teb, 0x401000 );                           /* shot 1 */
        ios_x86_spin_code( teb, 0x401000 );                           /* shot 2 */
        ios_x86_spin_code( teb, 0x401000 );                           /* nothing: two a process */
        return 0;
    }
    if (!strcmp( mode, "spin64" ))                                      /* a 64-bit thread (WowTebOffset 0): nothing */
    {
        setenv( "MADEIRA_X86_FAULT_DUMP", "1", 1 );
        put32( W, 0x10000 + 0x180c, 0 );
        ios_x86_spin_code( (uint64_t)(uintptr_t)W + 0x10000, 0x401000 );
        return 0;
    }
    if (!strcmp( mode, "spinoff" ))                                     /* the key unset: nothing */
    {
        unsetenv( "MADEIRA_X86_FAULT_DUMP" );
        ios_x86_spin_code( (uint64_t)(uintptr_t)W + 0x10000, 0x401000 );
        return 0;
    }
    if (!strcmp( mode, "page" ))
    {
        uint64_t win = (uint64_t)(uintptr_t)W;
        struct ios_x86f_reread rr[2];
        struct ios_wn32_mod mods[1] = { { 0x400000, 0x400000, "game.exe" } };
        memset( W + 0x300000, 0, 0x100 ); put32( W, 0x300004, 0x0db82238 );
        ios_x86f_page_state( win, "ESI", 0x300004 );
        ios_x86f_page_state( win, "the fault address", 0x3ff0000 );    /* 0x3ff0000 + win is inside the mapping: a page that exists */
        ios_x86f_page_state( win, "EDI", 0x4000000 + 0x100000 );       /* past the window's end: no region of ours (or another mapping) */
        put32( W, 0x300040, 0x11112222 );                              /* the allocation head, 64 KB below */
        ios_x86f_alloc_start( win, "ESI", 0x300004 );
        rr[0].name = "ESI"; rr[0].v = 0x300000; rr[0].fgot = ios_i3x_read( win + 0x300000, rr[0].first, rr[0].fvalid, IOS_X86F_OBJ_BYTES );
        rr[1].name = "EDI"; rr[1].v = 0x310000; rr[1].fgot = ios_i3x_read( win + 0x310000, rr[1].first, rr[1].fvalid, IOS_X86F_OBJ_BYTES );
        put32( W, 0x300004, 0 );                                       /* ESI+4 reads back as zero after the pause: the report says so */
        ios_x86f_reread_report( win, rr, 2, mods, 1 );
        return 0;
    }
    if (!strcmp( mode, "x64" ) || !strcmp( mode, "x64-wow" ) || !strcmp( mode, "x64-off" ) || !strcmp( mode, "x64-readable" ))
    {
        arm_thread_state64_t st; memset( &st, 0, sizeof(st) );
        uint8_t *mod = mmap( NULL, 0x100000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 );
        uint8_t *heap = mmap( NULL, 0x10000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 );
        uint8_t *jit = mmap( NULL, 0x10000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 );
        uint8_t *teb = mmap( NULL, 0x4000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 );
        uint64_t frame[16] = { 0x123456, 0, 0, 0x1402714ca };
        g_mod_base = (uint64_t)(uintptr_t)mod; g_mod_size = 0x100000;
        /* a PE32+ header: Machine 0x8664, 2 sections, TimeDateStamp, SizeOfOptionalHeader 0xf0, SizeOfImage, CheckSum; sections .text and .data */
        put32( mod, 0x3c, 0x80 ); put32( mod, 0x80, 0x4550 ); put16( mod, 0x84, 0x8664 ); put16( mod, 0x86, 2 ); put32( mod, 0x88, 0x5f5e1000 ); put16( mod, 0x94, 0xf0 );
        put16( mod, 0x98, 0x20b ); put32( mod, 0x98 + 0x38, 0x100000 ); put32( mod, 0x98 + 0x40, 0xabcdef01 );
        memcpy( mod + 0x80 + 0x18 + 0xf0, ".text\0\0\0", 8 ); put32( mod, 0x80 + 0x18 + 0xf0 + 8, 0x80000 ); put32( mod, 0x80 + 0x18 + 0xf0 + 12, 0x1000 ); put32( mod, 0x80 + 0x18 + 0xf0 + 36, 0x60000020 );
        memcpy( mod + 0x80 + 0x18 + 0xf0 + 40, ".data\0\0\0", 8 ); put32( mod, 0x80 + 0x18 + 0xf0 + 40 + 8, 0x1000 ); put32( mod, 0x80 + 0x18 + 0xf0 + 40 + 12, 0x90000 ); put32( mod, 0x80 + 0x18 + 0xf0 + 40 + 36, 0xc0000040 );
        /* the object RBX points at: a vtable-like code pointer, a heap pointer to a second object, a NULL array pointer */
        { uint64_t obj[12] = { (uint64_t)(uintptr_t)mod + 0x1234, (uint64_t)(uintptr_t)heap + 0x2000, 0, 0xdead, 0, 0, 0x41, 0, 0, 0, 0, 0 }; memcpy( heap, obj, sizeof(obj) ); }
        { uint64_t second[4] = { 0x1111, 0x2222, 0x3333, 0x4444 }; memcpy( heap + 0x2000, second, sizeof(second) ); }
        ios_jit_rx_base_global = jit; ios_jit_pool_size_global = 0x10000;
        st.__pc = (uint64_t)(uintptr_t)jit + 0x100; st.__x[28] = (uint64_t)(uintptr_t)frame;
        g_exact_rip = (uint64_t)(uintptr_t)mod + 0x1000;
        st.__x[27] = (uint64_t)(uintptr_t)heap;                          /* RBX */
        st.__x[23] = (uint64_t)(uintptr_t)heap + 0x8000;                 /* RSP: readable, zeros */
        st.__x[8] = 0; st.__x[0] = (uint64_t)(uintptr_t)mod + 0x2000;    /* RAX = 0, RCX = a module address (code or data) */
        put32( teb, 0x180c, !strcmp( mode, "x64-wow" ) ? 0x2000 : 0 );
        if (!strcmp( mode, "x64-off" )) unsetenv( "MADEIRA_X86_FAULT_DUMP" ); else setenv( "MADEIRA_X86_FAULT_DUMP", "1", 1 );
        if (!strcmp( mode, "x64-readable" )) { ios_x64_fault_av( &st, (uintptr_t)teb, (uint64_t)(uintptr_t)heap + 8 ); return 0; }    /* a readable page: a protection fault, not a wild pointer */
        ios_x64_fault_av( &st, (uintptr_t)teb, 0x8 );                    /* report 1 */
        ios_x64_fault_av( &st, (uintptr_t)teb, 0x8 );                    /* the redelivery of the same fault: nothing */
        return 0;
    }
    if (!strcmp( mode, "alias" ) || !strcmp( mode, "alias-zero" ))
    {
        /* three 16 KB-aligned pages (the line works on the 16 KB page of the RIP) */
        uint8_t *g = (uint8_t *)(((uintptr_t)mmap( NULL, 0x8000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 ) + 0x3fff) & ~(uintptr_t)0x3fff);
        uint8_t *rw = (uint8_t *)(((uintptr_t)mmap( NULL, 0x8000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 ) + 0x3fff) & ~(uintptr_t)0x3fff);
        uint8_t *rx = (uint8_t *)(((uintptr_t)mmap( NULL, 0x8000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 ) + 0x3fff) & ~(uintptr_t)0x3fff);
        if (!strcmp( mode, "alias" )) { memset( rw, 0x90, 0x4000 ); memset( rx, 0x90, 0x4000 ); }   /* the guest view is zero while rw / rx hold code: BCD's aliasing */
        g_alias_rw = (uintptr_t)rw; g_alias_rx = (uintptr_t)rx;
        ios_noexec_alias_views( (uint64_t)(uintptr_t)g + 0x1230 );
        return 0;
    }
    if (!strcmp( mode, "rows" ))
    {
        uint8_t *m = mmap( NULL, 0x8000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 );
        size_t i;
        for (i = 0; i < 0x8000; i++) m[i] = (uint8_t)i;
        munmap( m + 0x4000, 0x4000 );                                   /* the upper half is gone */
        /* win = m: guest 0x100 .. : 64 bytes at 0x1000 (readable), 192 before + 32 after 0x3fe0 (the last 32 cross into the hole) */
        ios_t32_code_rows( (uint64_t)(uintptr_t)m, "at EIP", 0x1000, 0, 64 );
        ios_t32_code_rows( (uint64_t)(uintptr_t)m, "ret", 0x2000, 192, 32 );
        ios_t32_code_rows( (uint64_t)(uintptr_t)m, "ret", 0x3ff0, 32, 32 );
        return 0;
    }
    if (!strcmp( mode, "ring" ))
    {
        memset( ios_teb_free_ring, 0, sizeof(ios_teb_free_ring) );
        ios_teb_free_n = 18;                                            /* 18 frees so far: the ring has wrapped */
        { int k; for (k = 0; k < 16; k++) { struct ios_teb_free_rec *r = &ios_teb_free_ring[k];
            r->teb = 0x70000000 + k * 0x100000; r->ks = r->teb + 0x10000; r->ks_end = r->ks + 0x4000; r->st_lo = r->teb + 0x40000; r->st_hi = r->teb + 0x80000;
            r->tid = 0xe0 + 4 * k; r->by_tid = 0xd0; r->ms = ios_teb_free_now_ms() - 2000; } }
        { char what[400]; uintptr_t a[6] = { 0x70100000 + 0x10000 + 0x3944, 0x70000000 + 0x40000 + 0x10, 0x70000000 + 0x100, 0x70000000 + 0x1fff, 0x12345678, 0x9 }; int k;
          /* the ring has wrapped (18 frees, 16 slots): slot 1 holds the newest free (17 & 15), slot 0 the one before; the entry number printed is the position from the newest */
          for (k = 0; k < 6; k++) { int r = ios_teb_free_lookup( a[k], what, sizeof(what) ); printf( "[%d] %d %s\n", k, r, r ? what : "" ); } }
        return 0;
    }
    return 2;
}
'''
HARNESS = (HARNESS.replace("@PEEK@", peek_parse).replace("@NOEXEC@", noexec_alias).replace("@T32READ@", t32_read).replace("@T32ROWS@", t32_rows)
           .replace("@RINGDECL@", ring_decl).replace("@RINGLOOKUP@", ring_lookup))

with tempfile.TemporaryDirectory(prefix="wsig-") as d:
    d = Path(d)
    (d / "h.c").write_text(HARNESS)
    r = subprocess.run([cc, "-O0", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", f"-I{NT}", str(d / "h.c"), "-o", str(d / "h")], capture_output=True, text=True)
    check("the real headers and the extracted functions compile on the host against stand-ins", r.returncode == 0, r.stderr[-3500:])
    if r.returncode != 0:
        print("FAILED")
        sys.exit(1)

    def run(mode, **env):
        import os
        e = {k: v for k, v in os.environ.items() if k not in ("MADEIRA_X86_FAULT_DUMP",)}
        e.update(env)
        p = subprocess.run([str(d / "h"), mode], capture_output=True, text=True, env=e)
        return p.returncode, p.stdout, p.stderr

    # ---- G2 S17: the peek list parser
    rc, out, err = run("peek")
    lines = out.strip().split("\n")
    check("G2 S17: the MADEIRA_FAULT_PEEK list of the Modern Warfare 2 row parses (5 items, the first 0x1429b00:128, the last 0x1b4f300:16); a bare hex without 0x works; "
          "a length of 0, 257 or a bad item stops the list; a trailing comma is tolerated; at most 8 items",
          rc == 0 and lines[0] == "[0]=5 first=1429b00:128 last=1b4f300:16" and lines[1] == "[1]=1 first=1429b00:128 last=1429b00:128" and lines[2] == "[2]=2 first=1:1 last=2:256"
          and lines[3] == "[3]=0" and lines[4] == "[4]=0" and lines[5] == "[5]=0" and lines[6] == "[6]=1 first=1:2 last=1:2" and lines[7] == "[7]=1 first=1:2 last=1:2"
          and lines[8] == "[8]=0" and lines[9] == "[9]=8 first=1:1 last=8:8", out + err)

    # ---- G1 S10 / D S13: [spin-code]
    rc, out, err = run("spin")
    if "skip" in out:
        print("note: " + out.strip())
    else:
        check("G1 S10: [spin-code] names the spin (game.exe+0x1000), 32 bytes before and the bucket's bytes, and follows hop 0 through the import slot to the hook stub (winmm.asi+0x500) "
              "naming the import kernel32.dll!Sleep... ",
              "[spin-code] the thread spins at guest 0x00401000 (game.exe+0x1000), window 0x2000000000, 3 modules in the PEB32 list" in err
              and "[spin-code]   hop 0: slot 0x01002300 (winmm.asi+0x2300, import kernel32.dll!Sleep) holds 0x01000500 (winmm.asi+0x500)" in err, err[-2500:])
        check("...then hop 1 through the hook stub's own slot to kernel32.dll!GetTickCount's address, and hop 2 ends the chain (ret 4, no jump)",
              "[spin-code]   hop 1: slot 0x01002304 (winmm.asi+0x2304, import kernel32.dll!GetTickCount) holds 0x02000100 (kernel32.dll+0x100)" in err
              and "hop 2: no jmp/call [abs32] or push [abs32]; ret in these bytes: the chain ends" in err, err[-2500:])
        check("...the code rows show the bytes (ff 25 00 23 00 01 at the spin address), two shots a process, nothing for a third",
              "0x00401000 (the bucket, the spin is inside it): ff 25 00 23 00 01" in err and err.count("the thread spins at guest") == 2, err[-1500:])
        rc2, out2, err2 = run("spin64")
        rc3, out3, err3 = run("spinoff")
        check("a 64-bit thread (no WoW TEB) and the key unset print nothing", "[spin-code]" not in err2 and "[spin-code]" not in err3 and rc2 == 0 and rc3 == 0, err2 + err3)

        # ---- T1 S10 / G3 S10: page state, allocation head, re-read
        rc, out, err = run("page")
        check("T1 S10 / G3 S10: the page state of a register's object reads the host region (readable and writable, resident pages), and the allocation head 64 KB below is printed",
              re.search(r"page state of ESI=0x00300004: host region 0x[0-9a-f]+\+0x[0-9a-f]+ prot=rw- max=rw[x-] share_mode=\d+ pages_resident=\d+", err)
              and "allocation start 0x00300000 of ESI=0x00300004 (64 KB block, below the page)" in err or "allocation start 0x00300000 of ESI=0x00300004 (64 KB block, in the first page)" in err, err[-2500:])
        check("...a fault address in the window has its state too; the 50 ms re-read names a dword that changed (ESI+4: 0xdb82238 then 0) and calls the other object unchanged",
              "page state of the fault address=0x03ff0000" in err and "re-read after 50 ms of ESI=0x300000: CHANGED: +0x04 db82238 -> 0" in err
              and "re-read after 50 ms of EDI=0x310000: unchanged" in err, err[-2500:])

        # ---- G3 S4: [x64-fault]
        rc, out, err = run("x64")
        check("G3 S4: [x64-fault] names the bad access, the exact RIP with its module and RVA, and all 16 registers",
              re.search(r"\[x64-fault\] #1 a 64-bit guest faulted at address 0x8 at RIP 0x[0-9a-f]+ \(game\.exe\+0x1000\) \[exact RIP from FEX's block table\]; RAX=0x0 RCX=0x[0-9a-f]+ ", err)
              and "R15=0x0" in err, err[-2500:])
        check("...the registers that point into the module and the fault address minus each register (RAX = 0 gives 0x8)",
              "registers that point into a module: RCX=0x" in err and "game.exe+0x2000" in err and "fault address minus each register (mod 2^64): -RAX=0x8 " in err, err[-2500:])
        check("...the PE identity of the RIP's module: Machine 0x8664, 2 sections, TimeDateStamp, SizeOfImage, CheckSum, the section that holds the RIP marked",
              "image game.exe: base 0x" in err and "Machine 0x8664 NumberOfSections 2 TimeDateStamp 0x5f5e1000 SizeOfImage 0x100000 CheckSum 0xabcdef01" in err
              and "section .text    rva 0x00001000 vsize 0x00080000 chars 0x60000020 <- RIP" in err and "section .data    rva 0x00090000 vsize 0x00001000 chars 0xc0000040\n" in err, err[-3000:])
        check("...the object RBX points at (code pointers named module+RVA) and the second-level object found in it; the redelivery of the same fault prints nothing more",
              "[RBX=0x" in err and "(game.exe+0x1234)" in err and "second level: [RBX=0x" in err and "-> 0000000000001111 0000000000002222 0000000000003333 0000000000004444" in err
              and err.count("[x64-fault] #") == 1 and "end of report #1" in err, err[-3000:])
        rc2, out2, err2 = run("x64-wow")
        rc3, out3, err3 = run("x64-off")
        rc4, out4, err4 = run("x64-readable")
        check("a 32-bit process (WoW TEB), the key unset and a fault on a READABLE page (a protection fault, not a wild pointer) print nothing",
              "[x64-fault]" not in err2 and "[x64-fault]" not in err3 and "[x64-fault]" not in err4 and rc2 == 0 and rc3 == 0 and rc4 == 0, err2 + err3 + err4)

    # ---- G2 S10: alias views
    rc, out, err = run("alias")
    rc2, out2, err2 = run("alias-zero")
    check("G2 S10: with code in the RW and RX views and zeros in the guest view the line counts the non-zero bytes (0 / 16384 / 16384) and says the views DIFFER",
          "[noexec-who] alias: the RIP's 16 KB page is in a live anonymous JIT alias; guest view 0x" in err and "nonzero=0 hash=cbf29ce484222325" not in err.split("rw view")[1]
          and re.search(r"rw view 0x[0-9a-f]+ nonzero=16384 ", err) and re.search(r"rx view 0x[0-9a-f]+ nonzero=16384 ", err) and "the views DIFFER (an aliasing fault of BCD's, not the program's)" in err, err)
    check("...and with all three zero it says the program's JIT never wrote it", "nonzero=0 hash=" in err2 and "the views agree (what is zero is zero in all of them: the program's JIT never wrote it)" in err2, err2)

    # ---- G8 S4 / D S16: [term-code] rows
    rc, out, err = run("rows")
    check("G8 S4: [term-code] rows print 64 bytes at EIP (two rows), 192 before and 32 after a return address (7 rows) and mark the unreadable ones with ??",
          err.count("[term-code] at EIP ") == 2 and err.count("[term-code] ret ") == 7 + 2 and "[term-code] at EIP +0: 00 01 02 03" in err and "[term-code] ret -192: " in err
          and "[term-code] ret +0: ??" in err or "[term-code] ret +0: " in err, err[-2000:])
    check("...the rows' bytes are the memory's (guest 0x1000 reads 00 01 .. after the page offset), and a row that runs into the hole is ?? as a whole",
          "[term-code] at EIP +0: 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f 10 11 12 13 14 15 16 17 18 19 1a 1b 1c 1d 1e 1f" in err and "[term-code] ret +0: ??" in err, err[-2000:])

    # ---- A4 S6: the ring
    rc, out, err = run("ring")
    ls = out.strip().split("\n")
    check("A4 S6: an address in the kernel stack of the NEWEST freed thread (tid 00e4, entry 0), in the stack and the TEB block of the one before it (tid 00e0, entry 1) is named with the thread, "
          "the age and the freeing exit; the TEB block's last byte counts, an address in nothing does not (the ring has wrapped: 18 frees, 16 slots)",
          rc == 0 and ls[0].startswith("[0] 1 kernel stack (the pthread_t is at its top) of tid=00e4, freed 2000 ms ago by the exit of tid=00d0")
          and ls[1].startswith("[1] 1 stack of tid=00e0, freed 2000 ms ago") and ls[2].startswith("[2] 1 TEB block of tid=00e0") and ls[3].startswith("[3] 1 TEB block of tid=00e0")
          and ls[4].rstrip() == "[4] 0" and ls[5].rstrip() == "[5] 0", out + err)
    check("...the text names the ranges (TEB, kernel stack, stack) and the entry's place among the last 16",
          "(TEB 0x70100000, kernel stack 0x70110000..0x70114000, stack 0x70140000..0x70180000; entry 0 of the last 16)" in ls[0], out)

# ------------------------------------------------------------------------------------------------------------------ rules and fixtures
def expect(name):
    import json
    return json.loads((fixdir / (name + ".expect")).read_text())


for rid, fixtures, status in (("x64-null-fault-diag", ("crash-bandicoot-x64-null-fault-2026-11-08", "dark-deception-x64-null-fault-2026-11-05"), "trying"),
                              ("x86-indirect-jump-cycle-spin", ("acb-asi-spin-2026-11-06",), "known"),
                              ("protected-program-jump-into-instruction", ("yakuza-like-a-dragon-protected-jump-2026-11-05",), "known"),
                              ("iw4sp-thread-registry-null", ("call-of-duty-modern-warfare-2-thread-registry-2026-11-04",), "known"),
                              ("re5-protection-exit", ("re5-protection-exit-2026-11-07",), "known"),
                              ("control-coherent-ui-loop", ("control-coherent-ui-loop-2026-11-08",), "known")):
    present = ('"id": "%s"' % rid) in rules
    fx_ok = all((fixdir / (f + ".log")).exists() and (fixdir / (f + ".expect")).exists() and rid in expect(f)["matched"] and expect(f)["status"] == status for f in fixtures)
    check("auto-tune rule %s exists and its fixture(s) %s match it (status %s)" % (rid, ", ".join(fixtures), status), present and fx_ok)

print("PASS: the night round's log-only diagnostics, compiled from the real sources" if ok else "FAILED")
sys.exit(0 if ok else 1)
