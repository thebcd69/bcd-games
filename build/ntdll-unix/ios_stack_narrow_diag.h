/* madeira-bcd (round 714, agent M4 D7; Gal*Gun: Double Peace RZM5YP): [stack-narrow], a FAILURE-PATH DIAGNOSTIC for "a 64-bit
 * program threw away the upper half of an address on its own stack".
 *
 * Log only. Every read goes through ios_av_args_read (a fault-safe Mach read); nothing is written, protected or served. Called from
 * one place: the UNHANDLED report of ios_mach_exception_thread, so only for a bad access every earlier handler declined, and only
 * when the address is below 4 GB (where nothing can be mapped on iOS). At most 4 lines per process.
 *
 * Why: the game's ApexFramework_x64.dll builds a stack object at `(RSP + 0x28) & 0xfffffff8` with a ZERO-extended 32-bit mask
 * (lea rcx,[rsp+0x28]; mov eax,0xfffffff8; and rcx,rax), so 0x703bfaf288 becomes 0x3bfaf288 and the store faults in the DLL's
 * start-up code ("ApexFramework_x64.dll failed to initialize"). On Windows the program's stacks sit below 4 GB and the mask loses
 * nothing; on BCD every stack is above 4 GB. The log showed the narrowed pointer in RCX only, and the [operand] ml962 line looked
 * at R10. This names the case at once: the fault address equals the low 32 bits of an address inside the faulting thread's own
 * stack reservation [DeallocationStack, StackBase). Nothing serves the access yet (an opt-in key that would, MADEIRA_STACK_LOW_ALIAS,
 * waits for a second game that needs it); the line is how a second one would show.
 *
 * The arithmetic is ios_stack_narrow_real (pure, host-tested by tests/host/check-stack-narrow.py): a reservation that crosses a
 * 4 GB line, or that already lies below 4 GB, has no unambiguous low form and gives 0.
 */
#ifndef IOS_STACK_NARROW_DIAG_H
#define IOS_STACK_NARROW_DIAG_H

static uint64_t ios_stack_narrow_real( uint64_t addr, uint64_t len, uint64_t dealloc, uint64_t base )
{
    uint64_t lo, hi;

    if (addr >= 0x100000000ull || !len || dealloc < 0x100000000ull || base <= dealloc) return 0;
    if ((dealloc >> 32) != ((base - 1) >> 32)) return 0;
    lo = dealloc & 0xffffffffull;
    hi = lo + (base - dealloc);
    if (addr < lo || addr + len > hi) return 0;
    return dealloc + (addr - lo);
}

static void ios_stack_narrow_diag( uintptr_t thread_teb, int exception, uint64_t fault_addr )
{
    static int printed;
    uint64_t dealloc = 0, base = 0, real;
    int32_t woff = 0;

    if (exception != EXC_BAD_ACCESS || !thread_teb || fault_addr >= 0x100000000ull || fault_addr < 0x10000) return;
    if (__atomic_load_n( &printed, __ATOMIC_RELAXED ) >= 4) return;
    if (!ios_av_args_read( thread_teb + IOS_WN32_TEB_WOWOFF, &woff, 4 ) || woff != 0) return;   /* 64-bit programs only */
    if (!ios_av_args_read( thread_teb + offsetof( TEB, DeallocationStack ), &dealloc, 8 ) ||
        !ios_av_args_read( thread_teb + offsetof( TEB, Tib.StackBase ), &base, 8 )) return;
    if (!(real = ios_stack_narrow_real( fault_addr, 1, dealloc, base ))) return;
    if (__atomic_add_fetch( &printed, 1, __ATOMIC_RELAXED ) > 4) return;
    dprintf( 2, "[stack-narrow] r714 fault 0x%llx is the low 32 bits of this thread's own stack [0x%llx,0x%llx) (real 0x%llx): "
             "the program narrowed a stack address (a 32-bit mask or a 32-bit store of a pointer); nothing serves it yet\n",
             (unsigned long long)fault_addr, (unsigned long long)dealloc, (unsigned long long)base, (unsigned long long)real );
}

#endif /* IOS_STACK_NARROW_DIAG_H */
