/* ajan7-arkham (2026-10-21): TlsFree reaches into OTHER pseudo-processes' threads. A log line for it, and an opt-in switch.
 *
 * Wine's TlsFree(index) ends in NtSetInformationThread(ThreadZeroTlsCell), whose unix side (virtual_clear_tls_index) clears
 * cell `index` in the TEB of EVERY thread of the process. In Wine a Windows process is a Unix process and teb_list holds only
 * its threads. Here every pseudo-process of a session is a set of threads of one Mach task and teb_list holds them ALL (the
 * Steam client, the Dock host, the game, a launcher the game started). So a TlsFree(i) in one process zeroes TlsSlots[i] of
 * every thread of every other 32-bit process (the WOW_TEB cell) and of every 64-bit one (the TEB cell), including a cell the
 * other process is using right now. Two processes' low TLS indices are the same small numbers, so the collision is not rare
 * in principle; whether it happens depends on when the other process frees one.
 *
 * Why it is written down: BATMAN: ARKHAM CITY GOTY (Steam 200260, build 578, C7DGSN) starts BmLauncher.exe (a .NET Framework
 * launcher, run by Wine Mono) as a child of BatmanAC.exe, and the child dies in libmono, EIP libmono+0x2135C9, on
 * `mov dword [ebx+0x10c], 1` with EBX = 0: Mono's inlined TlsGetValue (mono_native_tls_get_value: TEB->TlsSlots[key]) returned
 * NULL for the key of its SGen thread info, in the middle of mono_init_internal, after the same thread had attached itself.
 * Meanwhile the parent was tearing down (eight worker threads exiting). A cell that was set and is NULL again is exactly what
 * a cross-process ThreadZeroTlsCell does. NOT PROVEN: the log has no TlsFree line. [tls-cross-clear] is the proof or the
 * refutation in the next log, and MADEIRA_TLS_CLEAR_OWN_PROCESS=1 is the keyed attempt.
 *
 * What this header holds (nothing in it writes to a TEB):
 *   - ios_tls_clear_own_process_only(): MADEIRA_TLS_CLEAR_OWN_PROCESS, read once. Unset or anything but "1": the old behaviour, a
 *     TlsFree clears the cell in every thread of the session. "1": only in threads whose PEB is the caller's.
 *   - ios_tls_clear_is_foreign(): the victim thread belongs to another process (both PEBs known and different).
 *   - ios_tls_clear_note(): the [tls-cross-clear] line, for a foreign thread whose cell was NOT zero (a live value). At most 40
 *     lines per run for victims without nw.dll; a victim process with nw.dll has its own budget (IOS_TLS_CROSS_PRIO_LINES). Every line
 *     carries a seq= and t= (monotonic) shared with the [nw-tls] AV report. Printed by default (log only); with the switch on it says "kept" instead of "cleared".
 */
#ifndef IOS_TLS_CLEAR_H
#define IOS_TLS_CLEAR_H

#include "ios_tls_diag_util.h"

#define IOS_TLS_CROSS_LINES 40

static int ios_tls_clear_own_process_only( void )
{
    static int mode = -1;

    if (mode < 0)
    {
        const char *e = getenv( "MADEIRA_TLS_CLEAR_OWN_PROCESS" );
        mode = e && e[0] == '1' && !e[1];
    }
    return mode;
}

/* 1 when `victim_peb` is the PEB of another process than the caller's. An unknown PEB on either side is never foreign: the old
 * behaviour (clear it) stays. */
static int ios_tls_clear_is_foreign( const void *victim_peb, const void *caller_peb )
{
    return caller_peb && victim_peb && victim_peb != caller_peb;
}

/* [tls-cross-clear] priority: a cross-process clear whose VICTIM process has nw.dll loaded (the Chromium/NW.js browser and its children)
 * is never counted against the IOS_TLS_CROSS_LINES budget of the other processes; it has its own, much larger budget. Neither
 * budget affects the clearing: the note is only a log line, written before the store that zeroes the cell. */
#define IOS_TLS_CROSS_PRIO_LINES 1000

/* One cleaning pass = one call of virtual_clear_tls_index. The answer "nw.dll is loaded in this PEB" is worked out at most once per
 * distinct victim PEB in the pass (lazily: only when a note is about to be written) and reused for that PEB's other threads. The table
 * lives on the caller's stack and dies with the pass: nothing is remembered between passes, neither a positive nor a negative answer. A
 * pass with more than 16 distinct victim PEBs works the answer out again for the 17th and later ones (not stored). */
struct ios_tls_clear_pass
{
    const void *peb[16];
    signed char nw[16];
    unsigned n;
};

/* What one note leaves for the DONE event that follows the store: filled only when a note line was written. */
struct ios_tls_clear_pending
{
    unsigned long long seq;      /* seq of the note line; 0 = no note was written, no DONE event */
    const void *cell;
    int is32;                    /* cell is a 32-bit ULONG */
    unsigned index, victim_tid;
    const void *victim_peb;
};

static int ios_tls_clear_victim_has_nw( struct ios_tls_clear_pass *pass, const void *victim_peb )
{
    unsigned i;
    int r;

    if (!victim_peb) return 0;
    for (i = 0; i < pass->n; i++) if (pass->peb[i] == victim_peb) return pass->nw[i];
    r = ios_tls_diag_find_module( (uint64_t)(uintptr_t)victim_peb, "nw.dll", NULL, NULL ) == 1;
    if (pass->n < 16)
    {
        pass->peb[pass->n] = victim_peb;
        pass->nw[pass->n++] = (signed char)r;
    }
    return r;
}

static const char *ios_tls_clear_bit_str( int b )
{
    return b < 0 ? "?" : b ? "1" : "0";
}

/* kept: 1 when the switch spared the cell, 0 when it was cleared (the store follows this call). old: the cell's value (not zero).
 * victim_teb / cell: the victim thread's TEB and the address of the cell (host addresses; for a 32-bit victim the WOW_TEB cell).
 * The allocation bit of `index` is read from BOTH 64-bit PEBs; for a 32-bit caller or victim the 64-bit PEB's bitmap is not the one
 * its TlsFree/TlsAlloc use, so that side prints "?" (n/a). TlsFree has already cleared the bit in the CALLER's PEB when this runs,
 * so a 0 on the caller side is expected; the victim side is the informative one: 1 = the index is allocated in the victim process. */
static void ios_tls_clear_note( struct ios_tls_clear_pass *pass, struct ios_tls_clear_pending *pend, int kept, unsigned index,
                                unsigned caller_tid, const void *caller_peb, int caller_32bit, unsigned victim_tid,
                                const void *victim_peb, const void *victim_teb, const void *cell, int victim_32bit,
                                unsigned long long old )
{
    static volatile int lines, plines;
    int n, prio = ios_tls_clear_victim_has_nw( pass, victim_peb );
    unsigned long long seq, t;
    int cbit, vbit;

    if (prio)
    {
        n = __sync_add_and_fetch( &plines, 1 );
        if (n > IOS_TLS_CROSS_PRIO_LINES + 1) return;
        if (n == IOS_TLS_CROSS_PRIO_LINES + 1)
        {
            dprintf( STDERR_FILENO, "[tls-cross-clear] %d priority (nw.dll victim) lines printed; the rest of them are not\n",
                     IOS_TLS_CROSS_PRIO_LINES );
            return;
        }
    }
    else
    {
        n = __sync_add_and_fetch( &lines, 1 );
        if (n > IOS_TLS_CROSS_LINES + 1) return;
        if (n == IOS_TLS_CROSS_LINES + 1)
        {
            dprintf( STDERR_FILENO, "[tls-cross-clear] %d lines printed; the rest of this run's cross-process TlsFree events are not\n",
                     IOS_TLS_CROSS_LINES );
            return;
        }
    }
    seq = ios_tls_diag_seq();
    t = ios_tls_diag_now_ns();
    cbit = caller_32bit ? -1 : ios_tls_diag_peb_bit( (uint64_t)(uintptr_t)caller_peb, index );
    vbit = victim_32bit ? -1 : ios_tls_diag_peb_bit( (uint64_t)(uintptr_t)victim_peb, index );
    dprintf( STDERR_FILENO,
             "[tls-cross-clear] seq=%llu t=%llu.%09llu rev=ajan7-arkham prio=%d TlsFree(%u) by tid %04x (peb %p, %s-bit) %s the live cell of tid %04x "
             "(peb %p, teb %p, cell %p, %s-bit): was 0x%llx%s; alloc-bit[caller peb]=%s alloc-bit[victim peb]=%s\n",
             seq, t / 1000000000ULL, t % 1000000000ULL, prio, index, caller_tid, caller_peb, caller_32bit ? "32" : "64",
             kept ? "SPARED (MADEIRA_TLS_CLEAR_OWN_PROCESS=1)" : "CLEARED", victim_tid, victim_peb, victim_teb, cell,
             victim_32bit ? "32" : "64", old, kept ? "" : " -> 0 (the store follows this line)",
             ios_tls_clear_bit_str( cbit ), ios_tls_clear_bit_str( vbit ) );
    pend->seq = seq;
    pend->cell = cell;
    pend->is32 = victim_32bit;
    pend->index = index;
    pend->victim_tid = victim_tid;
    pend->victim_peb = victim_peb;
}

/* [TLS_CLEAR_DONE]: written AFTER the store that zeroed the cell (the caller invokes it right behind the store; with the switch on and the
 * cell spared the store is skipped and so is this call). The cell is read back here (ios_tls_diag_read: a failed read prints READ FAILED), so
 * the line shows what the memory holds after the store, not what the code meant to write. Order inside this function: the store (done by
 * the caller just before the call), seq, then t, a memory barrier, the read-back, the print. seq / t are therefore taken after the store
 * and BEFORE the diagnostic read; they say "taken after the zero was written", not that the hardware events of different threads happened
 * in that exact order. In all three events the order of the two labels is seq, then t; note_seq names the [tls-cross-clear]
 * line of the same cell. The [tls-cross-clear] line is written BEFORE the store and is not evidence that the clear was done. */
static void ios_tls_clear_done( const struct ios_tls_clear_pending *pend )
{
    unsigned long long v = 0, seq, t;
    unsigned int v32 = 0;
    int ok;

    if (!pend->seq) return;
    seq = ios_tls_diag_seq();
    t = ios_tls_diag_now_ns();
    __atomic_thread_fence( __ATOMIC_SEQ_CST );
    ok = pend->is32 ? ios_tls_diag_read( (uint64_t)(uintptr_t)pend->cell, &v32, 4 )
                    : ios_tls_diag_read( (uint64_t)(uintptr_t)pend->cell, &v, 8 );
    if (pend->is32) v = v32;
    if (ok)
        dprintf( STDERR_FILENO, "[TLS_CLEAR_DONE] seq=%llu t=%llu.%09llu note_seq=%llu TlsFree(%u) cell %p of tid %04x (peb %p) read back after the store: 0x%llx%s\n",
                 seq, t / 1000000000ULL, t % 1000000000ULL, pend->seq, pend->index, pend->cell, pend->victim_tid, pend->victim_peb, v,
                 v ? " (NOT ZERO)" : " (zero)" );
    else
        dprintf( STDERR_FILENO, "[TLS_CLEAR_DONE] seq=%llu t=%llu.%09llu note_seq=%llu TlsFree(%u) cell %p of tid %04x (peb %p) read back after the store: READ FAILED\n",
                 seq, t / 1000000000ULL, t % 1000000000ULL, pend->seq, pend->index, pend->cell, pend->victim_tid, pend->victim_peb );
}

#endif
