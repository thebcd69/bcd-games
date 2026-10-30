/* [nw-tls]: the TLS state of a thread that takes a NULL-page access violation in a process with nw.dll loaded (Internship [demo], NW.js).
 *
 * Question it answers: is the TLS index nw.dll's Slot::Get uses (the variable at RVA 0x82C6F7C) equal to 5, and was the faulting
 * thread's cell for it zeroed (by a cross-process TlsFree, see ios_tls_clear.h) by the time of the fault? The RVAs are those of the
 * nw.dll with SHA-256 dbb53303...dd2a40, SizeOfImage 0x89B7000; a module of any other SizeOfImage is silently ignored (no line, no slot).
 *
 * Read-only. Every value comes from memory read here, in the fault handler, with mach_vm_read_overwrite (ios_tls_diag_read); the cell
 * is read from the faulting thread's own TEB memory and TlsGetValue is never called. The handler runs on the Mach exception thread
 * AFTER the fault, so the values are those at the time of this read, not at the instant of the access (a TlsFree that runs between the
 * two can change a cell). The line's seq= and t= are NOT the time of the AV: the handler takes them (fault_seq / fault_t) when it starts
 * working on the exception, just before the diagnostics chain, and passes them in; they order the start of the handling against the
 * [tls-cross-clear] and [TLS_CLEAR_DONE] lines. The AV itself happened at or before that point.
 *
 * The faulting GUEST instruction must lie inside the one function of nw.dll in which the failure was seen (IOS_NWTLS_FN_*_RVA, from the
 * .pdata entry and the disassembly of the analysed file), not anywhere in the image; this is tested BEFORE a TID slot is taken, so a NULL-class AV elsewhere
 * (another module, another process, a helper thread) never uses one of the IOS_NWTLS_MAX_TIDS slots. The guest address is resolved the
 * way ios_rwx_fault_diag.h does it (x28 -> state block -> block begin, ios_native_rip_from_hostpc); if the exact instruction cannot be
 * resolved the block's entry address is used and the line says so; with neither, nothing is printed and no slot is taken.
 *
 * Once per faulting TID (not once per process: all pseudo-processes of a session share this one address space and one static), at most
 * IOS_NWTLS_MAX_TIDS threads per run; the claim is an atomic compare-and-swap, so two threads never print for the same TID twice.
 * A fault in a process without nw.dll prints nothing and claims nothing.
 */
#ifndef IOS_NW_TLS_DIAG_H
#define IOS_NW_TLS_DIAG_H

#include "ios_tls_diag_util.h"   /* also needs ios_av_args_pc_is_jit from ios_av_args_diag.h (included earlier in signal_arm64_ios.c) */

#define IOS_NWTLS_MAX_TIDS    8
#define IOS_NWTLS_IMAGE_SIZE  0x89B7000u
/* The function the failure was seen in: the .pdata RUNTIME_FUNCTION of the analysed nw.dll (SHA-256 dbb53303...dd2a40) that holds the
 * crash, [0xEDD61A, 0xEDD69A) (BeginAddress 0xEDD61A, EndAddress 0xEDD69A, UnwindData 0x8285CC4). Disassembly: prologue push rsi at 0xEDD61A,
 * call 0xFFF540 at 0xEDD648 (return address 0xEDD64D), `mov rdi,[rax]` at 0xEDD661, ret at 0xEDD699. */
#define IOS_NWTLS_FN_BEGIN_RVA 0xEDD61Au
#define IOS_NWTLS_FN_END_RVA   0xEDD69Au     /* exclusive */
#define IOS_NWTLS_KEY_RVA     0x82C6F7Cu     /* the TLS index Slot::Get passes to TlsGetValue */
#define IOS_NWTLS_OBJ_RVA     0x83299E0u     /* lazy-instance object */
#define IOS_NWTLS_SLOT_RVA    0x83299E8u     /* its ThreadLocalStorage::Slot */

static void ios_nw_tls_diag( const arm_thread_state64_t *st, uintptr_t thread_teb, int exception, uintptr_t fault_addr,
                             unsigned long long fault_seq, unsigned long long fault_t )
{
    extern uint64_t ios_native_rip_from_hostpc( uint64_t, uint64_t, const char ** );
    static volatile uint32_t claimed[IOS_NWTLS_MAX_TIDS];
    uint64_t pc = 0, st28 = 0, bb = 0, entry = 0, rip = 0, self = 0, peb = 0, uniq = 0, base = 0, cell_addr = 0, cell = 0, exp_slots = 0, mask_ptr, a;
    uint32_t imgsize = 0, key = 0, tid, obj[4], slot[4];
    int found, have_key, have_cell = 0, have_obj, have_slot, bit, i, claim = 0;
    char cellstr[96], vec[160];
    const char *why = "", *rip_kind = "exact";

    if (exception != EXC_BAD_ACCESS || fault_addr >= 0x10000 || !thread_teb) return;
    pc = __darwin_arm_thread_state64_get_pc( *st );
    st28 = st->__x[28];
    if (!ios_av_args_pc_is_jit( pc ) || st28 < 0x100000) return;
    if (!ios_tls_diag_read( st28, &bb, 8 ) || !ios_tls_diag_read( st28 + 0x18, &entry, 8 )) return;
    rip = bb ? ios_native_rip_from_hostpc( bb, pc, &why ) : 0;
    if (!rip) { rip = entry; rip_kind = "BLOCK ENTRY, NOT the exact rip (exact rip unresolved)"; }
    if (rip < 0x10000 || rip >= 0x800000000000ULL) return;
    if (!ios_tls_diag_read( thread_teb + offsetof( TEB, Tib.Self ), &self, 8 ) || self != thread_teb) return;
    if (!ios_tls_diag_read( thread_teb + offsetof( TEB, Peb ), &peb, 8 ) ||
        !ios_tls_diag_read( thread_teb + offsetof( TEB, ClientId.UniqueThread ), &uniq, 8 )) return;
    found = ios_tls_diag_find_module( peb, "nw.dll", &base, &imgsize );
    if (found != 1) return;
    /* Only the function above: for the exact guest rip, rip inside [BEGIN, END). For the block-entry fallback the block must BEGIN inside
     * the same range (the block of an instruction is entered at or after the function's first instruction; a block that began in the
     * previous function would be rejected here), and the line then says "block entry", not an exact rip. Anything else: no slot is taken. */
    if (rip < base || rip - base < IOS_NWTLS_FN_BEGIN_RVA || rip - base >= IOS_NWTLS_FN_END_RVA) return;
    /* The RVAs of this file are those of one nw.dll build: a module of another size is not the target. Checked here, before a slot is
     * taken, and it prints nothing: a different build is neither reported as the target AV nor allowed to use one of the slots. */
    if (imgsize != IOS_NWTLS_IMAGE_SIZE) return;
    tid = (uint32_t)uniq;

    /* claim this TID (atomically); a TID already claimed, or a full table, ends here */
    for (i = 0; i < IOS_NWTLS_MAX_TIDS && !claim; i++)
    {
        uint32_t cur = __atomic_load_n( &claimed[i], __ATOMIC_ACQUIRE );

        if (cur == tid + 1) return;
        if (!cur && __sync_bool_compare_and_swap( &claimed[i], 0, tid + 1 )) claim = 1;
        else if (!cur && __atomic_load_n( &claimed[i], __ATOMIC_ACQUIRE ) == tid + 1) return;
    }
    if (!claim) return;

    dprintf( STDERR_FILENO, "[nw-tls] seq=%llu t=%llu.%09llu (exception-handler start, not the exact time of the AV) AV at 0x%llx in tid %04x (teb %p, peb %p): guest rip 0x%llx = nw.dll+0x%llx [%s]; nw.dll base 0x%llx SizeOfImage 0x%x\n",
             fault_seq, fault_t / 1000000000ULL, fault_t % 1000000000ULL, (unsigned long long)fault_addr, tid, (void *)thread_teb, (void *)(uintptr_t)peb,
             (unsigned long long)rip, (unsigned long long)(rip - base), rip_kind, (unsigned long long)base, imgsize );
    /* the key: RVA + 4 is inside the image (checked against the SizeOfImage above) */
    have_key = ios_tls_diag_read( base + IOS_NWTLS_KEY_RVA, &key, 4 );
    if (!have_key)
    {
        dprintf( STDERR_FILENO, "[nw-tls]   key at nw.dll+0x%x (0x%llx): not readable\n", IOS_NWTLS_KEY_RVA,
                 (unsigned long long)(base + IOS_NWTLS_KEY_RVA) );
    }
    else
    {
        snprintf( cellstr, sizeof(cellstr), "none" );
        if (key < IOS_TLSD_MIN_SLOTS)
        {
            cell_addr = thread_teb + offsetof( TEB, TlsSlots ) + 8 * (uint64_t)key;
            snprintf( cellstr, sizeof(cellstr), "TEB.TlsSlots[%u]", key );
        }
        else if (key < IOS_TLSD_MIN_SLOTS + IOS_TLSD_EXP_SLOTS)
        {
            if (ios_tls_diag_read( thread_teb + offsetof( TEB, TlsExpansionSlots ), &exp_slots, 8 ) && exp_slots)
            {
                cell_addr = exp_slots + 8 * (uint64_t)(key - IOS_TLSD_MIN_SLOTS);
                snprintf( cellstr, sizeof(cellstr), "TEB.TlsExpansionSlots[%u]", key - IOS_TLSD_MIN_SLOTS );
            }
            else snprintf( cellstr, sizeof(cellstr), "TlsExpansionSlots NULL or not readable" );
        }
        else if (key == 0xFFFFFFFFu) snprintf( cellstr, sizeof(cellstr), "key is TLS_OUT_OF_INDEXES (never allocated or already reset)" );
        else snprintf( cellstr, sizeof(cellstr), "key out of range" );
        if (cell_addr) have_cell = ios_tls_diag_read( cell_addr, &cell, 8 );
        bit = ios_tls_diag_peb_bit( peb, key );
        dprintf( STDERR_FILENO,
                 "[nw-tls]   key=%u (0x%x, %s5) read at 0x%llx; cell %s at 0x%llx: %s; alloc-bit in this PEB=%s\n", key, key,
                 key == 5 ? "==" : "!=", (unsigned long long)(base + IOS_NWTLS_KEY_RVA), cellstr, (unsigned long long)cell_addr,
                 !cell_addr ? "not read" : have_cell ? "read" : "READ FAILED", bit < 0 ? "?" : bit ? "1" : "0" );
        if (have_cell)
        {
            mask_ptr = cell & ~3ULL;
            dprintf( STDERR_FILENO, "[nw-tls]   cell value 0x%llx (low bits %llu, & ~3 = 0x%llx)%s\n", (unsigned long long)cell,
                     (unsigned long long)(cell & 3), (unsigned long long)mask_ptr,
                     !cell ? " -> ZERO" : !mask_ptr ? " -> EFFECTIVE ZERO (cell != 0 but cell & ~3 == 0: Slot::Get sees NULL)" : "" );
            /* Chromium's TlsVectorEntry {void *data; uint32_t version;} (16 bytes) is ASSUMED, not checked against this binary: the
             * entry for the slot index at nw.dll+0x83299E8 (read below) is shown only when the index is below 256. */
            vec[0] = 0;
            if (mask_ptr && ios_tls_diag_read( base + IOS_NWTLS_SLOT_RVA, slot, 4 ) && slot[0] < 256)
            {
                uint64_t ent[2];

                a = mask_ptr + 16ULL * slot[0];
                if (ios_tls_diag_read( a, ent, 16 ))
                    snprintf( vec, sizeof(vec), "; entry[%u] at 0x%llx = {0x%llx, 0x%llx} (assumed 16-byte entries)", slot[0],
                              (unsigned long long)a, (unsigned long long)ent[0], (unsigned long long)ent[1] );
                else snprintf( vec, sizeof(vec), "; entry[%u] at 0x%llx: READ FAILED", slot[0], (unsigned long long)a );
            }
            if (vec[0]) dprintf( STDERR_FILENO, "[nw-tls]  %s\n", vec );
        }
    }

    /* the lazy-instance object and its slot, 16 bytes each, as raw words */
    have_obj = ios_tls_diag_read( base + IOS_NWTLS_OBJ_RVA, obj, 16 );
    have_slot = ios_tls_diag_read( base + IOS_NWTLS_SLOT_RVA, slot, 16 );
    if (have_obj)
        dprintf( STDERR_FILENO, "[nw-tls]   nw.dll+0x%x: %08x %08x %08x %08x\n", IOS_NWTLS_OBJ_RVA, obj[0], obj[1], obj[2], obj[3] );
    else dprintf( STDERR_FILENO, "[nw-tls]   nw.dll+0x%x: READ FAILED\n", IOS_NWTLS_OBJ_RVA );
    if (have_slot)
        dprintf( STDERR_FILENO, "[nw-tls]   nw.dll+0x%x: %08x %08x %08x %08x\n", IOS_NWTLS_SLOT_RVA, slot[0], slot[1], slot[2], slot[3] );
    else dprintf( STDERR_FILENO, "[nw-tls]   nw.dll+0x%x: READ FAILED\n", IOS_NWTLS_SLOT_RVA );
}

#endif
