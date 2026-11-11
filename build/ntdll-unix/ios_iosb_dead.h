/* An async I/O completion does not store an IO_STATUS_BLOCK32 into memory that is not there.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * Battle.net (build 594, the owner's iPhone18,2, log 19:22:01): the updated Agent (Agent.9824\Agent.exe) and AgentHelper.exe, both 32-bit
 * CONSOLE programs, faulted 8 and 10 times (18 faults in all) in the first seconds after they started:
 *
 *   [mach_exc] UNHANDLED #1 pc=0x10378a42c addr=0x10004 ... sym pc=Madeira`invoke_system_apc+0x57c
 *   backtrace: server_select <- server_wait <- server_wait_for_object <- NtWriteFile <- __wine_syscall_dispatcher
 *   [av-detail] ... p0=WRITE p1=0x10004      (x19 = 0x10000, w22 = 0x8e: STR W22, [X19, #4])
 *
 * It looked like a 32-bit guest's IO_STATUS_BLOCK written at its RAW guest address (0x10000) instead of B + 0x10000. It is not.
 * The same fault, the same R12 = 0x10000, is in four older logs of other games whose 32-bit launcher runs cmd.exe: Portal (build 579, two
 * uploads: R15 = 0x8d, the first beacon line of that build) and Batman: Arkham Asylum and LEGO Batman 2 (build 592, R15 = 0x8e).
 *   - 0x8e = 142 and 0x45 = 69 are the byte counts of the first two lines of rpmalloc's band-selector beacon ("[va-profile] ml787
 *     advertised-maxapp ...", "[va-profile] ml706 cand0 ...": 142 + 69 + 59 + 63 = the 333 bytes of "deferred selector log (333 bytes)").
 *     rpmalloc_initialize() runs at the top of libwow64fex.dll's BTCpuProcessInit and writes them with WriteFile( GetStdHandle( -12 ) ).
 *   - That WriteFile is FEX's own (FEX/Source/Windows/Common/WinAPI/IO.cpp, DLLEXPORT_FUNC): `IO_STATUS_BLOCK IOSB;` NOT initialised, then
 *     NtWriteFile( ..., &IOSB, ... ). It is 64-bit NATIVE code calling a native ntdll inside a 32-bit process's pseudo-process; no
 *     guest syscall, no wow64 thunk, and no guest has run yet (FEX is still initialising).
 *   - In a console program the standard error handle is a live console handle (the Agent got "conhost.exe --server 0x24 --headless"
 *     just before), so for the first time the write got past the handle check into
 *     server_write_file: the server queues the write for conhost, the client waits, and conhost's answer comes back as an
 *     APC_ASYNC_IO system APC for that wait. Processes whose standard handles are NULL (every Chromium helper) never got that far.
 *   - ntdll decides how an IO_STATUS_BLOCK is laid out from the CALLING PSEUDO-PROCESS (ios_in_wow64_call(): "the guest window base of
 *     the calling thread is not 0", file.c), not from the call: in a 32-bit process every call counts as the thunks' calls, whose
 *     IO_STATUS_BLOCK.Pointer is the address of the guest's IO_STATUS_BLOCK32 (wow64's iosb_32to64()). iosb_client_ptr() therefore took
 *     the uninitialised Pointer of FEX's IOSB (stale stack: 0x10000) as the address of a 32-bit status block, the server handed it back
 *     as the APC's `sb`, and set_async_iosb() stored Information (+4) and Status (+0) there.
 * So 0x10000 is not a guest address that lacks +B (+B would have been a write into the guest's own memory at a random place, which is
 * worse than the fault); it is garbage in a 64-bit status block that was never a cookie.
 *
 * Today the store faults, the redelivery guard counts five identical faults and skips the instruction ("SEGV LOOP"), and the program goes
 * on (the Agent listened on 127.0.0.1:60590 and Battle.net connected to it): a storm of Mach exceptions for nothing, and a write to
 * whatever the garbage happens to point at on the day it points at something mapped.
 *
 * WHAT IS DECIDED HERE
 * --------------------
 * Only one thing: whether the 8 bytes at `p` can be stored to at all. A completion in a 32-bit process leaves a store alone when
 *   - the pointer is NULL (set_async_iosb returns first),
 *   - or it lies in the process's own 4 GB guest window (the thunks' guest status blocks; whatever the pages there are doing, as before),
 *   - or it is below the iOS floor (the 4 GB __PAGEZERO) and the fault path would serve it (a sub-floor image window or a low-alias
 *     allocation: the exceptions that have a registry behind them),
 *   - or it is at or above the floor and every byte of the 8 is inside some mapping of the task (internal cookies such as ntdll's
 *     sync_ioctl() / socket send queues put an IO_STATUS_BLOCK32 on the host stack or heap: those stay as they are).
 * Anything else is a store into nothing. It faulted before; it is skipped now, with a "[iosb-dead]" line. A mapped page with the wrong
 * protection still counts as mapped: the fault path may heal it, and then the store happens exactly as before.
 *
 * 64-bit processes never get here (their calls are not in_wow64_call()). MADEIRA_IOSB_DEAD_TARGET=0 stores anyway, as before.
 *
 * NIGHT ROUND 2026-11-11 (agent G8 S1; Resident Evil 5 BF3644, build 630, an iPhone on a 7-day free profile: 63 GB address map): the same
 * garbage pointer, this time not 0x10000 but a RETURN ADDRESS that an earlier call of the same thread had left on the stack where FEX's
 * IOSB lives: 0x30a6f1354, inside kernelbase.dll's copy in the JIT pool (the call that returned there was the failed first band
 * allocation: "[nt-fail] NtAllocateVirtualMemoryEx status=c000000d ... low=0x7c00000000"). That address is above the floor and
 * mapped (prot r-x), so "mapped" was no reason to skip, the store was done through the pool's RW alias by the store emulator, and the
 * program then executed `00000043` (Status 0 at +0, Information 0x43 at +4 over two instruction words): "ill_handler ILL at pc=0x30a6f1358,
 * kernelbase.dll+0x51358" and the process ended. The same site is in the Portal logs CQEQJX, Q3SG8T, V7F39B and MX074P and in the earlier
 * Battle.net Agent, Batman: Arkham Asylum and LEGO Batman 2 ones. An IO_STATUS_BLOCK32 never lives in the executable code of a DLL
 * the pool copies: a store target inside it is a store into nothing as well. ios_iosb32_target_in_code asks the pool (the `.text`
 * of every image copy) after the cheap cases; the data sections of the copies, the host stack and the host heap stay as they are.
 *
 * The probes are passed in so tests/host/check-iosb-dead-target.py can compile this file on the host and run every case.
 */
#ifndef IOS_IOSB_DEAD_H
#define IOS_IOSB_DEAD_H

#include <stdlib.h>

#define IOS_IOSB32_SIZE      8ull                /* sizeof(IO_STATUS_BLOCK32): Status, Information */
#define IOS_IOSB_FLOOR       0x100000000ull      /* iOS's __PAGEZERO: nothing is mapped below it (IOS_SUBFLOOR_FLOOR) */
#define IOS_IOSB_WINDOW_SIZE 0x100000000ull      /* a guest window is 4 GB */

typedef int (*ios_iosb_probe_fn)( unsigned long long addr, unsigned long long len );

static inline int ios_iosb_dead_enabled( void )
{
    const char *e = getenv( "MADEIRA_IOSB_DEAD_TARGET" );

    return !(e && e[0] == '0' && !e[1]);
}

/* 1: the 8-byte store at p cannot land and may be skipped. 0: leave it as it was.
 *   guest_base   B of the calling pseudo-process (0 when it has none)
 *   mapped       1 when [addr, addr + len) is inside the task's mappings, whatever their protection (0 only for a hole)
 *   served_low   1 when the fault path serves [addr, addr + len) below the floor (sub-floor image window, low alias) */
static inline int ios_iosb32_target_dead( unsigned long long p, unsigned long long guest_base,
                                          ios_iosb_probe_fn mapped, ios_iosb_probe_fn served_low )
{
    if (!p) return 0;
    if (p + IOS_IOSB32_SIZE < p) return 1;                                      /* wraps the address space */
    if (guest_base && p >= guest_base && p - guest_base < IOS_IOSB_WINDOW_SIZE) return 0;
    if (p < IOS_IOSB_FLOOR) return !served_low( p, IOS_IOSB32_SIZE );
    return !mapped( p, IOS_IOSB32_SIZE );
}

/* 1: the 8-byte store at p would land in the executable code of an image the JIT pool copied (night round 2026-11-11, see above) and must be
 * skipped as well. Only a pointer at or above the floor and outside the process's own guest window is asked about.
 *   exec_pool   1 when [addr, addr + len) touches the `.text` of a JIT pool image copy */
static inline int ios_iosb32_target_in_code( unsigned long long p, unsigned long long guest_base, ios_iosb_probe_fn exec_pool )
{
    if (!p || p < IOS_IOSB_FLOOR) return 0;
    if (p + IOS_IOSB32_SIZE < p) return 0;
    if (guest_base && p >= guest_base && p - guest_base < IOS_IOSB_WINDOW_SIZE) return 0;
    return exec_pool( p, IOS_IOSB32_SIZE ) != 0;
}

#endif
