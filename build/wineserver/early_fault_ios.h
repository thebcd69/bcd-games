/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * [early-fault] (round 624, agent D1 S8; lucieneslife C8G6JC): between the wineserver thread's init_signals() and the first Wine process's
 * signal_init_process() the only fault handler of the app is Wine's do_sigsegv, which prints one fixed sentence ("wineserver crashed, please
 * enable coredumps ...") and aborts; SIGBUS and SIGILL print nothing. This reporter is installed right after init_signals(), only while
 * signal_init_process() has not run yet (ios_signal_init_done: BCD's own handlers are never replaced, not even when the server is started a
 * second time in one app run). On a fault it first puts back exactly what was installed before, then prints one line with the signal, the
 * address, pc/lr/sp/fp, the thread and the loader step (ios_loader_phase) and the return addresses of the frame chain, and returns: the
 * faulting instruction runs again and meets the handler it would have met (do_sigsegv: the same sentence and abort; the default action
 * otherwise), so the process ends exactly as it did. write(2) only; every memory read is fault-safe; a fault inside the reporter goes to the
 * restored handler, never back here. */
#ifndef IOS_EARLY_FAULT_H
#define IOS_EARLY_FAULT_H

#include <signal.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <sys/ucontext.h>

extern const char *volatile ios_loader_phase;   /* build/ntdll-unix/loader_ios.c: the loader step the app is in */
extern volatile int ios_signal_init_done;        /* build/ntdll-unix/signal_arm64_ios.c: BCD's own handlers are installed */

static struct sigaction ios_early_old[3];   /* SIGSEGV, SIGBUS, SIGILL: what was installed before */

static int ios_early_index( int sig ) { return sig == SIGSEGV ? 0 : sig == SIGBUS ? 1 : 2; }

static size_t ios_early_cat( char *b, size_t l, size_t cap, const char *s )
{
    while (*s && l + 1 < cap) b[l++] = *s++;
    return l;
}

static size_t ios_early_num( char *b, size_t l, size_t cap, uint64_t v, int hex )
{
    static const char digits[] = "0123456789abcdef";
    char t[24];
    size_t n = 0;

    if (hex) l = ios_early_cat( b, l, cap, "0x" );
    do { t[n++] = digits[hex ? (v & 15) : (v % 10)]; v = hex ? (v >> 4) : (v / 10); } while (v);
    while (n && l + 1 < cap) b[l++] = t[--n];
    return l;
}

static void ios_early_fault( int sig, siginfo_t *si, void *ucv )
{
    uint64_t pc = 0, lr = 0, sp = 0, fp = 0;
    const char *phase = ios_loader_phase;
    char b[768];
    size_t l = 0;
    int n;

    (void)ucv;   /* read only on arm64 */
    /* first of all: what was there before is back (a second fault, here or after the return, goes to it) */
    sigaction( sig, &ios_early_old[ios_early_index( sig )], NULL );
#if defined(__arm64__) || defined(__aarch64__)
    if (ucv)
    {
        const ucontext_t *uc = ucv;

        pc = __darwin_arm_thread_state64_get_pc( uc->uc_mcontext->__ss );
        lr = __darwin_arm_thread_state64_get_lr( uc->uc_mcontext->__ss );
        sp = __darwin_arm_thread_state64_get_sp( uc->uc_mcontext->__ss );
        fp = __darwin_arm_thread_state64_get_fp( uc->uc_mcontext->__ss );
    }
#endif
    l = ios_early_cat( b, l, sizeof b, "[early-fault] sig=" );     l = ios_early_num( b, l, sizeof b, (uint64_t)sig, 0 );
    l = ios_early_cat( b, l, sizeof b, " code=" );                   l = ios_early_num( b, l, sizeof b, si ? (uint64_t)(unsigned)si->si_code : 0, 0 );
    l = ios_early_cat( b, l, sizeof b, " addr=" );                   l = ios_early_num( b, l, sizeof b, si ? (uint64_t)(uintptr_t)si->si_addr : 0, 1 );
    l = ios_early_cat( b, l, sizeof b, " pc=" );                     l = ios_early_num( b, l, sizeof b, pc, 1 );
    l = ios_early_cat( b, l, sizeof b, " lr=" );                     l = ios_early_num( b, l, sizeof b, lr, 1 );
    l = ios_early_cat( b, l, sizeof b, " sp=" );                     l = ios_early_num( b, l, sizeof b, sp, 1 );
    l = ios_early_cat( b, l, sizeof b, " fp=" );                     l = ios_early_num( b, l, sizeof b, fp, 1 );
    l = ios_early_cat( b, l, sizeof b, " thread=" );                 l = ios_early_num( b, l, sizeof b, (uint64_t)mach_thread_self(), 1 );
    l = ios_early_cat( b, l, sizeof b, " phase=" );                  l = ios_early_cat( b, l, sizeof b, phase ? phase : "?" );
    l = ios_early_cat( b, l, sizeof b, " rev=r624\n" );
    (void)!write( STDERR_FILENO, b, l );

    l = ios_early_cat( b, 0, sizeof b, "[early-fault] frames:" );
    for (n = 0; n < 16 && fp > 0x100000 && !(fp & 7); n++)
    {
        uint64_t pair[2];
        mach_vm_size_t got = 0;

        if (mach_vm_read_overwrite( mach_task_self(), (mach_vm_address_t)fp, 16, (mach_vm_address_t)pair, &got ) != KERN_SUCCESS || got != 16) break;
        l = ios_early_cat( b, l, sizeof b, " " );
        l = ios_early_num( b, l, sizeof b, pair[1], 1 );
        if (pair[0] <= fp || pair[0] - fp > 0x1000000) break;
        fp = pair[0];
    }
    l = ios_early_cat( b, l, sizeof b, "\n" );
    (void)!write( STDERR_FILENO, b, l );
    /* returning re-executes the faulting instruction, which now meets the handler that was there before */
}

static void ios_early_fault_install( void )
{
    static const int sigs[3] = { SIGSEGV, SIGBUS, SIGILL };
    struct sigaction sa;
    int i;

    if (ios_signal_init_done) return;   /* BCD's own handlers are in place: never replace them */
    memset( &sa, 0, sizeof(sa) );
    sa.sa_sigaction = ios_early_fault;
    sigemptyset( &sa.sa_mask );
    sa.sa_flags = SA_SIGINFO | SA_NODEFER;
    for (i = 0; i < 3; i++) sigaction( sigs[i], &sa, &ios_early_old[i] );
}

#endif
