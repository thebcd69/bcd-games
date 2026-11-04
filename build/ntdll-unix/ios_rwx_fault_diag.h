/* ajan5-mhs3 (2026-10-21): [rwx-fault], a FAILURE-PATH DIAGNOSTIC for a guest fault in code the emulator
 * translated from a page that may have been rewritten under it.
 *
 * Log only. Every read of guest memory goes through mach_vm_read_overwrite (fault-safe); nothing is written,
 * protected or stopped. Called from one place: the UNHANDLED report of ios_mach_exception_thread, so only for a
 * fault every earlier handler declined, in JIT code, with the exact guest RIP in a page Wine holds executable.
 * At most 3 reports per process; a second report for the same 4 KB page is not a report.
 *
 * Why: MONSTER HUNTER STORIES 3 TRIAL (Steam 3470630, the owner's iPad16,5, build 576) and MONSTER HUNTER RISE
 * (MKPN2N, build 574) both die in the first instructions of their protector: an unnamed RWX section whose entry stub
 * decrypts its own code with a byte-wise xor loop. In Rise the block after the loop was compiled from the bytes
 * BEFORE the loop ran (the faulting host load is [rdi+0x764afffd], the bytes at that guest rip now read
 * `48 89 e9 8b 89 3c`) and the log holds no SMC event: the emulator was never told about the loop's stores. Whether
 * that is the pool copy (env.MADEIRA_X64_IMAGE_NOCOPY = 1 is the keyed attempt, rule x64-image-rwx-protector-nocopy)
 * or the 16 KB host page (Wine ORs the protection of the four 4 KB pages of a host page, so one armed 4 KB page of an
 * RWX section leaves the host page writable while a sibling still has W) cannot be told from the log. This line says:
 *   - the guest RIP of the faulting instruction and of its block's entry, and the PE section holding it (only an
 *     image with a pool copy is named; "no pool-copied image" means a run from the loaded view, or not an image);
 *   - the host region's real protection and extent (mach_vm_region);
 *   - Wine's protection byte of each of the four 4 KB pages of the 16 KB host page holding the RIP;
 *   - "trap": armed (the host page is read-only: a guest store faults and the emulator is told), OFF (Wine's table
 *     says the page is read-only but the host page is writable: a guest store does not fault), or writable (not armed);
 *   - the pool copy of that address, if any, and 32 bytes at the RIP from the loaded image and from the pool copy.
 * Reading it next time: trap OFF decides for the host-page (union) explanation, and NOCOPY will not help alone; trap
 * armed with differing image and pool bytes decides for the pool copy; trap armed and equal bytes puts the cause in the
 * emulator's own handling of the stores.
 */
#ifndef IOS_RWX_FAULT_DIAG_H
#define IOS_RWX_FAULT_DIAG_H

static void ios_rwx_fault_hex( char *out, size_t outsz, const uint8_t *b, unsigned n )
{
    unsigned i;

    out[0] = 0;
    for (i = 0; i < n && 3 * i + 4 <= outsz; i++) snprintf( out + 3 * i, outsz - 3 * i, "%02x ", b[i] );
    if (i) out[3 * i - 1] = 0;
}

static void ios_rwx_fault_prot( char *out, int prot )
{
    out[0] = prot < 0 ? '?' : (prot & 1) ? 'r' : '-';
    out[1] = prot < 0 ? '?' : (prot & 2) ? 'w' : '-';
    out[2] = prot < 0 ? '?' : (prot & 4) ? 'x' : '-';
    out[3] = 0;
}

static void ios_rwx_fault_diag( const arm_thread_state64_t *st, int exception )
{
    extern uint64_t ios_native_rip_from_hostpc( uint64_t, uint64_t, const char ** );
    extern int ios_image_section_describe( unsigned long long va, char *buf, size_t len, unsigned long long *img_base );
    extern unsigned char ios_reclaim_page_vprot( unsigned long long va );
    extern void *ios_jit_translate_addr( void *addr );
    static volatile int reports;
    static volatile uint64_t seen[3];
    uint64_t pc = __darwin_arm_thread_state64_get_pc( *st ), st28 = st->__x[28], bb = 0, entry = 0, rip = 0, page, host;
    const char *why = "";
    unsigned char vp[4], this_vp, any_vp = 0;
    unsigned i, nbytes = 32, ndiff = 0;
    uint8_t pe_b[32], pool_b[32];
    char sec[200], hex_pe[100], hex_pool[100], rp[4], rm[4], pool_text[64];
    unsigned long long ib = 0;
    mach_vm_address_t ra;
    mach_vm_size_t rs = 0;
    vm_region_basic_info_data_64_t ri;
    mach_msg_type_number_t rc = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t ro = MACH_PORT_NULL;
    int rprot = -1, rmax = -1, have_sec, have_pool, pe_ok, pool_ok, num;
    void *pool_va;
    const char *trap;

    if (exception != EXC_BAD_ACCESS || !ios_av_args_pc_is_jit( pc ) || st28 < 0x100000) return;
    if (!ios_av_args_read( st28, &bb, 8 ) || !ios_av_args_read( st28 + 0x18, &entry, 8 )) return;
    rip = bb ? ios_native_rip_from_hostpc( bb, pc, &why ) : 0;
    if (!rip) rip = entry;
    if (rip < 0x10000 || rip >= 0x800000000000ULL) return;
    this_vp = ios_reclaim_page_vprot( rip );
    if (!(this_vp & 0x04)) return;                                  /* Wine does not hold this page executable */
    for (i = 0; i < 3; i++) if (seen[i] == (rip & ~0xfffULL)) return;

    page = rip & ~0x3fffULL;
    for (i = 0; i < 4; i++) { vp[i] = ios_reclaim_page_vprot( page + 0x1000ULL * i ); any_vp |= vp[i]; }
    ra = (mach_vm_address_t)rip;
    if (mach_vm_region( mach_task_self(), &ra, &rs, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&ri, &rc, &ro ) == KERN_SUCCESS &&
        ra <= rip)
    {
        rprot = ri.protection; rmax = ri.max_protection;
    }
    else ra = 0;
    host = ra;
    have_sec = ios_image_section_describe( rip, sec, sizeof(sec), &ib );
    if (!have_sec) snprintf( sec, sizeof(sec), "no pool-copied image holds it (a run from the loaded view, or not an image)" );
    pool_va = ios_jit_translate_addr( (void *)(uintptr_t)rip );
    have_pool = pool_va && pool_va != (void *)(uintptr_t)rip;
    pe_ok = ios_av_args_read( rip, pe_b, nbytes );
    if (!pe_ok) { nbytes = 16; pe_ok = ios_av_args_read( rip, pe_b, nbytes ); }
    pool_ok = have_pool && pe_ok && ios_av_args_read( (uint64_t)(uintptr_t)pool_va, pool_b, nbytes );
    if (pool_ok) for (i = 0; i < nbytes; i++) if (pe_b[i] != pool_b[i]) ndiff++;
    ios_rwx_fault_hex( hex_pe, sizeof(hex_pe), pe_b, pe_ok ? nbytes : 0 );
    ios_rwx_fault_hex( hex_pool, sizeof(hex_pool), pool_b, pool_ok ? nbytes : 0 );
    if (!have_pool) snprintf( pool_text, sizeof(pool_text), "none" );
    else snprintf( pool_text, sizeof(pool_text), "%p", pool_va );
    ios_rwx_fault_prot( rp, rprot );
    ios_rwx_fault_prot( rm, rmax );
    if (rprot < 0) trap = "unknown (host region unreadable)";
    /* Round 630 (agent G1 S15): Wine maps a WRITECOPY page (0x08) writable as well; FINAL FANTASY RESONANCE's protected pages are 0x2d,
     * and the line named no cause for them. */
    else if ((rprot & 2) && !(this_vp & 0x02)) trap = (this_vp & 0x08)
        ? "OFF: Wine's table says this page is WRITECOPY, which keeps its host page writable"
        : (any_vp & 0x02) ? "OFF: Wine's table says this page is read-only, its host page is writable, and a neighbouring 4 KB page of it has W"
        : (any_vp & 0x08) ? "OFF: Wine's table says this page is read-only, its host page is writable, and a neighbouring 4 KB page of it is WRITECOPY"
        : "OFF: Wine's table says this page is read-only, its host page is writable";
    else if (rprot & 2) trap = "writable (not armed)";
    else trap = "armed (a guest store here faults)";
    if ((num = __sync_add_and_fetch( &reports, 1 )) > 3) return;
    seen[num - 1] = rip & ~0xfffULL;
    dprintf( STDERR_FILENO,
             "[rwx-fault] rev=ajan5-mhs3 #%d guest rip=0x%llx (block entry 0x%llx) %s | host region 0x%llx+0x%llx prot=%s max=%s | "
             "Wine vprot of the 4 KB pages of host page 0x%llx: %02x %02x %02x %02x (this page %02x) | trap: %s | pool copy: %s | "
             "%u bytes at rip, loaded image: %s | pool copy: %s | %s\n",
             num, (unsigned long long)rip, (unsigned long long)entry, sec, (unsigned long long)host, (unsigned long long)rs, rp, rm,
             (unsigned long long)page, vp[0], vp[1], vp[2], vp[3], this_vp, trap, pool_text, nbytes, pe_ok ? hex_pe : "unreadable",
             pool_ok ? hex_pool : (have_pool ? "unreadable" : "-"),
             !pool_ok ? "image and pool copy not compared"
                      : ndiff ? "image and pool copy DIFFER" : "image and pool copy equal" );
}

#endif
