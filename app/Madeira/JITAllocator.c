/* Portions copyright 2026 SirDank: code taken from https://github.com/willfaust/Madeira (pull request #362)
 * under GPL-3.0-or-later and modified for BCD in October 2026. See THIRD-PARTY-NOTICES.md. */
#include "JITAllocator.h"
#include "../../build/madeira_cfg.h"   /* metal-validation (ml1249) */

#include <mach/mach.h>
#include <mach/vm_map.h>
#include <sys/mman.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <libkern/OSCacheControl.h>
#include <pthread.h>
#include <unistd.h>
#include <signal.h>
#include <fcntl.h>
#include <errno.h>
#include <mach-o/dyld.h>
#include <os/log.h>
#include <os/proc.h>
#include <setjmp.h>
#include <dlfcn.h>
#include <CoreFoundation/CoreFoundation.h>

// csops syscall - used to check CS_DEBUGGED flag
#ifndef CS_DEBUGGED
#define CS_DEBUGGED 0x10000000
#endif
#ifndef CS_OPS_STATUS
#define CS_OPS_STATUS 0
#endif
extern int csops(pid_t pid, unsigned int ops, void *useraddr, size_t usersize);

/* ml748: <mach/mach_vm.h> is "unsupported" on the iOS SDK, but these are
 * exported by libsystem_kernel and are the only calls that report a region's
 * max_protection -- which the W^X probe needs, since the failure mode is a
 * successful-looking call whose W was silently stripped. */
extern kern_return_t mach_vm_region(vm_map_t, mach_vm_address_t *, mach_vm_size_t *,
                                    vm_region_flavor_t, vm_region_info_t,
                                    mach_msg_type_number_t *, mach_port_t *);
extern kern_return_t mach_vm_protect(vm_map_t, mach_vm_address_t, mach_vm_size_t,
                                     boolean_t, vm_prot_t);

// Page size on iOS is 16KB
#define JIT_PAGE_SIZE 0x4000

// VM_LEDGER_TAG_DEFAULT and VM_LEDGER_FLAG_NO_FOOTPRINT
// These are private Mach APIs used by MeloNX to make JIT memory
// not count against the app's Jetsam memory limit.
#ifndef VM_LEDGER_TAG_DEFAULT
#define VM_LEDGER_TAG_DEFAULT 0
#endif
#ifndef VM_LEDGER_FLAG_NO_FOOTPRINT
#define VM_LEDGER_FLAG_NO_FOOTPRINT (1 << 0)
#endif

// Private Mach API declarations
extern kern_return_t mach_memory_entry_ownership(
    mach_port_t mem_entry,
    mach_port_t owner,
    int ledger_tag,
    int ledger_flags
);

struct JITRegion {
    void *rw_ptr;       // Read-Write view (for writing code)
    void *rx_ptr;       // Read-Execute view (for executing code)
    size_t size;        // Size of the region
    mach_port_t mem_entry;  // Memory entry port for cleanup
};

static jit_log_callback_t g_log_callback = NULL;

void jit_set_log_callback(jit_log_callback_t callback) {
    g_log_callback = callback;
}

static void jit_log(const char *fmt, ...) {
    char buf[1024];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    if (g_log_callback) {
        g_log_callback(buf);
    }
    // Log to os_log (visible in Console.app) and stderr (Xcode)
    os_log(OS_LOG_DEFAULT, "[JIT] %{public}s", buf);
    fprintf(stderr, "[JIT] %s\n", buf);
}

static size_t align_to_page(size_t size) {
    return (size + JIT_PAGE_SIZE - 1) & ~(JIT_PAGE_SIZE - 1);
}

JITRegion *jit_region_create(size_t size) {
    size = align_to_page(size);

    JITRegion *region = calloc(1, sizeof(JITRegion));
    if (!region) {
        jit_log("Failed to allocate JITRegion struct");
        return NULL;
    }
    region->size = size;
    region->mem_entry = MACH_PORT_NULL;

    kern_return_t kr;
    mach_port_t task = mach_task_self();

    // Strategy: MeloNX dual-mapping approach
    //
    // 1. Create a named memory entry with RWX max protection
    // 2. Mark it as no-footprint (doesn't count against Jetsam limit)
    // 3. Map two views of it:
    //    - RW view for writing generated code
    //    - RX view for executing generated code

    // Step 1: Create a named memory entry
    memory_object_size_t entry_size = (memory_object_size_t)size;
    mach_port_t mem_entry = MACH_PORT_NULL;

    kr = mach_make_memory_entry_64(
        task,
        &entry_size,
        0,  // offset
        MAP_MEM_NAMED_CREATE | VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE,
        &mem_entry,
        MACH_PORT_NULL  // parent entry
    );

    if (kr != KERN_SUCCESS) {
        jit_log("mach_make_memory_entry_64 failed: %s (kr=%d)", mach_error_string(kr), kr);
        free(region);
        return NULL;
    }

    jit_log("Created memory entry: port=%d, size=%zu", mem_entry, (size_t)entry_size);
    region->mem_entry = mem_entry;

    // Step 2: Mark as no-footprint (MeloNX trick)
    // This makes the memory not count against the app's Jetsam memory limit.
    kr = mach_memory_entry_ownership(
        mem_entry,
        TASK_NULL,  // No owner task = system-owned
        VM_LEDGER_TAG_DEFAULT,
        VM_LEDGER_FLAG_NO_FOOTPRINT
    );

    if (kr != KERN_SUCCESS) {
        // Non-fatal: memory will just count against the limit
        jit_log("mach_memory_entry_ownership failed (non-fatal): %s (kr=%d)", mach_error_string(kr), kr);
    } else {
        jit_log("Memory entry marked as no-footprint");
    }

    // Step 3a: Map RW view (for writing code)
    mach_vm_address_t rw_addr = 0;
    kr = vm_map(
        task,
        (vm_address_t *)&rw_addr,
        size,
        0,  // mask
        VM_FLAGS_ANYWHERE,
        mem_entry,
        0,  // offset
        FALSE,  // copy
        VM_PROT_READ | VM_PROT_WRITE,      // current protection
        VM_PROT_READ | VM_PROT_WRITE,      // max protection
        VM_INHERIT_DEFAULT
    );

    if (kr != KERN_SUCCESS) {
        jit_log("vm_map (RW) failed: %s (kr=%d)", mach_error_string(kr), kr);
        mach_port_deallocate(task, mem_entry);
        free(region);
        return NULL;
    }

    region->rw_ptr = (void *)rw_addr;
    jit_log("Mapped RW view at %p", region->rw_ptr);

    // Step 3b: Map RX view (for executing code)
    mach_vm_address_t rx_addr = 0;
    kr = vm_map(
        task,
        (vm_address_t *)&rx_addr,
        size,
        0,  // mask
        VM_FLAGS_ANYWHERE,
        mem_entry,
        0,  // offset
        FALSE,  // copy
        VM_PROT_READ | VM_PROT_EXECUTE,                  // current protection
        VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE,  // max protection
        // Wider max so vm_protect can grant temporary W (e.g. FEX
        // PatchCallChecker writes to its __ImageBase + RVA, which lands
        // on the RX alias). vm_protect+VM_PROT_COPY then COW-flips the
        // page R/W. W^X still enforced via the actual current protection.
        VM_INHERIT_DEFAULT
    );

    if (kr != KERN_SUCCESS) {
        jit_log("vm_map (RX) failed: %s (kr=%d)", mach_error_string(kr), kr);
        // Clean up RW mapping
        vm_deallocate(task, (vm_address_t)region->rw_ptr, size);
        mach_port_deallocate(task, mem_entry);
        free(region);
        return NULL;
    }

    region->rx_ptr = (void *)rx_addr;
    jit_log("Mapped RX view at %p", region->rx_ptr);
    jit_log("Dual-mapped JIT region created: size=%zu, RW=%p, RX=%p", size, region->rw_ptr, region->rx_ptr);

    return region;
}

// ml358: make an ALREADY-MAPPED region jetsam-exempt.
//
// jit_region_create() marks its memory entry NO_FOOTPRINT, but the production
// pool never goes through it — allocatePool() gets its RX pages from StikDebug
// (jit26_prepare_region) and vm_remaps the RW alias, so the 896MB pool has
// been counting against phys_footprint in full this whole time. ml357 died of
// exactly that ("Terminated due to memory issue" with 848MB of pool written
// and PartitionAlloc's reserves at 0% committed).
//
// mach_make_memory_entry_64 over an existing range hands back an entry for the
// SAME vm_object both aliases share, so ownership applied to it should re-account
// resident pages as well as future ones. This is private API on a beta OS: every
// step is logged, failure is non-fatal, and phys_footprint is sampled either
// side so the log says whether it actually WORKED rather than whether it was
// merely attempted.
bool jit_make_region_no_footprint(void *addr, size_t size, const char *label) {
    mach_port_t task = mach_task_self();
    kern_return_t kr;

    uint64_t fp_before = 0, fp_after = 0;
    task_vm_info_data_t vmi;
    mach_msg_type_number_t vmi_cnt = TASK_VM_INFO_COUNT;
    if (task_info(task, TASK_VM_INFO, (task_info_t)&vmi, &vmi_cnt) == KERN_SUCCESS) {
        fp_before = vmi.phys_footprint;
    }

    /* ml458 (#75): the original single attempt used MAP_MEM_VM_SHARE and always
     * came back "ownership failed kr=4" (KERN_INVALID_ARGUMENT) — so the 896MB
     * pool has counted against phys_footprint every run, which is what pins the
     * pool at a size that now exhausts (ml455/ml456 both died there) and what
     * jetsam'd the 1024MB attempt at ml421-422.
     *
     * kr=4 is a REJECTED ENTRY KIND, not a permissions refusal (that would be
     * KERN_NO_ACCESS / KERN_PROTECTION_FAILURE): MAP_MEM_VM_SHARE hands back a
     * copy-style named entry, while the ownership call wants an entry that
     * names the VM object itself — which is exactly the difference from
     * jit_region_create()'s MAP_MEM_NAMED_CREATE entry, where this same
     * ownership call succeeds.
     *
     * This is undocumented private API on a beta OS, so instead of betting the
     * run on one guess, walk a small ladder of (entry-flags, owner) pairs and
     * let the log say which pair the kernel accepts.  Every step prints its kr;
     * the winner prints the phys_footprint delta, which is the only honest
     * proof the exemption took effect (a delta of ≈ the resident pool bytes
     * means real relief; ≈0 means the kernel accepted the call but kept
     * charging us). Non-fatal throughout: worst case we are exactly as
     * jetsam-counted as before. */
    struct { unsigned int flags; int own_self; const char *name; } variants[] = {
        { VM_PROT_READ | VM_PROT_WRITE,                    0, "plain/TASK_NULL" },
        { VM_PROT_READ | VM_PROT_WRITE,                    1, "plain/self" },
        { MAP_MEM_VM_SHARE | VM_PROT_READ | VM_PROT_WRITE, 1, "share/self" },
        { MAP_MEM_VM_SHARE | VM_PROT_READ | VM_PROT_WRITE, 0, "share/TASK_NULL" },
    };

    for (unsigned v = 0; v < sizeof(variants) / sizeof(variants[0]); v++) {
        memory_object_size_t entry_size = (memory_object_size_t)size;
        mach_port_t entry = MACH_PORT_NULL;
        kr = mach_make_memory_entry_64(
            task,
            &entry_size,
            (memory_object_offset_t)(uintptr_t)addr,
            variants[v].flags,
            &entry,
            MACH_PORT_NULL
        );
        if (kr != KERN_SUCCESS || entry == MACH_PORT_NULL) {
            jit_log("[no-footprint] %s: [%s] make_memory_entry failed kr=%d (%s) rev=ml458",
                    label, variants[v].name, kr, mach_error_string(kr));
            continue;
        }
        if ((size_t)entry_size < size) {
            jit_log("[no-footprint] %s: [%s] entry covers only %llu of %zu bytes (partial)",
                    label, variants[v].name, (unsigned long long)entry_size, size);
        }

        kr = mach_memory_entry_ownership(entry,
                                         variants[v].own_self ? task : TASK_NULL,
                                         VM_LEDGER_TAG_DEFAULT,
                                         VM_LEDGER_FLAG_NO_FOOTPRINT);
        mach_port_deallocate(task, entry);
        if (kr != KERN_SUCCESS) {
            jit_log("[no-footprint] %s: [%s] ownership failed kr=%d (%s) rev=ml458",
                    label, variants[v].name, kr, mach_error_string(kr));
            continue;
        }

        vmi_cnt = TASK_VM_INFO_COUNT;
        if (task_info(task, TASK_VM_INFO, (task_info_t)&vmi, &vmi_cnt) == KERN_SUCCESS) {
            fp_after = vmi.phys_footprint;
        }
        jit_log("[no-footprint] %s: [%s] ownership OK for %zu MB | phys_footprint %llu MB -> %llu MB "
                "(delta %lld MB) rev=ml458",
                label, variants[v].name, size >> 20,
                (unsigned long long)(fp_before >> 20), (unsigned long long)(fp_after >> 20),
                ((long long)fp_after - (long long)fp_before) >> 20);
        return true;
    }

    jit_log("[no-footprint] %s: ALL %zu variants refused — pool stays jetsam-counted rev=ml458",
            label, sizeof(variants) / sizeof(variants[0]));
    return false;
}

void jit_region_destroy(JITRegion *region) {
    if (!region) return;

    mach_port_t task = mach_task_self();

    if (region->rw_ptr) {
        vm_deallocate(task, (vm_address_t)region->rw_ptr, region->size);
        jit_log("Unmapped RW view at %p", region->rw_ptr);
    }
    if (region->rx_ptr) {
        vm_deallocate(task, (vm_address_t)region->rx_ptr, region->size);
        jit_log("Unmapped RX view at %p", region->rx_ptr);
    }
    if (region->mem_entry != MACH_PORT_NULL) {
        mach_port_deallocate(task, region->mem_entry);
    }

    free(region);
}

void *jit_region_rw_ptr(JITRegion *region) {
    return region ? region->rw_ptr : NULL;
}

void *jit_region_rx_ptr(JITRegion *region) {
    return region ? region->rx_ptr : NULL;
}

size_t jit_region_size(JITRegion *region) {
    return region ? region->size : 0;
}

void jit_region_invalidate(JITRegion *region, size_t offset, size_t size) {
    if (!region || !region->rx_ptr) return;
    sys_icache_invalidate((char *)region->rx_ptr + offset, size);
}

void *jit_region_write(JITRegion *region, size_t offset, const void *code, size_t code_size) {
    if (!region) return NULL;
    if (offset + code_size > region->size) {
        jit_log("Write out of bounds: offset=%zu, code_size=%zu, region_size=%zu",
                offset, code_size, region->size);
        return NULL;
    }

    // Write to the RW view
    memcpy((char *)region->rw_ptr + offset, code, code_size);

    // Invalidate icache on the RX view
    sys_icache_invalidate((char *)region->rx_ptr + offset, code_size);

    // Return the RX pointer for execution
    return (char *)region->rx_ptr + offset;
}

// SIGTRAP handler: skips BRK instruction (PC += 4) and zeros x0.
// This prevents crashes when BRK is executed without a debugger attached.
// ml1233: only the JIT protocol's BRK #0xf00d (as Wine's handler does). Any other
// trap is not ours -- a Swift runtime trap (precondition, force unwrap, overflow)
// is BRK #1 -- and was skipped too, running on past it with x0 = 0. The handler
// stays installed for the rest of the app run (from start-up, since ios18-jit also
// with CS_DEBUGGED set, or from jit_arm_trap_fallback before a launch that then
// fails and keeps the app up), so put the default action back and return: the
// instruction traps again and the app crashes with a report.
static void sigtrap_handler(int sig, siginfo_t *info, void *context) {
    (void)info;
    ucontext_t *uc = (ucontext_t *)context;
    uint64_t pc = uc->uc_mcontext->__ss.__pc;
    if ((pc & 3) || *(const uint32_t *)(uintptr_t)pc != 0xd43e01a0u /* brk #0xf00d */) {
        struct sigaction dfl;
        memset(&dfl, 0, sizeof(dfl));
        dfl.sa_handler = SIG_DFL;
        sigaction(sig, &dfl, NULL);
        return;
    }
    uc->uc_mcontext->__ss.__pc += 4;
    uc->uc_mcontext->__ss.__x[0] = 0;
}

// madeira-bcd ios18-jit (2026-10-18): installed whether or not CS_DEBUGGED is already set.
// An app started already debugged (StikDebug's app list, LiveContainer's JIT launch, Xcode)
// used to get no handler, and its pool request then died on the BRK once that debugger had
// left (docs/research/ios18-2026-10-18/runtime.md §2.3 b). A debugger that is attached still
// receives every BRK first: the kernel raises the Mach exception, the debugger's exception port
// takes it, and the BSD signal this handler catches is never sent. That is BCD's everyday case
// too: it starts without CS_DEBUGGED, installs this handler, and StikDebug attaches with BCD's
// script afterwards and answers every BRK. `when_debugged` false (MADEIRA_JIT_TRAP_FALLBACK=0)
// keeps the old rule: no handler when CS_DEBUGGED is set.
void jit_install_trap_handler(bool when_debugged) {
    bool debugged = jit_check_debugged();
    if (debugged && !when_debugged) {
        jit_log("Debugger attached — skipping SIGTRAP handler (debugger handles BRK)");
        return;
    }
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_flags = SA_SIGINFO;
    sa.sa_sigaction = sigtrap_handler;
    sigaction(SIGTRAP, &sa, NULL);
    jit_log(debugged ? "SIGTRAP handler installed (CS_DEBUGGED set: a debugger that is attached still takes BRK first)"
                     : "SIGTRAP handler installed (no debugger)");
}

void jit_arm_trap_fallback(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_flags = SA_SIGINFO;
    sa.sa_sigaction = sigtrap_handler;
    sigaction(SIGTRAP, &sa, NULL);
    jit_log("SIGTRAP handler installed (CS_DEBUGGED set, no debugger attached)");
}

bool jit_cs_status(uint32_t *flags) {
    uint32_t value = 0;
    if (csops(getpid(), CS_OPS_STATUS, &value, sizeof(value)) != 0) return false;
    *flags = value;
    return true;
}

bool jit_task_map_range(uint64_t *min_address, uint64_t *max_address) {
    task_vm_info_data_t vmi;
    mach_msg_type_number_t cnt = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&vmi, &cnt) != KERN_SUCCESS ||
        !vmi.max_address) return false;
    *min_address = vmi.min_address;
    *max_address = vmi.max_address;
    return true;
}

uint64_t jit_available_memory(void) {
    return (uint64_t)os_proc_available_memory();
}

uint64_t jit_phys_footprint(void) {
    task_vm_info_data_t vmi;
    mach_msg_type_number_t cnt = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&vmi, &cnt) != KERN_SUCCESS) return 0;
    return vmi.phys_footprint;
}

/* ============================================================================
 * madeira-bcd ios18-jit (2026-10-18): JIT MEMORY WITHOUT A DEBUGGER
 * (docs/research/ios18-2026-10-18/runtime.md §2.5.3).
 *
 * A debugger that attaches and leaves -- StikDebug without BCD's script (iOS 18,
 * and iOS 26 on devices it treats as non-TXM), AltJIT, SideJITServer, Xcode,
 * LiveContainer's JIT launch -- leaves CS_DEBUGGED set for good. cs_allow_invalid()
 * cleared CS_KILL and CS_HARD (an unsigned page no longer kills: cs_invalid_page
 * only drops CS_VALID), opened the map to invalid code (vm_map_cs_wx_enable) and
 * allowed memory-ownership transfer. Where no TXM polices code (iOS 18; iOS 26
 * without TXM) that lets the process map its own executable memory, so the pool
 * needs no debugger. StikJITHelper decides where (JITNoDebuggerPool) and runs
 * jit_nodebug_selftest() first; on a TXM device none of this runs, because there
 * executing a page TXM never blessed can end the process.
 *
 * Each region is a named memory entry (max RWX), mapped RX wherever the kernel's
 * first fit puts it -- the same placement the debugger's _M gets (mach_vm_allocate
 * ANYWHERE from the bottom of the map), so the pool census and its plugs steer it
 * the same way -- and mapped a second time, RW, for the alias. No vm_remap of
 * unsigned RX pages (the Dart VM: "kernel crashes in certain combinations of
 * hardware and kernel version"), no prepare_memory_region writes (the pool is not
 * dirty from birth), and NO_FOOTPRINT on the entry.
 * ==========================================================================*/

/* One page through the same recipe as the pool: mov x0, #42; ret. */
static const unsigned char nodebug_probe_code[] = {
#if defined(__aarch64__)
    0x40, 0x05, 0x80, 0xd2,             /* mov x0, #42 */
    0xc0, 0x03, 0x5f, 0xd6,             /* ret */
#elif defined(__x86_64__)
    0xb8, 0x2a, 0x00, 0x00, 0x00,       /* mov eax, 42 (tests/host/check-jit-no-debugger.py on an Intel Mac) */
    0xc3,                               /* ret */
#else
#error "jit_nodebug_selftest has no probe code for this architecture"
#endif
};

static sigjmp_buf nodebug_jb;
static volatile sig_atomic_t nodebug_armed;
static pthread_t nodebug_thread;
static const int nodebug_sigs[3] = { SIGBUS, SIGSEGV, SIGILL };
static struct sigaction nodebug_prev[3];

/* A fault while the probe runs on the probing thread ends the probe, not the app.
 * Anything else goes to the handler that was there before (or its default action). */
static void nodebug_fault(int sig, siginfo_t *info, void *ctx)
{
    if (nodebug_armed && pthread_equal(pthread_self(), nodebug_thread)) {
        nodebug_armed = 0;
        siglongjmp(nodebug_jb, sig);
    }
    for (int i = 0; i < 3; i++) {
        if (nodebug_sigs[i] != sig) continue;
        const struct sigaction *prev = &nodebug_prev[i];
        if ((prev->sa_flags & SA_SIGINFO) && prev->sa_sigaction) { prev->sa_sigaction(sig, info, ctx); return; }
        if (!(prev->sa_flags & SA_SIGINFO) && prev->sa_handler != SIG_DFL && prev->sa_handler != SIG_IGN) {
            prev->sa_handler(sig);
            return;
        }
        sigaction(sig, prev, NULL);     /* the instruction faults again and gets the old action */
        return;
    }
}

static kern_return_t nodebug_entry(size_t size, mach_port_t *entry)
{
    memory_object_size_t esize = (memory_object_size_t)size;
    *entry = MACH_PORT_NULL;
    kern_return_t kr = mach_make_memory_entry_64(mach_task_self(), &esize, 0,
                                                 MAP_MEM_NAMED_CREATE | VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE,
                                                 entry, MACH_PORT_NULL);
    if (kr == KERN_SUCCESS && esize < size) {
        mach_port_deallocate(mach_task_self(), *entry);
        *entry = MACH_PORT_NULL;
        kr = KERN_RESOURCE_SHORTAGE;
    }
    return kr;
}

/* The probe with the RX view's protection as a parameter: the host check maps it
 * without EXECUTE to prove that a refused page ends the probe and not the app. */
static bool nodebug_selftest_prot(vm_prot_t rx_prot, char *detail, size_t detail_len)
{
    mach_port_t task = mach_task_self(), entry = MACH_PORT_NULL;
    vm_address_t rx = 0, rw = 0;
    const vm_size_t size = JIT_PAGE_SIZE;
    volatile int64_t result = -1;
    bool ok = false;
    int sig;

    kern_return_t kr = nodebug_entry(size, &entry);
    if (kr != KERN_SUCCESS) {
        snprintf(detail, detail_len, "no memory entry (kr=%d)", kr);
        return false;
    }
    kr = vm_map(task, &rx, size, 0, VM_FLAGS_ANYWHERE, entry, 0, FALSE, rx_prot, VM_PROT_ALL, VM_INHERIT_NONE);
    if (kr != KERN_SUCCESS) {
        snprintf(detail, detail_len, "the RX view was refused (vm_map kr=%d)", kr);
        rx = 0;
        goto out;
    }
    kr = vm_map(task, &rw, size, 0, VM_FLAGS_ANYWHERE, entry, 0, FALSE,
                VM_PROT_READ | VM_PROT_WRITE, VM_PROT_ALL, VM_INHERIT_NONE);
    if (kr != KERN_SUCCESS) {
        snprintf(detail, detail_len, "the RW view was refused (vm_map kr=%d)", kr);
        rw = 0;
        goto out;
    }
    memcpy((void *)rw, nodebug_probe_code, sizeof(nodebug_probe_code));
    sys_icache_invalidate((void *)rx, sizeof(nodebug_probe_code));
    if ((rx_prot & VM_PROT_READ) && memcmp((const void *)rx, nodebug_probe_code, sizeof(nodebug_probe_code)) != 0) {
        snprintf(detail, detail_len, "the RX view does not show what the RW view wrote");
        goto out;
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_flags = SA_SIGINFO;
    sa.sa_sigaction = nodebug_fault;
    sigemptyset(&sa.sa_mask);
    for (int i = 0; i < 3; i++) sigaction(nodebug_sigs[i], &sa, &nodebug_prev[i]);
    sig = sigsetjmp(nodebug_jb, 1);
    if (sig == 0) {
        nodebug_thread = pthread_self();
        nodebug_armed = 1;
        result = ((int64_t (*)(void))rx)();
        nodebug_armed = 0;
    }
    for (int i = 0; i < 3; i++) sigaction(nodebug_sigs[i], &nodebug_prev[i], NULL);

    if (sig != 0)
        snprintf(detail, detail_len, "executing the page raised signal %d", sig);
    else if (result != 42)
        snprintf(detail, detail_len, "the page returned %lld, not 42", (long long)result);
    else {
        snprintf(detail, detail_len, "a page mapped RX from a memory entry ran and returned 42");
        ok = true;
    }
out:
    if (rw) vm_deallocate(task, rw, size);
    if (rx) vm_deallocate(task, rx, size);
    mach_port_deallocate(task, entry);
    return ok;
}

bool jit_nodebug_selftest(char *detail, size_t detail_len)
{
    bool ok = nodebug_selftest_prot(VM_PROT_READ | VM_PROT_EXECUTE, detail, detail_len);
    jit_log("[jit-nodebug] self-test %s: %s", ok ? "passed" : "FAILED", detail);
    return ok;
}

void *jit_nodebug_region(size_t size, unsigned int *entry_out)
{
    mach_port_t task = mach_task_self(), entry = MACH_PORT_NULL;
    vm_address_t rx = 0;   /* first fit from the bottom of the map, as the debugger's _M */

    *entry_out = MACH_PORT_NULL;
    size = align_to_page(size);
    kern_return_t kr = nodebug_entry(size, &entry);
    if (kr != KERN_SUCCESS) {
        jit_log("[jit-nodebug] no memory entry for %zuMB (kr=%d)", size >> 20, kr);
        return NULL;
    }
    kern_return_t own = mach_memory_entry_ownership(entry, TASK_NULL, VM_LEDGER_TAG_DEFAULT,
                                                    VM_LEDGER_FLAG_NO_FOOTPRINT);
    kr = vm_map(task, &rx, size, 0, VM_FLAGS_ANYWHERE, entry, 0, FALSE,
                VM_PROT_READ | VM_PROT_EXECUTE, VM_PROT_ALL, VM_INHERIT_DEFAULT);
    if (kr != KERN_SUCCESS) {
        jit_log("[jit-nodebug] %zuMB could not be mapped RX (vm_map kr=%d)", size >> 20, kr);
        mach_port_deallocate(task, entry);
        return NULL;
    }
    jit_log("[jit-nodebug] region RX 0x%lx+%zuMB from a memory entry, no debugger (no-footprint %s, kr=%d)",
            (unsigned long)rx, size >> 20, own == KERN_SUCCESS ? "applied" : "refused", own);
    *entry_out = entry;
    return (void *)rx;
}

int jit_nodebug_map_alias(unsigned int entry, uintptr_t *address, size_t size, int flags)
{
    vm_address_t a = (vm_address_t)*address;
    kern_return_t kr = vm_map(mach_task_self(), &a, (vm_size_t)size, 0, flags, (mach_port_t)entry, 0, FALSE,
                              VM_PROT_READ | VM_PROT_WRITE, VM_PROT_ALL, VM_INHERIT_NONE);
    if (kr == KERN_SUCCESS) *address = (uintptr_t)a;
    return kr;
}

void jit_nodebug_release(unsigned int entry)
{
    if (entry != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), (mach_port_t)entry);
}

/* Whether the device tree lists TXM: StikJIT's own test (IODeviceTree:/chosen/memory-map
 * has a "TXM..." key). 1 listed, 0 not listed, -1 unknown (no IOKit, or the sandbox does
 * not show the node). Only asked on iOS 26, where some models StikDebug treats as non-TXM
 * may have it. */
int jit_txm_in_device_tree(void)
{
    static const char *const paths[] = { "/System/Library/Frameworks/IOKit.framework/IOKit",
                                         "/System/Library/Frameworks/IOKit.framework/Versions/A/IOKit" };
    void *iokit = NULL;
    for (unsigned i = 0; i < sizeof(paths) / sizeof(paths[0]) && !iokit; i++) iokit = dlopen(paths[i], RTLD_LAZY | RTLD_LOCAL);
    if (!iokit) {
        jit_log("[jit-nodebug] device tree: unknown (IOKit not loaded)");
        return -1;
    }
    mach_port_t (*from_path)(mach_port_t, const char *) =
        (mach_port_t (*)(mach_port_t, const char *))dlsym(iokit, "IORegistryEntryFromPath");
    CFTypeRef (*property)(mach_port_t, CFStringRef, CFAllocatorRef, uint32_t) =
        (CFTypeRef (*)(mach_port_t, CFStringRef, CFAllocatorRef, uint32_t))dlsym(iokit, "IORegistryEntryCreateCFProperty");
    kern_return_t (*release)(mach_port_t) = (kern_return_t (*)(mach_port_t))dlsym(iokit, "IOObjectRelease");
    if (!from_path || !property || !release) {
        jit_log("[jit-nodebug] device tree: unknown (IOKit functions missing)");
        return -1;
    }
    mach_port_t node = from_path(MACH_PORT_NULL, "IODeviceTree:/chosen/memory-map");
    if (node == MACH_PORT_NULL) {
        jit_log("[jit-nodebug] device tree: unknown (IODeviceTree:/chosen/memory-map not visible)");
        return -1;
    }
    CFTypeRef keys = property(node, CFSTR("IORegistryEntryPropertyKeys"), kCFAllocatorDefault, 0);
    release(node);
    int found = -1;
    if (keys && CFGetTypeID(keys) == CFArrayGetTypeID()) {
        found = 0;
        for (CFIndex i = 0, n = CFArrayGetCount((CFArrayRef)keys); i < n && !found; i++) {
            CFTypeRef key = CFArrayGetValueAtIndex((CFArrayRef)keys, i);
            if (key && CFGetTypeID(key) == CFStringGetTypeID() &&
                CFStringFind((CFStringRef)key, CFSTR("TXM"), 0).location != kCFNotFound) found = 1;
        }
    }
    if (keys) CFRelease(keys);
    jit_log("[jit-nodebug] device tree: TXM %s", found == 1 ? "listed" : found == 0 ? "not listed" : "unknown (no key list)");
    return found;
}

// iOS 26 BRK-based JIT syscalls.
// These use BRK #0xf00d with x16 indicating the command.
// When a debugger (StikDebug) is attached, it intercepts the BRK,
// reads x16/x0/x1, performs the operation, and resumes.
// When no debugger is attached, the SIGTRAP handler skips the BRK.

__attribute__((noinline, optnone))
void *jit26_prepare_region(void *addr, size_t len) {
    register void *x0 __asm__("x0") = addr;
    register size_t x1 __asm__("x1") = len;
    __asm__ volatile(
        "mov x16, #1\n"
        "brk #0xf00d\n"
        : "+r"(x0)
        : "r"(x1)
        : "x16", "memory"
    );
    return x0;
}

__attribute__((noinline, optnone))
void jit26_detach(void) {
    __asm__ volatile(
        "mov x16, #0\n"
        "brk #0xf00d\n"
        ::: "x16", "memory"
    );
}

bool jit_check_debugged(void) {
    uint32_t flags = 0;
    int result = csops(getpid(), CS_OPS_STATUS, &flags, sizeof(flags));
    if (result != 0) {
        jit_log("csops failed, assuming not debugged");
        return false;
    }
    bool debugged = (flags & CS_DEBUGGED) != 0;
    jit_log("CS_DEBUGGED flag: %s (flags=0x%x)", debugged ? "SET" : "NOT SET", flags);
    return debugged;
}

// Same check without the log line, for callers that poll (the JIT badge
// re-reads this every 2s and jit_check_debugged would flood the log).
bool jit_is_debugged_quiet(void) {
    uint32_t flags = 0;
    if (csops(getpid(), CS_OPS_STATUS, &flags, sizeof(flags)) != 0) return false;
    return (flags & CS_DEBUGGED) != 0;
}

bool jit_test_mapping(void) {
    jit_log("=== JIT Mapping Test (non-executing) ===");

    // Test 1: Can we create a dual-mapped region?
    JITRegion *region = jit_region_create(JIT_PAGE_SIZE);
    if (!region) {
        jit_log("FAIL: Could not create dual-mapped region");
        return false;
    }
    jit_log("OK: Dual-mapped region created");

    // Test 2: Are RW and RX at different virtual addresses?
    if (region->rw_ptr == region->rx_ptr) {
        jit_log("FAIL: RW and RX are at the same address");
        jit_region_destroy(region);
        return false;
    }
    jit_log("OK: RW=%p != RX=%p", region->rw_ptr, region->rx_ptr);

    // Test 3: Write to RW, read from RX to verify shared backing
    uint32_t pattern = 0xDEADBEEF;
    memcpy(region->rw_ptr, &pattern, sizeof(pattern));
    uint32_t readback = 0;
    memcpy(&readback, region->rx_ptr, sizeof(readback));

    if (readback != pattern) {
        jit_log("FAIL: Write 0x%x to RW, read 0x%x from RX", pattern, readback);
        jit_region_destroy(region);
        return false;
    }
    jit_log("OK: Dual-map coherent (wrote 0x%x, read 0x%x)", pattern, readback);

    // Test 4: Check RX page has execute permission via vm_region
    vm_address_t addr = (vm_address_t)region->rx_ptr;
    vm_size_t region_size = 0;
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t info_count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object_name = MACH_PORT_NULL;

    kern_return_t kr = vm_region_64(
        mach_task_self(), &addr, &region_size,
        VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info,
        &info_count, &object_name
    );

    if (kr == KERN_SUCCESS && (info.protection & VM_PROT_EXECUTE)) {
        jit_log("OK: RX page has execute permission");
    } else {
        jit_log("WARN: RX page may lack execute permission (prot=0x%x)", info.protection);
    }

    jit_region_destroy(region);
    jit_log("=== Mapping test passed ===");
    return true;
}

// Check page protection via vm_region
static void jit_check_page_protection(void *addr, const char *label) {
    vm_address_t query_addr = (vm_address_t)addr;
    vm_size_t region_size = 0;
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t info_count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object_name = MACH_PORT_NULL;

    kern_return_t kr = vm_region_64(
        mach_task_self(), &query_addr, &region_size,
        VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info,
        &info_count, &object_name
    );

    if (kr == KERN_SUCCESS) {
        jit_log("%s at %p: prot=0x%x (R%s%s), max_prot=0x%x, size=%zu",
                label, addr,
                info.protection,
                (info.protection & VM_PROT_WRITE) ? "W" : "-",
                (info.protection & VM_PROT_EXECUTE) ? "X" : "-",
                info.max_protection,
                (size_t)region_size);
    } else {
        jit_log("%s at %p: vm_region failed (kr=%d)", label, addr, kr);
    }
}

int64_t jit_test_execute(void) {
    jit_log("=== JIT Execution Test ===");

    // Check CS_DEBUGGED first
    if (!jit_check_debugged()) {
        jit_log("FAIL: CS_DEBUGGED not set. Attach debugger first.");
        return -2;
    }

    // ARM64 machine code for: int64_t test_func(void) { return 42; }
    uint32_t code[] = {
        0xD2800540,  // mov x0, #42
        0xD65F03C0,  // ret
    };

    // Strategy 1: Dual-mapped region, write FIRST then prepare
    // (TXM may authorize page content at preparation time)
    jit_log("--- Strategy 1: Dual-map, write-then-prepare ---");
    {
        JITRegion *region = jit_region_create(JIT_PAGE_SIZE);
        if (!region) {
            jit_log("FAIL: Could not create JIT region");
            return -1;
        }

        // Write code FIRST (before prepare)
        jit_log("Writing ARM64 code (%zu bytes) to RW view BEFORE prepare", sizeof(code));
        void *exec_ptr = jit_region_write(region, 0, code, sizeof(code));
        if (!exec_ptr) {
            jit_log("FAIL: Could not write code to JIT region");
            jit_region_destroy(region);
            return -1;
        }

        jit_check_page_protection(region->rx_ptr, "RX page BEFORE prepare");

        // NOW ask debugger to prepare (authorize) the pages
        jit_log("Requesting debugger to prepare RX region at %p (%zu bytes)...",
                region->rx_ptr, region->size);
        void *prepared = jit26_prepare_region(region->rx_ptr, region->size);
        jit_log("prepare_region returned: %p", prepared);

        jit_check_page_protection(region->rx_ptr, "RX page AFTER prepare");

        jit_log("Executing from RX view at %p...", exec_ptr);

        typedef int64_t (*jit_func_t)(void);
        jit_func_t func = (jit_func_t)exec_ptr;
        int64_t result = func();

        jit_log("Result: %lld (expected 42)", result);

        if (result == -3) {
            jit_log("Strategy 1: FAULT LOOP detected — TXM rejected self-mapped RX pages");
            jit_region_destroy(region);
        } else if (result == 42) {
            jit_log("SUCCESS: Strategy 1 works! JIT is functional.");

            // Run additional tests
            uint32_t add_code[] = {
                0x8B010000,  // add x0, x0, x1
                0xD65F03C0,  // ret
            };
            void *add_ptr = jit_region_write(region, sizeof(code), add_code, sizeof(add_code));
            if (add_ptr) {
                // Re-prepare after writing new code
                jit26_prepare_region(region->rx_ptr, region->size);
                typedef int64_t (*add_func_t)(int64_t, int64_t);
                int64_t add_result = ((add_func_t)add_ptr)(100, 200);
                jit_log("add(100, 200) = %lld (expected 300)", add_result);
            }

            jit_region_destroy(region);
            jit_log("=== JIT tests passed (strategy 1) ===");
            return 42;
        } else {
            jit_log("Strategy 1 failed with result: %lld", result);
            jit_region_destroy(region);
        }
    }

    // If strategy 1 fault-looped, try strategy 2 automatically
    jit_log("Strategy 1 did not succeed, trying strategy 2...");
    return jit_test_execute_strategy2();
}

int64_t jit_test_execute_strategy2(void) {
    jit_log("--- Strategy 2: Debugger-allocated RX + vm_remap RW (MeloNX approach) ---");

    if (!jit_check_debugged()) {
        jit_log("FAIL: CS_DEBUGGED not set. Attach debugger first.");
        return -2;
    }

    uint32_t code[] = {
        0xD2800540,  // mov x0, #42
        0xD65F03C0,  // ret
    };

    size_t size = JIT_PAGE_SIZE;
    mach_port_t task = mach_task_self();

    // Step 1: Let StikDebug allocate RX pages via _M command (x0=0)
    jit_log("Requesting debugger to allocate %zu bytes of RX memory (x0=0)...", size);
    void *rx_ptr = jit26_prepare_region(NULL, size);
    jit_log("Debugger allocated RX at: %p", rx_ptr);

    if (!rx_ptr) {
        jit_log("FAIL: Debugger allocation returned NULL");
        return -1;
    }

    jit_check_page_protection(rx_ptr, "Debugger-allocated RX page");

    // Step 2: Use vm_remap to create a second view of the same pages (MeloNX approach)
    vm_address_t rw_addr = 0;
    vm_prot_t cur_prot = 0;
    vm_prot_t max_prot = 0;

    kern_return_t kr = vm_remap(
        task,
        &rw_addr,
        size,
        0,                              // mask
        VM_FLAGS_ANYWHERE,
        task,
        (vm_address_t)rx_ptr,           // source = debugger-allocated RX pages
        FALSE,                          // copy = false (share the pages)
        &cur_prot,
        &max_prot,
        VM_INHERIT_NONE
    );

    if (kr != KERN_SUCCESS) {
        jit_log("FAIL: vm_remap failed: %s (kr=%d)", mach_error_string(kr), kr);
        return -1;
    }

    jit_log("vm_remap succeeded: RW at %p (cur_prot=0x%x, max_prot=0x%x)",
            (void *)rw_addr, cur_prot, max_prot);

    // Step 3: Set the remapped view to RW (MeloNX does this)
    kr = vm_protect(task, rw_addr, size, FALSE, VM_PROT_READ | VM_PROT_WRITE);
    if (kr != KERN_SUCCESS) {
        jit_log("FAIL: vm_protect(RW) failed: %s (kr=%d)", mach_error_string(kr), kr);
        vm_deallocate(task, rw_addr, size);
        return -1;
    }

    jit_log("Set remapped view to RW");
    jit_check_page_protection((void *)rw_addr, "Remapped RW view");
    jit_check_page_protection(rx_ptr, "Original RX view after remap");

    // Step 4: Write code to RW view
    jit_log("Writing ARM64 code (%zu bytes) to RW view at %p", sizeof(code), (void *)rw_addr);
    memcpy((void *)rw_addr, code, sizeof(code));
    sys_icache_invalidate(rx_ptr, sizeof(code));

    // Verify coherence: read from RX should show what we wrote to RW
    uint32_t readback = *(uint32_t *)rx_ptr;
    jit_log("Coherence check: wrote 0x%x to RW, read 0x%x from RX %s",
            code[0], readback, readback == code[0] ? "(OK)" : "(MISMATCH!)");

    // Step 5: Execute from debugger-allocated RX
    jit_log("Executing from debugger RX at %p...", rx_ptr);
    typedef int64_t (*jit_func_t)(void);
    int64_t result = ((jit_func_t)rx_ptr)();
    jit_log("Result: %lld (expected 42)", result);

    if (result == 42) {
        jit_log("SUCCESS: Strategy 2 (debugger alloc + vm_remap) works!");

        // Additional test: add function
        uint32_t add_code[] = {
            0x8B010000,  // add x0, x0, x1
            0xD65F03C0,  // ret
        };
        memcpy((void *)(rw_addr + sizeof(code)), add_code, sizeof(add_code));
        sys_icache_invalidate((char *)rx_ptr + sizeof(code), sizeof(add_code));

        typedef int64_t (*add_func_t)(int64_t, int64_t);
        int64_t add_result = ((add_func_t)((char *)rx_ptr + sizeof(code)))(100, 200);
        jit_log("add(100, 200) = %lld (expected 300)", add_result);
    }

    // Cleanup
    vm_deallocate(task, rw_addr, size);

    if (result == 42) {
        jit_log("=== JIT tests passed (strategy 2) ===");
        return 42;
    }

    jit_log("=== All JIT strategies failed ===");
    return result;
}

/* ============================================================================
 * ml748 -- W^X A/B PROBE: does PROT_WRITE survive on FILE-BACKED image pages?
 *
 * Loading xtajit64.dll on the jailbroken research VM faults writing its .rdata
 * (off=0x207000, kr=2 PROTECTION_FAILURE, prot=1 max=7) during the ARM64EC
 * hybrid-metadata / TLS-index fixups. The identical build loads it fine on the
 * iPhone 13 Pro. The fault reproduced with the debugger FULLY ATTACHED, so
 * CS_DEBUGGED being live is not the variable.
 *
 * Two explanations remain and reasoning cannot separate them:
 *   (a) the VM is STRICTER than a real CS_DEBUGGED device, because its
 *       patchVmMapProtect() was removed and that patch is what forced W to
 *       stick on file-backed pages. Then hardware keeps W, the VM strips it,
 *       and this is a false failure local to the VM.
 *   (b) hardware masks a GENUINE portability bug some other way, both strip W,
 *       and the loader's reliance on holding RWX over image pages is wrong.
 *
 * Run the SAME binary in BOTH places and compare. This must live inside
 * Madeira, not in a standalone tool: the 13 Pro has no root, so an arbitrary
 * CLI cannot run there at all, and a platform binary over SSH already gave a
 * misleading answer once (it reported mprotect(RWX) succeeding while Madeira in
 * its real container saw the opposite). Same process, same sandbox, same
 * cs_wx_enabled map, or it proves nothing.
 *
 * THE READBACK IS THE ANSWER, NOT THE RETURN CODE. The failure signature is a
 * call that reports success while W is silently stripped, so every case reads
 * protection AND max_protection back via mach_vm_region.
 * ==========================================================================*/

static void wxprobe_readback(const char *tag, void *addr, int rc, int err) {
    mach_vm_address_t a = (mach_vm_address_t)(uintptr_t)addr;
    mach_vm_size_t sz = 0;
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t cnt = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t obj = MACH_PORT_NULL;
    kern_return_t kr = mach_vm_region(mach_task_self(), &a, &sz,
                                      VM_REGION_BASIC_INFO_64,
                                      (vm_region_info_t)&info, &cnt, &obj);
    if (kr != KERN_SUCCESS) {
        jit_log("[wx-probe] ml748 %-22s rc=%d errno=%d  region=UNREADABLE kr=%d",
                tag, rc, err, kr);
        return;
    }
    /* W_KEPT is the whole verdict: the call may have returned 0 and still lost W. */
    jit_log("[wx-probe] ml748 %-22s rc=%d errno=%-3d prot=%d max=%d  [R=%d W=%d X=%d]  W_KEPT=%s",
            tag, rc, err, info.protection, info.max_protection,
            !!(info.protection & VM_PROT_READ),
            !!(info.protection & VM_PROT_WRITE),
            !!(info.protection & VM_PROT_EXECUTE),
            (info.protection & VM_PROT_WRITE) ? "YES" : "NO");
}

void jit_wx_probe(void) {
    const size_t len = JIT_PAGE_SIZE * 4;

    jit_log("[wx-probe] ml748 BEGIN  CS_DEBUGGED=%d pagesize=%d",
            (int)jit_check_debugged(), (int)JIT_PAGE_SIZE);

    /* A file-backed PRIVATE mapping is the shape that actually fails: PE images
     * are mapped from the container, not allocated anonymously. Map our own
     * executable -- guaranteed present and readable in both environments. */
    const char *self = "/proc/self/exe";
    int fd = open(self, O_RDONLY);
    if (fd < 0) {
        /* No procfs on iOS; any readable file in the bundle serves the purpose. */
        extern int _NSGetExecutablePath(char *, uint32_t *);
        char path[4096]; uint32_t psz = sizeof(path);
        if (_NSGetExecutablePath(path, &psz) == 0)
            fd = open(path, O_RDONLY);
    }
    if (fd < 0) {
        jit_log("[wx-probe] ml748 ABORT: no file to map (errno=%d)", errno);
        return;
    }

    /* --- Cases 1-3: FILE-BACKED. This is the shape that faults. --- */
    for (int c = 1; c <= 3; c++) {
        void *p = mmap(NULL, len, PROT_READ, MAP_PRIVATE | MAP_FILE, fd, 0);
        if (p == MAP_FAILED) {
            jit_log("[wx-probe] ml748 case%d mmap FAILED errno=%d", c, errno);
            continue;
        }
        int rc, err;
        if (c == 1) {
            rc = mprotect(p, len, PROT_READ | PROT_WRITE); err = errno;
            wxprobe_readback("1 file mprotect RW", p, rc, err);
        } else if (c == 2) {
            /* Exactly what the loader does today. */
            rc = mprotect(p, len, PROT_READ | PROT_WRITE | PROT_EXEC); err = errno;
            wxprobe_readback("2 file mprotect RWX", p, rc, err);
        } else {
            /* VM_PROT_COPY forces a private writable copy of a file-backed page
             * instead of asking to write through the shared mapping -- the
             * mechanism the RW-window fix would rely on, and the one that should
             * remain legal under an ENFORCING W^X. */
            kern_return_t kr = mach_vm_protect(mach_task_self(),
                                               (mach_vm_address_t)(uintptr_t)p, len, FALSE,
                                               VM_PROT_COPY | VM_PROT_READ | VM_PROT_WRITE);
            wxprobe_readback("3 file vm_protect COPY", p, (int)kr, 0);
        }
        munmap(p, len);
    }
    close(fd);

    /* --- Case 4: ANONYMOUS control. If anon keeps W|X and file-backed does not,
     * the constraint is file backing, not W^X in general. --- */
    void *a = mmap(NULL, len, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANON, -1, 0);
    if (a != MAP_FAILED) {
        int rc = mprotect(a, len, PROT_READ | PROT_WRITE | PROT_EXEC);
        wxprobe_readback("4 anon mprotect RWX", a, rc, errno);
        munmap(a, len);
    } else {
        jit_log("[wx-probe] ml748 case4 anon mmap FAILED errno=%d", errno);
    }

    jit_log("[wx-probe] ml748 END");
}


/* ml1040: CLAIM THE LOW GAP AT IMAGE LOAD.
 *
 * On the iPhone 18 Pro everything that must live low -- the 0x140000000 window
 * for a non-relocatable main image, and the RX JIT pool, which the debugger
 * places first-fit with no address hint -- competes for one gap of roughly
 * 1.4GB between the app image and the malloc zones. By the time the user taps
 * launch, our own process has allocated into that gap, differently every time:
 *
 *   launch A:  505MB | window | 628MB | 261MB            -> 608MB pool
 *   launch B:  476MB | window | 604MB | 286MB            -> 592MB pool
 *   launch C:  463MB | window | 349MB | 181MB | 329MB    -> 448MB pool, and the
 *              image head alone filled it (397MB) before the game started:
 *              "[jit-pool] EXHAUSTED ... FAILING the load" x4, shell32 then ran
 *              from its un-pooled address (AV EXEC) and the process died.
 *
 * In launch C a 31MB runtime allocation had landed at 0x15dd00000, in the
 * middle of the hole the pool needs. We cannot stop the runtime allocating, but
 * we can get there first: a constructor runs before main, before SwiftUI, Metal
 * or the log store exist. Hold the window, then hold the largest contiguous run
 * directly above it. Both are PROT_NONE reservations and cost no memory.
 * StikJITHelper releases the pool placeholder immediately before asking the
 * debugger for RX, and plugs any lower hole that would win first-fit. */
#include <mach/mach.h>
unsigned long madeira_early_window_base, madeira_early_window_size;
unsigned long madeira_early_pool_base, madeira_early_pool_size;
unsigned long madeira_early_intruder_base, madeira_early_intruder_size;   /* ml1135 */
unsigned madeira_early_intruder_tag, madeira_early_intruder_prot;

static int bcd_pool_reserve_at_load(void);   /* madeira-bcd pool-reserve, below */
/* Read-only views of the above, for callers that cannot take a mutable global.
 * The values are written once by the constructor below, before main runs, and
 * never change afterwards -- so this is an access-shape fix, not a lock. */
void madeira_early_pool_placeholder(unsigned long *base, unsigned long *size) {
    if (base) *base = madeira_early_pool_base;
    if (size) *size = madeira_early_pool_size;
}

void madeira_early_pool_intruder(unsigned long *base, unsigned long *size, unsigned *tag) {
    if (base) *base = madeira_early_intruder_base;
    if (size) *size = madeira_early_intruder_size;
    if (tag)  *tag  = madeira_early_intruder_tag;
}

__attribute__((constructor(101), used)) static void madeira_early_va_claim(void)
{
    const vm_address_t win = 0x140000000ul, winsz = 0x8000000ul;       /* 128MB, see ml1037 */
    vm_address_t a = win;
    vm_size_t sz;

    if (bcd_pool_reserve_at_load()) return;   /* madeira-bcd pool-reserve: the window is inside the reservation (only a build with BCD_POOL_BSS_MB) */
    if (vm_allocate(mach_task_self(), &a, winsz, VM_FLAGS_FIXED) == KERN_SUCCESS && a == win) {
        vm_protect(mach_task_self(), a, winsz, 0, VM_PROT_NONE);
        madeira_early_window_base = win; madeira_early_window_size = winsz;
    }
    /* Largest no-overwrite run starting at the window's end, 16MB granularity. */
    for (sz = 1024ul << 20; sz >= (256ul << 20); sz -= (16ul << 20)) {
        a = win + winsz;
        if (vm_allocate(mach_task_self(), &a, sz, VM_FLAGS_FIXED) == KERN_SUCCESS && a == win + winsz) {
            vm_protect(mach_task_self(), a, sz, 0, VM_PROT_NONE);
            madeira_early_pool_base = a; madeira_early_pool_size = sz;
            break;
        }
    }
    /* ml1135: NAME what blocked it. ph-rdr90 got no placeholder because a ~31MB
     * mapping already sat at ~0x157d00000 before this constructor ran, leaving
     * 253MB above the window; the pool fell back to a 432MB hole below it and FEX
     * rolled its code cache over 52 times (a ~1 s freeze each). Record the first
     * mapped region above the window so the log can say what it is.
     * madeira-bcd: also when a placeholder was obtained -- it ends where the first
     * mapping above it starts (normally the main thread's stack, VM tag 30; on
     * RDR2's build 470 run a 71MB mapping at 0x159d00000 cut it to 272MB and the
     * pool to 604MB). */
    {
        vm_address_t ra = win + winsz + (vm_address_t)madeira_early_pool_size;
        vm_size_t rs = 0;
        natural_t depth = 0;
        vm_region_submap_info_data_64_t info;
        mach_msg_type_number_t cnt = VM_REGION_SUBMAP_INFO_COUNT_64;
        if (vm_region_recurse_64(mach_task_self(), &ra, &rs, &depth, (vm_region_recurse_info_t)&info, &cnt) == KERN_SUCCESS
            && ra < 0x190000000ul) {
            madeira_early_intruder_base = ra; madeira_early_intruder_size = rs;
            madeira_early_intruder_tag = info.user_tag; madeira_early_intruder_prot = (unsigned)info.protection;
        }
    }
}

/* ============================================================================
 * madeira-bcd pool-reserve (Modified by BCD (thebcd69), 2026-10-18): THE JIT
 * POOL'S ADDRESS RANGE RESERVED AT EXEC TIME. The method is described in
 * docs/research/open-fork-2026-10-18/playport.md §3 P1.2; this code is BCD's own.
 *
 * ml1040 above claims the window and a placeholder in a constructor, but by then
 * dyld, libmalloc's start-up regions and other start-up mappings are already
 * placed: ml1135 named a 31 MB mapping at 0x157d00000, RDR2's build 470 a 71 MB
 * one at 0x159d00000 (pool 604 MB), launch C a 31 MB one at 0x15dd00000 (pool
 * exhausted, crash). A zero-fill segment of the main executable is mapped by the
 * kernel together with the image, before any of them, so nothing can land in it;
 * untouched it has no resident pages and no footprint.
 *
 * It is a SEGMENT of its own (__BCDPOOL), not an array in __bss: ld always puts a
 * custom segment right after __DATA, so its offset in the image is fixed (__TEXT +
 * __DATA_CONST + __DATA, 73 MB in build 571), while an array in __bss lands
 * wherever the link order puts it.
 *
 * NOT in the default build. The array's presence alone moves every start-up
 * mapping (dyld goes right after the image's end, libmalloc's regions first-fit
 * above it), so no runtime switch could make a build that carries it lay out its
 * address space as today. BCD_POOL_BSS_MB = 0 (the default) compiles none of
 * it: bcd_pool_reserve_at_load() returns 0 and the constructor above runs as
 * before. A reservation build is made with the archive step's environment
 * variable BCD_POOL_BSS_MB (build-ipa.yml); 1152 is the planned size:
 *   - 1152 MB covers the executable window [0x140000000, 0x148000000) at every
 *     slide and image size (the image starts at 0x100000000 or above);
 *   - the image's end at the largest slide (80 MB) is then about 0x152d00000,
 *     ~370 MB under the lowest main-thread stack mapping the alpha logs show
 *     (0x16b000000 at a 76.8 MB slide); the kernel creates the stack at a fixed
 *     address after mapping the image and dyld, so an image reaching it would
 *     end the exec. tests/host/check-pool-reserve.py bounds the size.
 *
 * In such a build, bcd.cfg's pool-bss is read here (the game is not known yet):
 *   0      release it all now; ml1040 runs on what is left;
 *   other  keep it, PROT_NONE, the window included (exported as before), until
 *          the pool request. That decides per game (StikJITHelper, PoolReserve):
 *          pool-bss = 1 takes the pool's slice at the reservation's start, anything
 *          else gives all of it back but the window and places the pool as before.
 * ==========================================================================*/
#ifndef BCD_POOL_BSS_MB
#define BCD_POOL_BSS_MB 0
#endif

unsigned long bcd_pool_res_built;              /* bytes compiled in; 0: this build has no reservation */
unsigned long bcd_pool_res_base;               /* where the reservation starts (also after it is given back) */
int bcd_pool_res_state;                        /* BCD_POOL_RES_* */
int bcd_pool_res_load_setting = -1;            /* bcd.cfg pool-bss at image load: -1 unset, 0, 1 */
const char *bcd_pool_res_refusal = "";         /* why a reservation was not kept at load */
unsigned long bcd_pool_res_stack_lo, bcd_pool_res_stack_hi;   /* the main thread's stack mapping at load */
unsigned long bcd_pool_res_image_end;          /* the end of the main executable's last segment */

/* The pool's slice of a reservation [res_base, +res_size): it starts at the reservation's start, or at
 * floor_addr when that is higher (16 KB pages), ends at the window or at the reservation's end, and
 * holds `wanted` bytes plus `slack` (that absorbs a small mapping another thread makes at the slice's
 * start during the request; cut at the window). A pool that does not fit is shrunk to 16 MB steps;
 * under min_pool there is no plan (returns 0). Pure: tests/host/check-pool-reserve.py. */
int bcd_pool_res_plan(unsigned long res_base, unsigned long res_size, unsigned long win_base,
                      unsigned long win_size, unsigned long floor_addr, unsigned long wanted,
                      unsigned long slack, unsigned long min_pool, bcd_pool_res_plan_t *out)
{
    const unsigned long page = 0x4000ul, step = 16ul << 20;
    unsigned long res_end = res_base + res_size, start, end, avail, pool;

    memset(out, 0, sizeof(*out));
    if (!res_size || res_end < res_base) return 0;
    start = res_base > floor_addr ? res_base : floor_addr;
    start = (start + page - 1) & ~(page - 1);
    if (win_size && start >= win_base && start < win_base + win_size) start = win_base + win_size;   /* never in the window */
    end = res_end;
    if (win_size && win_base >= start && win_base < end) end = win_base;      /* the window stays out of the slice */
    if (start >= end) return 0;
    avail = (end - start) & ~(page - 1);
    pool = (wanted + page - 1) & ~(page - 1);
    if (pool > avail) pool = avail & ~(step - 1);
    if (!pool || pool < min_pool) return 0;
    out->pool_size = pool;
    out->slice_base = start;
    out->slice_size = avail - pool > slack ? pool + slack : avail;
    return 1;
}

/* What stays held of the reservation once the slice [slice_base, +slice_size) is released (slice_size 0:
 * no slice): everything but the slice and the window, at most 3 ranges, written as base and size to
 * out[i][0] and out[i][1]. Pure: the host check. */
int bcd_pool_res_rest(unsigned long res_base, unsigned long res_size, unsigned long win_base,
                      unsigned long win_size, unsigned long slice_base, unsigned long slice_size,
                      unsigned long out[3][2])
{
    unsigned long cut[2][2], res_end = res_base + res_size, at = res_base;
    int n = 0, c = 0, i;

    if (slice_size) { cut[c][0] = slice_base; cut[c][1] = slice_base + slice_size; c++; }
    if (win_size) { cut[c][0] = win_base; cut[c][1] = win_base + win_size; c++; }
    if (c == 2 && cut[1][0] < cut[0][0]) {           /* address order */
        unsigned long t0 = cut[0][0], t1 = cut[0][1];
        cut[0][0] = cut[1][0]; cut[0][1] = cut[1][1]; cut[1][0] = t0; cut[1][1] = t1;
    }
    for (i = 0; i <= c && n < 3; i++) {
        unsigned long lo = at, hi = i < c ? cut[i][0] : res_end;
        if (hi > res_end) hi = res_end;
        if (hi > lo) { out[n][0] = lo; out[n][1] = hi - lo; n++; }
        if (i < c && cut[i][1] > at) at = cut[i][1];
    }
    return n;
}

/* Release [base, +size) of the reservation and confirm that the range is free now: 0, else the
 * vm_deallocate error, or -1 when something is still mapped in it (the kernel kept the entry). */
int bcd_pool_res_release(unsigned long base, unsigned long size)
{
    mach_vm_address_t a = base;
    mach_vm_size_t rs = 0;
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t cnt = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t obj = MACH_PORT_NULL;
    kern_return_t kr;

    if (!size) return 0;
    kr = vm_deallocate(mach_task_self(), (vm_address_t)base, (vm_size_t)size);
    if (kr != KERN_SUCCESS) return kr;
    kr = mach_vm_region(mach_task_self(), &a, &rs, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info, &cnt, &obj);
    if (kr == KERN_SUCCESS && a < base + size) return -1;
    return 0;
}

/* Give back what is still held but the window (the slice, if any, is already free); the state becomes
 * `state`. Returns how many parts did not come free. */
int bcd_pool_res_give_back(unsigned long slice_base, unsigned long slice_size, int state)
{
    unsigned long parts[3][2];
    int n, i, bad = 0;

    if (bcd_pool_res_state != BCD_POOL_RES_HELD) return 0;
    n = bcd_pool_res_rest(bcd_pool_res_base, bcd_pool_res_built, BCD_POOL_RES_WIN, BCD_POOL_RES_WIN_SIZE,
                          slice_base, slice_size, parts);
    for (i = 0; i < n; i++)
        if (bcd_pool_res_release(parts[i][0], parts[i][1]) != 0) bad++;
    bcd_pool_res_state = bad ? BCD_POOL_RES_STUCK : state;
    return bad;
}

/* The main thread's stack mapping (the VM_MEMORY_STACK regions running down from its top, guard pages
 * included) and the end of the main executable, for the log: the image must stay under the stack. */
static void bcd_pool_res_note_layout(void)
{
    const struct mach_header_64 *mh = (const struct mach_header_64 *)_dyld_get_image_header(0);
    unsigned long top = (unsigned long)pthread_get_stackaddr_np(pthread_self());
    unsigned long lo = top - (unsigned long)pthread_get_stacksize_np(pthread_self());
    vm_address_t a = top > (64ul << 20) ? top - (64ul << 20) : 0;

    while (a < top) {
        vm_size_t rs = 0;
        natural_t depth = 0;
        vm_region_submap_info_data_64_t info;
        mach_msg_type_number_t cnt = VM_REGION_SUBMAP_INFO_COUNT_64;
        if (vm_region_recurse_64(mach_task_self(), &a, &rs, &depth, (vm_region_recurse_info_t)&info, &cnt) != KERN_SUCCESS
            || a >= top || !rs) break;
        if (info.user_tag == VM_MEMORY_STACK && a < lo && a + rs >= lo) lo = a;
        a += rs;
    }
    bcd_pool_res_stack_lo = lo;
    bcd_pool_res_stack_hi = top;
    if (mh && mh->magic == MH_MAGIC_64) {
        const struct load_command *lc = (const struct load_command *)(mh + 1);
        unsigned long slide = (unsigned long)_dyld_get_image_vmaddr_slide(0), end = 0;
        for (uint32_t i = 0; i < mh->ncmds; i++, lc = (const struct load_command *)((const char *)lc + lc->cmdsize)) {
            const struct segment_command_64 *seg = (const struct segment_command_64 *)lc;
            if (lc->cmd != LC_SEGMENT_64 || !seg->vmsize || !strcmp(seg->segname, "__PAGEZERO")) continue;
            if (seg->vmaddr + seg->vmsize + slide > end) end = seg->vmaddr + seg->vmsize + slide;
        }
        bcd_pool_res_image_end = end;
    }
}

/* madeira-bcd pool-floor (P1.3): FEX writes every byte of code through the pool's RW alias
 * (Buffer.h WritePtr, WriteOffset = WINE_IOS_JIT_RW - WINE_IOS_JIT_RX), which is what made the
 * 0x119000000 "mode A" floor obsolete elsewhere. The floor is dropped only when that alias works:
 * a word written through RW at the pool's first and last 16 bytes must read back through RX. The
 * old bytes are put back. 1 passed; 0 failed, with `detail` saying where. */
int bcd_pool_rw_rx_selftest(const void *rx, void *rw, unsigned long size, char *detail, unsigned long detail_len)
{
    static const uint64_t pattern[2] = { 0x4243445f706f6f6cull, 0x6c6f6f705f444342ull };   /* "BCD_pool" both ways */
    unsigned long offs[2];
    int i;

    if (!rx || !rw || rx == (const void *)rw || size < 64) {
        snprintf(detail, detail_len, "no RW alias apart from RX (rx=%p rw=%p size=%lu)", rx, (void *)rw, size);
        return 0;
    }
    offs[0] = 8;
    offs[1] = (size - 16) & ~7ul;
    for (i = 0; i < 2; i++) {
        volatile uint64_t *w = (volatile uint64_t *)((char *)rw + offs[i]);
        const volatile uint64_t *r = (const volatile uint64_t *)((const char *)rx + offs[i]);
        uint64_t old = *w, seen;
        *w = pattern[i];
        __atomic_thread_fence(__ATOMIC_SEQ_CST);
        seen = *r;
        *w = old;
        if (seen != pattern[i]) {
            snprintf(detail, detail_len, "wrote 0x%llx through RW at +0x%lx, read 0x%llx through RX",
                     (unsigned long long)pattern[i], offs[i], (unsigned long long)seen);
            return 0;
        }
    }
    snprintf(detail, detail_len, "a word written through RW read back through RX at +0x8 and +0x%lx", offs[1]);
    return 1;
}

/* Decide at image load what happens to a reservation [base, +size) (the build's, or the host check's
 * own): 1 when it is kept for the pool request (the window inside it is then the one ml1040 would
 * have held), 0 when it was given back. */
int bcd_pool_res_keep(unsigned long base, unsigned long size)
{
    char v[32];

    bcd_pool_res_built = size;
    bcd_pool_res_base = base;
    bcd_pool_res_note_layout();
    // Keeps the JIT pool's address range reserved from app start (only in a build made with BCD_POOL_BSS_MB): a game with pool-bss = 1 gets its pool there, below the executable window; 0 in bcd.cfg gives it back at start. Off by default.
    if (madeira_cfg_get("pool-bss", v, sizeof v)) bcd_pool_res_load_setting = madeira_cfg_bool("pool-bss", 0);
    if (bcd_pool_res_load_setting == 0) {
        bcd_pool_res_refusal = "pool-bss = 0 in bcd.cfg";
        bcd_pool_res_state = bcd_pool_res_release(base, size) == 0 ? BCD_POOL_RES_RELEASED : BCD_POOL_RES_STUCK;
        return 0;
    }
    if (base < 0x100000000ul || base + (256ul << 20) > BCD_POOL_RES_WIN
        || base + size < BCD_POOL_RES_WIN + BCD_POOL_RES_WIN_SIZE) {
        bcd_pool_res_refusal = "the executable window is not inside it, or less than 256 MB of it lies below the window";
        bcd_pool_res_state = bcd_pool_res_release(base, size) == 0 ? BCD_POOL_RES_REFUSED : BCD_POOL_RES_STUCK;
        return 0;
    }
    /* Never touched; PROT_NONE like ml1040's holds, the window part included. */
    vm_protect(mach_task_self(), (vm_address_t)base, (vm_size_t)size, 0, VM_PROT_NONE);
    madeira_early_window_base = BCD_POOL_RES_WIN;
    madeira_early_window_size = BCD_POOL_RES_WIN_SIZE;
    bcd_pool_res_state = BCD_POOL_RES_HELD;
    return 1;
}

#if BCD_POOL_BSS_MB > 0
_Static_assert(BCD_POOL_BSS_MB >= 1152 && BCD_POOL_BSS_MB <= 1408,
               "BCD_POOL_BSS_MB: 1152 (covers the executable window at every slide) to 1408 (room under the stack)");

/* The reservation. Its name is what dladdr gives a JIT pool pc that lies in it. */
__attribute__((used, aligned(0x4000), section("__BCDPOOL,__reserve,zerofill")))
unsigned char bcd_jit_pool_reservation[(unsigned long)BCD_POOL_BSS_MB << 20];

static int bcd_pool_reserve_at_load(void)
{
    return bcd_pool_res_keep((unsigned long)bcd_jit_pool_reservation, sizeof(bcd_jit_pool_reservation));
}
#else
static int bcd_pool_reserve_at_load(void) { return 0; }
#endif

/* ml1249: METAL API VALIDATION ON DEMAND.
 *
 * `metal-validation = 1` in Documents/bcd.cfg turns on Metal's own debug
 * layer for this launch, reporting instead of aborting, and routes NSLog to
 * stderr so the reports land in the Madeira log. It has to be in the
 * environment before the first MTLDevice exists, and the app makes one for its
 * CAMetalLayer long before bcd.cfg is otherwise read -- hence a
 * constructor. Expensive: diagnostic runs only. */
__attribute__((constructor(102), used)) static void madeira_metal_validation_opt_in(void)
{
    /* Metal API validation for this launch (debug layer, reports instead of aborts). */
    if (!madeira_cfg_bool("metal-validation", 0)) return;
    setenv("MTL_DEBUG_LAYER", "1", 1);
    setenv("MTL_DEBUG_LAYER_ERROR_MODE", "nslog", 1);
    setenv("MTL_DEBUG_LAYER_WARNING_MODE", "nslog", 1);
    setenv("CFLOG_FORCE_STDERR", "1", 1);
}
