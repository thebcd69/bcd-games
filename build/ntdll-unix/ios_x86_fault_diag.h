/* [x86-fault]: an OPT-IN report for a 32-bit guest's unhandled access violation or int3, with the evidence that names the code.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * Two Need for Speed games of one engine (non-Steam copies, 32-bit) stop at a fixed place of their own program, and the logs cannot say why:
 *
 *   Need for Speed: Most Wanted, speed.exe (builds 574, 580 and 592, five testers' logs XM2BC1, GEVT5A, NEPBNC, NC4V56, 5C54B7, GTKMGA):
 *     "wine: Unhandled page fault on read access to B514AB6A at address 0081AB7A", the same address in every run. The guest registers at
 *     the fault (x32 mapping, [fault-full]) are EAX = 0x0A6A0110, EDI = 0x0A6A00C0, ECX = 0xB514AB6A, and ECX - EDI = 0xAAAAAAAA in every
 *     log (0x099F00C0 + 0xAAAAAAAA = 0xB449AB6A on another phone, 0x09C700C0 + 0xAAAAAAAA = 0xB471AB6A on a third): the load is
 *     `mov r32,[ecx]` through a pointer the program built from a heap object and a 0xAAAAAAAA fill pattern, i.e. from a field that was never
 *     initialised. The code at 0x81AB7A is not readable by the existing report ([rsp-trunc] reads a guest address as a host address, which
 *     is only right for a 64-bit process).
 *   Need for Speed: Underground 2, SPEED2.EXE (builds 580 and 592, five logs): an int3 at 0x4405A9 after the Enter key of the menu, with the
 *     [stp-split] read-back of build 591 proving the 580 split store is right ("source/guest/alias read equal"), so the int3 is the game's.
 *
 * Both need the same thing the Battle.net renderers needed (ios_int3_x86_diag.h, build 597): the code around the address, the stack, the
 * modules, the objects the registers point at. That report only runs for an int3 and only when env.MADEIRA_INT3_X86_DUMP = 1 (Battle.net's
 * launcher entry, whose checks forbid any other user); this one is a separate switch for the games' own lists, and it also runs for an
 * unhandled access violation.
 *
 * WHAT IT PRINTS (only for a 32-bit guest, at most 3 reports per session, every line starts with "[x86-fault]" except the PE identity rows
 * that the helper of ios_int3_x86_diag.h prints with its "[int3-x86]" prefix)
 * ----------------------------------------------------------------------------------------------
 *  1. the headline: what happened, the faulting address (an access violation: the guest address read or written), the address of the block
 *     (the guest rip of the translated block that faulted: the instruction is within its first bytes) or of the int3, with module+RVA, and
 *     the eight registers; then the registers that point into a module. For an access violation the faulting instruction's EIP is the
 *     exact one from FEX's block table (round 624, A-S12: the block address alone was FEX's State.rip, only the last block the dispatcher
 *     entered; a note line says when it could not be resolved); with no EBP chain the code before up to 8 call-preceded return addresses of
 *     the program image on the stack ("stk#N") is printed;
 *  2. for an access violation, the fault address minus each register (a 0xAAAAAAAA there names a pattern, a small delta a stale pointer);
 *  3. the thread facts: TEB32 and PEB32, the stack range and depth, LastErrorValue and LastStatusValue, the TLS bitmap; the modules;
 *  4. the PE identity of the module the address is in: TimeDateStamp, SizeOfImage, CheckSum, the sections (identifies the exact build of the
 *     program, whatever the copy);
 *  5. 96 dwords of stack from ESP, and the ones that point into a module; the EBP chain with module+RVA;
 *  6. the code: 256 bytes before the block's address and 160 after it, and 64 bytes before every return address of the chain;
 *  7. 96 bytes at EAX, ECX, EDX, EBX, ESI, EDI and EBP when they point into the stack or heap, as dwords with module+RVA (the objects the
 *     failing function was handed).
 *  Round 624 (agents E1 S17 and D2 S8; Unreal Gold EQ6DMS/X3HYP5, Need for Speed: Underground 2 B1MR64), for an access violation:
 *  8. the Wine views around the fault address and around EBP, ESI, EDI, EBX, ECX and EDX when they point into the window (at most 6 places,
 *     "[fault-rgn]" lines: the view, the views below and above, their history), from ios_dump_fault_region_report (virtual_ios.c), which only
 *     tries the memory lock;
 *  9. the WoW64 allocations and frees before the fault (virtual_ios.c keeps the last 64 while the switch is on): whether the address was ever
 *     allocated, freed, or only computed;
 * 10. the places in the program's image that hold the fault address as a dword (at most 16): the structure the pointer came from.
 *  A write to a READABLE page (the write protection of translated code, a guard page; FEX and the guest handle both) takes no report of the
 *  three: the report is for the wild pointers a program follows, whose pages are not readable. Besides the [rsp-trunc] block of the Mach
 *  exception thread (the first 12 events of a process) the report is also reached from the signal path's "BUS->AV: unreadable target"
 *  branch, where a game's real wall can come thousands of events later (EQ6DMS: about event 17,400).
 *
 * WHAT IT NEVER DOES
 * ------------------
 * It reads the guest's memory through ios_av_args_read (a fault-safe Mach read; a page that is not there prints "??" or is left out) and
 * writes only to stderr. It stops nothing, writes no memory, changes no protection and does not touch the thread's state. It is off unless
 * env.MADEIRA_X86_FAULT_DUMP = 1 (a game's recommended list, nothing else sets it); with the switch unset the fault paths do exactly what they
 * did before (one call to ios_x86_fault_enabled(), which reads the environment once).
 *
 * The 32-bit register mapping is FEX x32::SRA (EAX x4, ECX x7, EDX x5, EBX x6, ESP x8, EBP x9, ESI x10, EDI x11, window base x19); the
 * access-violation call hands over the host registers in the order RAX RCX RDX RBX RSP RBP RSI RDI of FEX's register file, the int3 call the
 * guest register file itself (the same order).
 *
 * Uses ios_av_args_read (ios_av_args_diag.h), ios_wn32_modules / struct ios_wn32_mod (ios_wow_null_diag.h) and the helpers of
 * ios_int3_x86_diag.h (ios_i3x_read, ios_i3x_where, ios_i3x_hex, ios_i3x_image), so it is included after those.
 */
#ifndef IOS_X86_FAULT_DIAG_H
#define IOS_X86_FAULT_DIAG_H

#define IOS_X86F_MAX_REPORTS  3
#define IOS_X86F_STACK_DWORDS 96
#define IOS_X86F_CODE_BEFORE  256
#define IOS_X86F_CODE_AFTER   160
#define IOS_X86F_OBJ_BYTES    96
#define IOS_X86F_FRAMES       24
#define IOS_X86F_SITES        12
#define IOS_X86F_STACK_SITES  8

/* env.MADEIRA_X86_FAULT_DUMP = 1 turns the report on; anything else (or nothing) leaves it off. Read once. */
static int ios_x86_fault_enabled( void )
{
    static int enabled = -1;

    if (enabled < 0)
    {
        const char *e = getenv( "MADEIRA_X86_FAULT_DUMP" );

        enabled = e && e[0] == '1' && !e[1];
    }
    return enabled;
}

/* code bytes [at - before, at + after) of the guest, 32 per row (ios_i3x_code of the earlier report is sized for 288 bytes; this one for 416) */
static void ios_x86f_code( uint64_t win, const char *what, uint32_t at, unsigned before, unsigned after, const struct ios_wn32_mod *mods, unsigned nmods )
{
    uint8_t buf[IOS_X86F_CODE_BEFORE + IOS_X86F_CODE_AFTER], valid[IOS_X86F_CODE_BEFORE + IOS_X86F_CODE_AFTER];
    unsigned len = before + after, row, got;
    uint32_t from = at - before;
    char hex[32 * 3 + 4], w[96];

    if (len > sizeof(buf) || at < before) return;
    got = ios_i3x_read( win + from, buf, valid, len );
    ios_i3x_where( mods, nmods, at, w, sizeof(w) );
    dprintf( STDERR_FILENO, "[x86-fault]   code %s: %u bytes before and %u after guest 0x%08x%s%s%s (%u of %u readable); rows are 32 bytes, "
             "the first at 0x%08x\n", what, before, after, at, w[0] ? " (" : "", w, w[0] ? ")" : "", got, len, from );
    for (row = 0; row < len; row += 32)
    {
        unsigned n = len - row < 32 ? len - row : 32;

        ios_i3x_hex( hex, sizeof(hex), buf + row, valid + row, n );
        dprintf( STDERR_FILENO, "[x86-fault]   code %s %+d: %s\n", what, (int)(row - before), hex );
    }
}

/* IOS_X86F_OBJ_BYTES bytes at a register that points into the stack or heap, as dwords with module+RVA */
static void ios_x86f_object( uint64_t win, const char *reg, uint32_t v, const struct ios_wn32_mod *mods, unsigned nmods )
{
    uint8_t buf[IOS_X86F_OBJ_BYTES], valid[IOS_X86F_OBJ_BYTES];
    char line[600], w[96];
    unsigned i, row;

    if (v < 0x10000 || v > 0xfffe0000u) return;
    ios_i3x_where( mods, nmods, v, w, sizeof(w) );
    if (w[0]) return;
    if (!ios_i3x_read( win + v, buf, valid, sizeof(buf) )) return;
    for (row = 0; row < IOS_X86F_OBJ_BYTES / 32; row++)
    {
        size_t l = 0;

        line[0] = 0;
        for (i = 0; i < 8 && l + 60 < sizeof(line); i++)
        {
            uint32_t d;
            unsigned at = row * 32 + i * 4;

            if (!valid[at]) { l += (size_t)snprintf( line + l, sizeof(line) - l, " ??" ); continue; }
            memcpy( &d, buf + at, 4 );
            ios_i3x_where( mods, nmods, d, w, sizeof(w) );
            l += (size_t)snprintf( line + l, sizeof(line) - l, " %x%s%s%s", d, w[0] ? "(" : "", w, w[0] ? ")" : "" );
        }
        dprintf( STDERR_FILENO, "[x86-fault]   [%s=0x%x]+0x%02x:%s\n", reg, v, row * 32, line );
    }
}

/* round 624 (E1 S17 a2): the Wine views around a guest address of the window (virtual_ios.c; the memory lock is only tried there). */
static void ios_x86f_views( uint64_t win, const char *what, uint32_t v )
{
    extern int ios_dump_fault_region_report( void *addr );

    dprintf( STDERR_FILENO, "[x86-fault]   views around %s=0x%08x:\n", what, v );
    if (!ios_dump_fault_region_report( (void *)(uintptr_t)(win + v) ))
        dprintf( STDERR_FILENO, "[x86-fault]     the memory lock was busy: not read\n" );
}

/* round 624 (D2 S8 d3): the places in the program's image (the first module of the PEB32 list) that hold `value` as an aligned dword, at most
 * 16, read 2 KB at a time through the fault-safe reader (what is not there is skipped). */
static void ios_x86f_scan_image( uint64_t win, const struct ios_wn32_mod *mods, unsigned nmods, uint32_t value )
{
    uint32_t page[0x800 / 4], at, end, hits = 0;   /* 2 KB a read: the report also runs on the 50 KB signal stack */
    char line[700];
    size_t l = 0;
    unsigned k, pages = 0;

    if (!nmods || value < 0x10000 || mods[0].size < 0x1000 || mods[0].size > 0x4000000u) return;   /* an image of 64 MB at most */
    at = mods[0].base & ~0x7ffu;
    end = mods[0].base + mods[0].size;
    line[0] = 0;
    for (; at < end && hits < 16; at += 0x800)
    {
        if (!ios_av_args_read( win + at, page, sizeof(page) )) continue;
        pages++;
        for (k = 0; k < sizeof(page) / 4 && hits < 16; k++)
        {
            if (page[k] != value || at + k * 4 < mods[0].base || at + k * 4 >= end) continue;
            hits++;
            if (l + 40 < sizeof(line)) l += (size_t)snprintf( line + l, sizeof(line) - l, " %s+0x%x", mods[0].name, at + k * 4 - mods[0].base );
        }
    }
    dprintf( STDERR_FILENO, "[x86-fault]   the image %s holds the fault address 0x%08x as a dword at %u place(s)%s:%s (%u of its 2 KB blocks read)\n",
             mods[0].name, value, hits, hits >= 16 ? " (the first 16)" : "", hits ? line : " none", pages );
}

/* 1 when the 8 bytes before `ret` end in a call instruction (E8 rel32, or FF /2: call r/m32), i.e. `ret` looks like a return address */
static int ios_x86f_call_before( uint64_t win, uint32_t ret )
{
    uint8_t b[8];
    unsigned len;

    if (ret < 0x10008 || !ios_av_args_read( win + ret - 8, b, 8 )) return 0;
    if (b[3] == 0xe8) return 1;
    for (len = 2; len <= 7; len++)
        if (b[8 - len] == 0xff && ((b[9 - len] >> 3) & 7) == 2) return 1;
    return 0;
}

/* The report. gregs: the 8 registers in the order RAX RCX RDX RBX RSP RBP RSI RDI (32-bit values in the low halves); eip: the guest address
 * of the int3 or of the block that faulted; fault_addr: the guest address of the access (-1 for an int3); write: 1 for a store, 0 for a
 * load, -1 when not known. */
static void ios_x86_fault_report_ex( const arm_thread_state64_t *st, uintptr_t thread_teb, const uint64_t *gregs, uint64_t eip64,
                                     int64_t fault_addr, int write, int resolve )
{
    extern uint64_t ios_native_rip_from_hostpc( uint64_t, uint64_t, const char ** );
    static volatile int reports;
    uint64_t state_rip = 0;
    const char *why = "";
    uint8_t sv[IOS_X86F_STACK_DWORDS * 4] = { 0 };
    unsigned got = 0;
    int from_stack = 0;
    uint64_t base = st->__x[19], teb32_host = 0;
    uint32_t eax, ecx, edx, ebx, esp, ebp, esi, edi, eip, teb32, peb32 = 0, stack[IOS_X86F_STACK_DWORDS], i;
    int32_t woff = 0;
    struct ios_wn32_mod mods[96];
    unsigned nmods = 0, k, nrefs = 0, nsite = 0;
    char w[96], line[1000];
    int num, in_mod = -1;
    uint32_t sites[IOS_X86F_SITES];

    if (!gregs || eip64 < 0x10000 || eip64 > 0xffffffffULL) return;                           /* a 64-bit guest has its own reports */
    if (!((base & 0xffffffffULL) == 0 && base >= 0x100000000ULL)) return;                     /* not a 4 GB window base in x19 */
    /* x18 is the thread's TEB in JIT code; the registry lookup the caller did can fall back to slot 0, which is another thread's, so x18 wins
     * when it lies in this window */
    if (st->__x[18] >= base && st->__x[18] - base < 0x100000000ULL) thread_teb = (uintptr_t)st->__x[18];
    if (!thread_teb) return;
    if (fault_addr >= 0)
    {
        uint8_t probe;

        /* round 624 (E1 S17 a1): a readable page is the write protection of translated code or a guard page: not one of the three */
        if (ios_av_args_read( base + (uint64_t)fault_addr, &probe, 1 )) return;
    }
    if ((num = __sync_add_and_fetch( &reports, 1 )) > IOS_X86F_MAX_REPORTS) return;

    if (resolve && fault_addr >= 0)
    {
        uint64_t bb = 0, exact = 0;

        if (st->__x[28] >= 0x10000 && ios_av_args_read( st->__x[28], &bb, 8 ) && bb >= 0x10000)
            exact = ios_native_rip_from_hostpc( bb, __darwin_arm_thread_state64_get_pc( *st ), &why );
        else why = "x28 not readable";
        if (exact >= 0x10000 && exact <= 0xffffffffULL) { state_rip = eip64; eip64 = exact; }
    }

    eax = (uint32_t)gregs[0]; ecx = (uint32_t)gregs[1]; edx = (uint32_t)gregs[2]; ebx = (uint32_t)gregs[3];
    esp = (uint32_t)gregs[4]; ebp = (uint32_t)gregs[5]; esi = (uint32_t)gregs[6]; edi = (uint32_t)gregs[7];
    eip = (uint32_t)eip64;

    if (ios_av_args_read( thread_teb + IOS_WN32_TEB_WOWOFF, &woff, 4 ) && woff > 0) teb32_host = thread_teb + (uint64_t)woff;
    teb32 = teb32_host >= base && teb32_host - base < 0x100000000ULL ? (uint32_t)(teb32_host - base) : 0;
    if (teb32)
    {
        uint32_t self = 0;

        if (!ios_av_args_read( teb32_host + IOS_WN32_TEB_SELF, &self, 4 ) || self != teb32) teb32 = 0;
    }
    if (teb32 && ios_av_args_read( teb32_host + IOS_WN32_TEB_PEB, &peb32, 4 ) && peb32 >= 0x10000)
        nmods = ios_wn32_modules( base, peb32, mods, 96 );
    ios_i3x_where( mods, nmods, eip, w, sizeof(w) );

    if (fault_addr >= 0 && state_rip)
        dprintf( STDERR_FILENO,
                 "[x86-fault] #%d a 32-bit guest faulted %s guest 0x%08x at EIP 0x%08x (%s) [exact: the block table of FEX for host pc 0x%llx; State.rip=0x%08x is only "
                 "the last block entry], window 0x%llx, %u modules in the PEB32 list; EAX=0x%x ECX=0x%x EDX=0x%x EBX=0x%x ESI=0x%x EDI=0x%x EBP=0x%x ESP=0x%x\n",
                 num, write < 0 ? "at" : (write ? "writing" : "reading"), (uint32_t)fault_addr, eip, w[0] ? w : "no module",
                 (unsigned long long)__darwin_arm_thread_state64_get_pc( *st ), (uint32_t)state_rip, (unsigned long long)base, nmods,
                 eax, ecx, edx, ebx, esi, edi, ebp, esp );
    else if (fault_addr >= 0)
        dprintf( STDERR_FILENO,
                 "[x86-fault] #%d a 32-bit guest faulted %s guest 0x%08x in the block that starts at guest 0x%08x (%s), window 0x%llx, %u modules in "
                 "the PEB32 list; EAX=0x%x ECX=0x%x EDX=0x%x EBX=0x%x ESI=0x%x EDI=0x%x EBP=0x%x ESP=0x%x\n",
                 num, write < 0 ? "at" : (write ? "writing" : "reading"), (uint32_t)fault_addr, eip, w[0] ? w : "no module",
                 (unsigned long long)base, nmods, eax, ecx, edx, ebx, esi, edi, ebp, esp );
    else
        dprintf( STDERR_FILENO,
                 "[x86-fault] #%d a 32-bit guest executed int3 at guest 0x%08x (%s), window 0x%llx, %u modules in the PEB32 list; "
                 "EAX=0x%x ECX=0x%x EDX=0x%x EBX=0x%x ESI=0x%x EDI=0x%x EBP=0x%x ESP=0x%x\n",
                 num, eip, w[0] ? w : "no module", (unsigned long long)base, nmods, eax, ecx, edx, ebx, esi, edi, ebp, esp );

    if (resolve && fault_addr >= 0 && !state_rip)
        dprintf( STDERR_FILENO, "[x86-fault]   note: the block above is FEX's State.rip (0x%08x), only the entry of the last block the dispatcher entered; "
                 "the exact EIP could not be resolved (%s); FEX's own \"D <tid> pc: .. eip: ..\" line below names it\n", (uint32_t)eip64, why );

    /* registers that point into a module, and (for an access) the fault address minus each register */
    {
        const struct { const char *name; uint32_t v; } regs[] =
            { { "EAX", eax }, { "ECX", ecx }, { "EDX", edx }, { "EBX", ebx }, { "ESI", esi }, { "EDI", edi }, { "EBP", ebp }, { "ESP", esp } };
        size_t l = 0;

        line[0] = 0;
        for (k = 0; k < sizeof(regs) / sizeof(regs[0]); k++)
        {
            ios_i3x_where( mods, nmods, regs[k].v, w, sizeof(w) );
            if (w[0] && l + 100 < sizeof(line)) l += (size_t)snprintf( line + l, sizeof(line) - l, " %s=0x%x(%s)", regs[k].name, regs[k].v, w );
        }
        dprintf( STDERR_FILENO, "[x86-fault]   registers that point into a module:%s\n", l ? line : " none" );
        if (fault_addr >= 0)
        {
            l = 0;
            line[0] = 0;
            for (k = 0; k < sizeof(regs) / sizeof(regs[0]); k++)
                if (l + 40 < sizeof(line))
                    l += (size_t)snprintf( line + l, sizeof(line) - l, " -%s=0x%x", regs[k].name, (uint32_t)fault_addr - regs[k].v );
            dprintf( STDERR_FILENO, "[x86-fault]   fault address minus each register (mod 2^32):%s\n", line );
        }
    }

    /* the thread's TEB32 and PEB32: stack range and depth, last error and status, the TLS bitmap */
    if (teb32)
    {
        uint32_t sbase = 0, slimit = 0, dealloc = 0, lerr = 0, lstat = 0, tlsexp = 0, bits[2] = { 0, 0 };
        int have_bits = peb32 >= 0x10000 && ios_av_args_read( base + peb32 + IOS_WN32_PEB_TLSBITS, bits, 8 );

        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_STACKBASE, &sbase, 4 );
        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_STACKLIMIT, &slimit, 4 );
        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_DEALLOC, &dealloc, 4 );
        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_LASTERROR, &lerr, 4 );
        (void)ios_av_args_read( teb32_host + IOS_I3X_TEB_LASTSTATUS, &lstat, 4 );
        (void)ios_av_args_read( teb32_host + IOS_WN32_TEB_TLSEXP, &tlsexp, 4 );
        dprintf( STDERR_FILENO,
                 "[x86-fault]   thread TEB32=0x%x PEB32=0x%x StackBase=0x%x StackLimit=0x%x DeallocationStack=0x%x (ESP is 0x%x below the base) "
                 "LastErrorValue=0x%x LastStatusValue=0x%08x TlsBitmapBits=%s0x%08x%08x (%d of 64 taken) TlsExpansionSlots=0x%x\n",
                 teb32, peb32, sbase, slimit, dealloc, sbase > esp ? sbase - esp : 0, lerr, lstat,
                 have_bits ? "" : "(unreadable) ", bits[1], bits[0], __builtin_popcount( bits[0] ) + __builtin_popcount( bits[1] ), tlsexp );
    }

    /* the modules */
    {
        size_t l = 0;

        line[0] = 0;
        for (k = 0; k < nmods; k++)
        {
            l += (size_t)snprintf( line + l, sizeof(line) - l, " %s=0x%x/0x%x", mods[k].name, mods[k].base, mods[k].size );
            if (l + 70 >= sizeof(line) || (k % 6) == 5 || k + 1 == nmods)
            {
                dprintf( STDERR_FILENO, "[x86-fault]   modules (name=base/size):%s\n", line );
                l = 0; line[0] = 0;
            }
            if (eip >= mods[k].base && eip - mods[k].base < mods[k].size) in_mod = (int)k;
        }
    }

    /* the PE identity of the module the address is in (the program's exact build) */
    if (in_mod >= 0) ios_i3x_image( base, &mods[in_mod] );

    /* the stack from ESP: raw rows, then the dwords that point into a module */
    {
        got = esp >= 0x10000 ? ios_i3x_read( base + esp, (uint8_t *)stack, sv, sizeof(sv) ) : 0;

        if (!got) dprintf( STDERR_FILENO, "[x86-fault]   guest stack at ESP=0x%x is not readable\n", esp );
        else
        {
            for (i = 0; i < IOS_X86F_STACK_DWORDS; i += 8)
            {
                size_t l = 0;

                line[0] = 0;
                for (k = 0; k < 8; k++)
                    l += (size_t)(sv[(i + k) * 4] ? snprintf( line + l, sizeof(line) - l, " %08x", stack[i + k] ) : snprintf( line + l, sizeof(line) - l, " ????????" ));
                dprintf( STDERR_FILENO, "[x86-fault]   stack ESP+0x%03x:%s\n", (unsigned)(i * 4), line );
            }
            {
                size_t l = 0;

                line[0] = 0;
                for (i = 0; i < IOS_X86F_STACK_DWORDS; i++)
                {
                    if (!sv[i * 4]) continue;
                    ios_i3x_where( mods, nmods, stack[i], w, sizeof(w) );
                    if (!w[0]) continue;
                    nrefs++;
                    if (l + 140 < sizeof(line)) l += (size_t)snprintf( line + l, sizeof(line) - l, " +0x%03x=%08x(%s)", (unsigned)(i * 4), stack[i], w );
                    if (l + 140 >= sizeof(line))
                    {
                        dprintf( STDERR_FILENO, "[x86-fault]   stack words that point into a module:%s\n", line );
                        l = 0; line[0] = 0;
                    }
                }
                dprintf( STDERR_FILENO, "[x86-fault]   stack words that point into a module (%u):%s\n", nrefs, l ? line : (nrefs ? " (listed above)" : " none") );
            }
        }
    }

    /* the EBP chain */
    {
        uint32_t fp = ebp;

        for (k = 0; k < IOS_X86F_FRAMES; k++)
        {
            uint32_t pair[2] = { 0, 0 };

            if (fp < 0x10000 || (fp & 3) || !ios_av_args_read( base + fp, pair, 8 )) break;
            ios_i3x_where( mods, nmods, pair[1], w, sizeof(w) );
            dprintf( STDERR_FILENO, "[x86-fault]   frame #%u ebp=0x%x saved-ebp=0x%x return=0x%08x%s%s%s\n", k, fp, pair[0], pair[1],
                     w[0] ? " (" : "", w, w[0] ? ")" : "" );
            if (w[0] && nsite < IOS_X86F_SITES) sites[nsite++] = pair[1];
            if (pair[0] <= fp || pair[0] - fp > 0x100000) break;
            fp = pair[0];
        }
    }

    /* the code around the address and before the first return addresses (rows of 32 bytes, "[x86-fault]   code <what> <offset>: ...") */
    if (!nsite && nmods && got)
    {
        for (i = 0; i < IOS_X86F_STACK_DWORDS && nsite < IOS_X86F_STACK_SITES; i++)
            if (sv[i * 4] && stack[i] >= mods[0].base && stack[i] - mods[0].base < mods[0].size && ios_x86f_call_before( base, stack[i] ))
                sites[nsite++] = stack[i];
        from_stack = nsite != 0;
    }
    ios_x86f_code( base, fault_addr >= 0 ? (state_rip ? "eip" : "block") : "int3", eip, IOS_X86F_CODE_BEFORE, IOS_X86F_CODE_AFTER, mods, nmods );
    for (k = 0; k < nsite; k++)
    {
        char tag[24];

        snprintf( tag, sizeof(tag), "%s#%u", from_stack ? "stk" : "ret", k );
        ios_x86f_code( base, tag, sites[k], 64, 8, mods, nmods );
    }

    /* what the registers point at */
    ios_x86f_object( base, "EAX", eax, mods, nmods );
    ios_x86f_object( base, "ECX", ecx, mods, nmods );
    ios_x86f_object( base, "EDX", edx, mods, nmods );
    ios_x86f_object( base, "EBX", ebx, mods, nmods );
    ios_x86f_object( base, "ESI", esi, mods, nmods );
    ios_x86f_object( base, "EDI", edi, mods, nmods );
    ios_x86f_object( base, "EBP", ebp, mods, nmods );

    /* round 624 (E1 S17 a2, D2 S8 d2 and d3): for an access violation, the views around the fault and the pointers, the allocations before it
     * and the places of the image that hold the address */
    if (fault_addr >= 0)
    {
        extern void ios_x86f_alloc_ring_dump( uint32_t fault );
        const struct { const char *name; uint32_t v; } at[] =
            { { "fault", (uint32_t)fault_addr }, { "EBP", ebp }, { "ESI", esi }, { "EDI", edi }, { "EBX", ebx }, { "ECX", ecx }, { "EDX", edx } };
        uint32_t seen[6];
        unsigned nseen = 0, j;

        for (k = 0; k < sizeof(at) / sizeof(at[0]) && nseen < 6; k++)
        {
            uint32_t pg = at[k].v & ~0x3fffu;

            if (at[k].v < 0x10000 || at[k].v > 0xfffe0000u) continue;
            for (j = 0; j < nseen && seen[j] != pg; j++) ;
            if (j < nseen) continue;            /* one place a page */
            seen[nseen++] = pg;
            ios_x86f_views( base, at[k].name, at[k].v );
        }
        ios_x86f_alloc_ring_dump( (uint32_t)fault_addr );
        ios_x86f_scan_image( base, mods, nmods, (uint32_t)fault_addr );
    }
    dprintf( STDERR_FILENO, "[x86-fault]   end of report #%d\n", num );
}

static void ios_x86_fault_report( const arm_thread_state64_t *st, uintptr_t thread_teb, const uint64_t *gregs, uint64_t eip64,
                                  int64_t fault_addr, int write )
{
    ios_x86_fault_report_ex( st, thread_teb, gregs, eip64, fault_addr, write, 0 );
}

/* The int3 call: gregs is FEX's guest register file (RAX RCX RDX RBX RSP RBP RSI RDI ...); grip the address after the int3. */
static void ios_x86_fault_int3( const arm_thread_state64_t *st, uintptr_t thread_teb, const uint64_t *gregs, uint64_t grip )
{
    if (grip < 2) return;
    ios_x86_fault_report( st, thread_teb, gregs, grip - 1, -1, -1 );
}

/* The access-violation call: the faulting thread's host state (x4 x7 x5 x6 x8 x9 x10 x11 are EAX ECX EDX EBX ESP EBP ESI EDI of a 32-bit
 * guest in JIT code), the guest rip of the translated block (State.rip) and the host address of the access (the window base plus the guest
 * address). */
/* round 624: never inlined, so the report's frame (about 8 KB) exists only while it runs: bus_handler calls it on the 50 KB signal stack, and
 * its own frame stays what it was (1184 bytes) for every other BUS signal. */
static __attribute__((noinline)) void ios_x86_fault_av( const arm_thread_state64_t *st, uintptr_t thread_teb, uint64_t block_rip,
                                                        uint64_t host_addr, int write )
{
    static volatile uint64_t last_rip, last_addr;
    uint64_t base = st->__x[19], g[8];
    int64_t guest_addr;

    if (!((base & 0xffffffffULL) == 0 && base >= 0x100000000ULL)) return;
    if (host_addr < base || host_addr - base >= 0x100000000ULL) return;                       /* not an access inside this window */
    if (block_rip == last_rip && host_addr == last_addr) return;                              /* the redelivery of the fault just reported */
    last_rip = block_rip; last_addr = host_addr;
    guest_addr = (int64_t)(host_addr - base);
    g[0] = st->__x[4]; g[1] = st->__x[7]; g[2] = st->__x[5]; g[3] = st->__x[6];
    g[4] = st->__x[8]; g[5] = st->__x[9]; g[6] = st->__x[10]; g[7] = st->__x[11];
    ios_x86_fault_report_ex( st, thread_teb, g, block_rip, guest_addr, write, 1 );
}

#endif
