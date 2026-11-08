/* Portions copyright 2026 SirDank: code taken from https://github.com/willfaust/Madeira (pull request #362)
 * under GPL-3.0-or-later and modified for BCD in October 2026. See THIRD-PARTY-NOTICES.md. */
#ifndef JIT_ALLOCATOR_H
#define JIT_ALLOCATOR_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Opaque handle to a dual-mapped JIT region
typedef struct JITRegion JITRegion;

// Create a dual-mapped JIT region of the given size.
// Returns NULL on failure. Size is rounded up to page boundary.
// The region has two views of the same physical memory:
//   - RW view: for writing generated code
//   - RX view: for executing generated code
JITRegion *jit_region_create(size_t size);

// Destroy a JIT region and unmap both views.
void jit_region_destroy(JITRegion *region);

/// Mark an already-mapped range jetsam-exempt (VM_LEDGER_FLAG_NO_FOOTPRINT).
/// For the production pool, whose pages come from the debugger rather than
/// jit_region_create(). Logs phys_footprint either side; returns false and
/// changes nothing if the private ownership API refuses.
bool jit_make_region_no_footprint(void *addr, size_t size, const char *label);

// Get the RW (writable) pointer. Write generated code here.
void *jit_region_rw_ptr(JITRegion *region);

// Get the RX (executable) pointer. Execute code from here.
void *jit_region_rx_ptr(JITRegion *region);

// Get the total size of the region.
size_t jit_region_size(JITRegion *region);

// Write code to the region at the given offset.
// Handles cache invalidation automatically.
// Returns the RX pointer to the written code (for execution).
void *jit_region_write(JITRegion *region, size_t offset, const void *code, size_t code_size);

// Invalidate instruction cache for a range in the RX view.
void jit_region_invalidate(JITRegion *region, size_t offset, size_t size);

// Check if CS_DEBUGGED flag is set (JIT execution is allowed).
// Returns true if the debugger has attached and set the flag.
bool jit_check_debugged(void);

/// CS_DEBUGGED without the log line — for the badge's 2s poll.
bool jit_is_debugged_quiet(void);

// Install SIGTRAP handler so BRK instructions don't crash the app
// when no debugger is attached. Must be called before any jit26_* functions.
// madeira-bcd ios18-jit: also when CS_DEBUGGED is already set (an app started
// debugged), unless `when_debugged` is false (MADEIRA_JIT_TRAP_FALLBACK=0, the
// old rule). An attached debugger still receives every BRK first.
void jit_install_trap_handler(bool when_debugged);

// Install the same SIGTRAP handler although CS_DEBUGGED is set. The flag stays
// set after a debugger detaches, so it does not say that anything will answer a
// BRK; with the handler armed an unanswered jit26_* request returns 0 instead of
// killing the app. A debugger that is attached still receives the BRK first.
void jit_arm_trap_fallback(void);

// The process's code-signing status flags (csops CS_OPS_STATUS), without logging.
// Returns false when the kernel does not report them.
bool jit_cs_status(uint32_t *flags);

// The user address map [min, max) from TASK_VM_INFO: max is 0xfc0000000 (63 GB)
// on a standard map and 0x8000000000 (512 GB) on an extended one. Returns false
// when the kernel does not report it.
bool jit_task_map_range(uint64_t *min_address, uint64_t *max_address);

// Memory this process may still allocate before the system's limit.
uint64_t jit_available_memory(void);

// This process's phys_footprint (TASK_VM_INFO), 0 when the kernel does not report it.
// With jit_available_memory() it gives the memory limit measured at launch.
uint64_t jit_phys_footprint(void);

// madeira-bcd ios18-jit (2026-10-18): JIT memory without a debugger, for where TXM
// does not police code (iOS 18; iOS 26 without TXM) and CS_DEBUGGED is set
// (StikJITHelper.swift, JITNoDebuggerPool, decides; never on a TXM device).
//
// One page made the way the pool's regions are made, written through its RW view
// and executed through its RX view (returns 42). A fault ends the test, not the
// app. `detail` says what happened. Logged as [jit-nodebug] self-test.
bool jit_nodebug_selftest(char *detail, size_t detail_len);
// A pool region of `size` bytes: a named memory entry (max RWX, NO_FOOTPRINT),
// mapped RX where the kernel's first fit puts it, as the debugger's _M request
// would. Returns the RX base and the entry in `entry_out` (for the RW alias and
// then jit_nodebug_release), or NULL.
void *jit_nodebug_region(size_t size, unsigned int *entry_out);
// The RW alias of such a region: the entry mapped again, read-write (max RWX), at
// `*address` with vm_map `flags` (ANYWHERE with a hint, FIXED, FIXED|OVERWRITE),
// in place of vm_remap. Returns the kern_return_t; `*address` gets the base.
int jit_nodebug_map_alias(unsigned int entry, uintptr_t *address, size_t size, int flags);
// Drops the entry's port once its views are mapped (or its region was given back).
void jit_nodebug_release(unsigned int entry);
// Whether the device tree lists TXM (StikJIT's test): 1 yes, 0 no, -1 unknown.
int jit_txm_in_device_tree(void);

// iOS 26 BRK-based protocol: Ask attached debugger (StikDebug) to
// prepare a memory region for JIT execution.
// Returns the prepared address (may differ from input on allocation).
void *jit26_prepare_region(void *addr, size_t len);

// iOS 26 BRK-based protocol: Tell the debugger to detach.
void jit26_detach(void);

// Test if dual-mapped regions can be created and RX pages are viable,
// WITHOUT actually executing generated code (safe to call without JIT).
// Returns true if dual mapping works and RX pages have execute permission.
bool jit_test_mapping(void);

// Full JIT test: tries Strategy 1 (write-then-prepare) then Strategy 2 (debugger alloc).
// Returns 42 on success, -1 on failure, -2 if no debugger attached, -3 if fault loop.
int64_t jit_test_execute(void);

// Strategy 2 only: Let debugger allocate RX via _M, dual-map RW on top.
// Returns 42 on success, -1 on failure, -2 if no debugger, -3 if fault loop.
int64_t jit_test_execute_strategy2(void);

/// ml748: W^X A/B probe. Reports whether PROT_WRITE survives on FILE-BACKED
/// mappings, which is what the ARM64EC loader needs when it patches an image's
/// .rdata. Run the same build on the research VM and on hardware and compare:
/// if hardware keeps W and the VM strips it, the VM is stricter and the failure
/// is local to it; if both strip it, the loader's reliance on RWX over image
/// pages is a real portability bug. Call after JIT is enabled.
void jit_wx_probe(void);

// Log callback type
typedef void (*jit_log_callback_t)(const char *message);

// Set a log callback for JIT operations
void jit_set_log_callback(jit_log_callback_t callback);

#ifdef __cplusplus
}
#endif


/* ml1040: address space claimed by a constructor at image load, before the app's
 * own runtime allocations can fragment the scarce low gap. See JITAllocator.m. */
extern unsigned long madeira_early_window_base;   /* 0x140000000 if held, else 0 */
extern unsigned long madeira_early_window_size;
extern unsigned long madeira_early_pool_base;     /* placeholder directly above the window, else 0 */
extern unsigned long madeira_early_intruder_base, madeira_early_intruder_size;   /* ml1135: first mapping above the window (above the placeholder when one was taken) */
extern unsigned madeira_early_intruder_tag, madeira_early_intruder_prot;
extern unsigned long madeira_early_pool_size;

/* madeira-bcd pool-reserve (Modified by BCD (thebcd69), 2026-10-18): the JIT pool's range reserved
 * at exec time in a build made with BCD_POOL_BSS_MB (JITAllocator.c; none in the default build). */
#define BCD_POOL_RES_NONE     0   /* this build has no reservation */
#define BCD_POOL_RES_HELD     1   /* held since image load, for the pool request */
#define BCD_POOL_RES_RELEASED 2   /* given back at image load: pool-bss = 0 in bcd.cfg */
#define BCD_POOL_RES_REFUSED  3   /* given back at image load: the layout check failed */
#define BCD_POOL_RES_USED     4   /* the pool request took its slice; only the window is left of it */
#define BCD_POOL_RES_DROPPED  5   /* the pool request did not use it: all but the window given back */
#define BCD_POOL_RES_STUCK    6   /* a release left a mapping behind (the kernel kept the entry) */
#define BCD_POOL_RES_WIN      0x140000000ul   /* the executable window, as ml1040 (128 MB, ml1037) */
#define BCD_POOL_RES_WIN_SIZE 0x8000000ul
extern unsigned long bcd_pool_res_built;       /* bytes compiled in; 0: no reservation */
extern unsigned long bcd_pool_res_base;        /* its start, also after it was given back */
extern int bcd_pool_res_state;                 /* BCD_POOL_RES_* */
extern int bcd_pool_res_load_setting;          /* bcd.cfg pool-bss at image load: -1 unset, 0, 1 */
extern const char *bcd_pool_res_refusal;       /* why it was given back at image load */
extern unsigned long bcd_pool_res_stack_lo, bcd_pool_res_stack_hi;   /* the main thread's stack mapping at load */
extern unsigned long bcd_pool_res_image_end;   /* the end of the main executable's last segment */
typedef struct { unsigned long slice_base, slice_size, pool_size; } bcd_pool_res_plan_t;
#ifdef __cplusplus
extern "C" {
#endif
/* The pool's slice in [res_base, +res_size): from max(res_base, floor_addr), the window kept out, the
 * pool (shrunk to 16 MB steps when it does not fit, at least min_pool) plus slack. 0: no plan. */
int bcd_pool_res_plan(unsigned long res_base, unsigned long res_size, unsigned long win_base,
                      unsigned long win_size, unsigned long floor_addr, unsigned long wanted,
                      unsigned long slack, unsigned long min_pool, bcd_pool_res_plan_t *out);
/* The reservation less the slice and the window: up to 3 (base, size) ranges. */
int bcd_pool_res_rest(unsigned long res_base, unsigned long res_size, unsigned long win_base,
                      unsigned long win_size, unsigned long slice_base, unsigned long slice_size,
                      unsigned long out[3][2]);
/* vm_deallocate and confirm the range is free: 0, the kern_return_t, or -1 (still mapped). */
int bcd_pool_res_release(unsigned long base, unsigned long size);
/* Gives back everything still held but the window; the state becomes `state`. Returns the parts
 * that did not come free. */
int bcd_pool_res_give_back(unsigned long slice_base, unsigned long slice_size, int state);
/* Image load: keep [base, +size) for the pool request (1) or give it back (0), per bcd.cfg pool-bss
 * and the layout check. Called by the image-load constructor in a reservation build. */
int bcd_pool_res_keep(unsigned long base, unsigned long size);
/* pool-floor = 0 (P1.3): a word written through RW must read back through RX at the pool's first and
 * last 16 bytes (old bytes restored). 1 passed, 0 failed (`detail` says where). */
int bcd_pool_rw_rx_selftest(const void *rx, void *rw, unsigned long size, char *detail, unsigned long detail_len);
#ifdef __cplusplus
}
#endif
/* The same two facts, as functions. A C global `var` is shared mutable state
 * under Swift 6 strict concurrency and cannot be read at all from Swift; these
 * can. Both are written once by the constructor before main and are constant
 * thereafter, so no synchronisation is implied or needed. */
void madeira_early_pool_placeholder(unsigned long *base, unsigned long *size);
void madeira_early_pool_intruder(unsigned long *base, unsigned long *size, unsigned *tag);

#endif // JIT_ALLOCATOR_H
