#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""JIT readiness, the trap fallback and the debugger-less pool (ios18-jit, 2026-10-18;
docs/research/ios18-2026-10-18/runtime.md §2.3-2.5).

Source contracts (every host):
  - `ready` is CS_DEBUGGED plus P_TRACED (isDebuggerTraced), this run's pool, or the
    debugger-less pool; the badge's isDebuggerAttached() keeps CS_DEBUGGED (b0ab3a3);
  - the SIGTRAP fallback goes in at start-up also when CS_DEBUGGED is set (unless
    MADEIRA_JIT_TRAP_FALLBACK=0), and the pool request arms it whenever no debugger is
    traced and the pool is not built without one;
  - no exit() in the pool request; detachDebugger sends its BRK only to a traced debugger
    and sets MADEIRA_DETACHED either way;
  - every pool region and RW alias goes through requestRegion/remapRW, whose debugger
    branches are the calls the pool always made (jit26_prepare_region(nil, size); vm_remap,
    copy = false, VM_INHERIT_NONE);
  - StikDebug inside LiveContainer is not ruled out by canOpenURL;
  - Wine's plain mprotect(PROT_EXEC) attempts are skipped only with MADEIRA_NO_PLAIN_EXEC=1;
  - the pool size from the memory limit only with MADEIRA_POOL_FROM_LIMIT=1, never larger.

Swift (swiftc; --require-swift makes its absence a failure): the pure decisions, compiled
from the app's own source: StikDebug's script rule, the gate per OS and model (never on iOS
27, never on a TXM device), readiness, the attach-and-leave poll, the pool size per limit,
and the advice (iOS 27 keeps its exact text).

C (cc): JITAllocator.c's own functions, compiled for this Mac and run. The probe page and a
pool region made from a memory entry and mapped RX and RW; a page without EXECUTE ends the
self-test and not the process, with the old signal handlers back afterwards; a region lands
where the debugger's _M (mach_vm_allocate ANYWHERE) would, also past a plug; the RW alias with
ANYWHERE and with FIXED|OVERWRITE into a reservation; the trap handler's install rule; on an
arm64 Mac, BRK #0xf00d with the handler returns 0. The mapping tests run on Intel only: the
Dart VM reports kernel crashes from dual mappings on M4 Macs. With the iOS SDK,
JITAllocator.c also compiles for arm64-apple-ios18.0 with -Wall -Werror.
"""
from pathlib import Path
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
stik = (app / "StikJITHelper.swift").read_text()
ent = (app / "EntitlementChecker.swift").read_text()
content = (app / "ContentView.swift").read_text()
alloc = (app / "JITAllocator.c").read_text()
header = (app / "JITAllocator.h").read_text()
virtual = (root / "build/ntdll-unix/virtual_ios.c").read_text()

ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
ok = True


def check(label, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + label + (("\n     " + detail) if detail and not cond else ""))
    ok = ok and bool(cond)


def braced(text, signature):
    """`signature` up to its matching closing brace."""
    start = text.index(signature)
    i = text.index("{", start)
    depth = 0
    while True:
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
        if depth == 0:
            return text[start:i]


def c_function(text, signature):
    start = text.index(signature)
    return text[start:text.index("\n}", start) + 2] + "\n"


def code(text):
    """Swift text without its // comments (a // inside a string literal stays)."""
    out = []
    for line in text.splitlines():
        head = line.split("//")[0]
        out.append(line if head.count('"') % 2 else head)
    return "\n".join(out)


# ---- source contracts ---------------------------------------------------------------
ready = braced(stik, "static var ready: Bool")
check("ready: CS_DEBUGGED, then P_TRACED, this run's pool or the debugger-less pool (not the badge's reading)",
      "guard SigningStatus.current.debugged else { return false }" in ready
      and "traced: isDebuggerTraced()" in ready and "noDebuggerPool: JITNoDebuggerPool.usable" in ready
      and "isDebuggerAttached()" not in ready)
check("readiness: !attachCheck || poolTaken || traced || noDebuggerPool",
      "!attachCheck || poolTaken || traced || noDebuggerPool" in braced(stik, "static func readiness("))
attached = braced(ent, "func isDebuggerAttached() -> Bool")
traced = braced(ent, "func isDebuggerTraced() -> Bool")
check("the badge keeps CS_DEBUGGED first (b0ab3a3), P_TRACED second",
      attached.index("if jit_is_debugged_quiet() { return true }") < attached.index("return isDebuggerTraced()"))
check("isDebuggerTraced is the sysctl P_TRACED reading alone",
      "KERN_PROC_PID" in traced and "(info.kp_proc.p_flag & P_TRACED) != 0" in traced and "jit_is_debugged" not in traced)
check("flaggedWithoutDebugger is CS_DEBUGGED without `ready`",
      "static var flaggedWithoutDebugger: Bool { SigningStatus.current.debugged && !ready }" in stik)

install = c_function(alloc, "void jit_install_trap_handler(bool when_debugged)")
check("the trap handler goes in also with CS_DEBUGGED set; only when_debugged=false keeps the old skip",
      "if (debugged && !when_debugged) {" in install and install.count("return;") == 1
      and "sigaction(SIGTRAP, &sa, NULL);" in install)
check("JITAllocator.h declares the new signature", "void jit_install_trap_handler(bool when_debugged);" in header)
check("start-up installs it through StikJITHelper.installTrapHandler (MADEIRA_JIT_TRAP_FALLBACK)",
      "StikJITHelper.installTrapHandler()" in content and "jit_install_trap_handler(" not in content
      and 'jit_install_trap_handler(MadeiraConfig.flag("MADEIRA_JIT_TRAP_FALLBACK"))' in stik)

pool = braced(stik, "static func allocatePool(")
check("the pool request asks P_TRACED, and only asks JITNoDebuggerPool with CS_DEBUGGED and nothing traced",
      "let debuggerAttached = isDebuggerTraced()" in pool
      and "noDebugger = !debuggerAttached && SigningStatus.current.debugged && JITNoDebuggerPool.takeForPool()" in pool
      and "defer { releaseNoDebuggerEntries() }" in pool)
check("it arms the trap fallback whenever nothing is traced and the pool is the debugger's",
      'if !debuggerAttached && !noDebugger && MadeiraConfig.flag("MADEIRA_JIT_TRAP_FALLBACK") {' in pool
      and pool.index("jit_arm_trap_fallback()") < pool.index("requestRegion(poolSize, attempt: attempt)"))
check("no exit() in the pool request: a failure refuses the launch and keeps BCD up",
      "exit(" not in code(pool) and "Killing in 10s" not in pool and "exiting now" not in pool)
check("an unanswered request reads P_TRACED again before calling it 'no debugger'",
      "if requestUnanswered && !isDebuggerTraced() {" in pool and "poolFailure = poolNoDebuggerMessage" in pool)
counts = {k: code(stik).count(k) for k in ("jit26_prepare_region(", "vm_remap(", "requestRegion(", "remapRW(")}
check("no direct region request or vm_remap left in StikJITHelper outside the two helpers",
      counts == {"jit26_prepare_region(": 1, "vm_remap(": 1, "requestRegion(": 6, "remapRW(": 8}, str(counts))
request = braced(stik, "private static func requestRegion(")
remap = braced(stik, "private static func remapRW(")
# round 624 (agent R S2): the debugger branch records the request first (BCDBrkTrail, an fsynced file line) and still makes the same call
check("their debugger branches are the pool's old calls, unchanged (the request recorded first, round 624)",
      "guard noDebugger else {" in request and "return jit26_prepare_region(nil, size)" in request
      and request.index("BCDBrkTrail.begin(site: site, size: size, attempt: attempt)") < request.index("return jit26_prepare_region(nil, size)")
      and "return vm_remap(mach_task_self_, &address, size, 0, flags, mach_task_self_, rx, 0, &cur, &max, VM_INHERIT_NONE)" in remap
      and "if let region = noDebuggerEntries[rx] {" in remap)
detach = braced(stik, "static func detachDebugger()")
check("detachDebugger sends its BRK only to a traced debugger and sets MADEIRA_DETACHED either way",
      detach.index("guard isDebuggerTraced() else {") < detach.index("jit26_detach()")
      and detach.count('setenv("MADEIRA_DETACHED", "1", 1)') == 2)
available = braced(stik, "static var isAvailable: Bool")
check("LiveContainer: LC_HOME_PATH skips canOpenURL (the open decides)",
      available.index('getenv("LC_HOME_PATH") != nil { return true }') < available.index("canOpenURL"))
wait = braced(stik, "static func waitForDebugger(")
check("Enable JIT ends early on attach-and-leave (3 s) and on the deadline names it when CS_DEBUGGED is set",
      "attachPoll(ready: ready, flaggedAtStart: flaggedAtStart," in wait and "timeIntervalSince(since) >= 3" in wait
      and "SigningStatus.current.debugged && !isDebuggerTraced() ? .attachedAndLeft : .timedOut" in wait)
check("the gate never asks the device tree outside iOS 26",
      "let tree = osMajor == 26 && !simulator && !onMac ? Int(jit_txm_in_device_tree()) : -1" in stik)
check("the pool size from the limit: opt-in, never larger than the size it had",
      'MadeiraConfig.flag("MADEIRA_POOL_FROM_LIMIT", fallback: false)' in stik
      and "poolSizeMB = min(poolSizeMB, StikJITHelper.launchPoolMB(standard: 896))" in content
      and content.index("DockPerformancePolicy.sessionPoolMB(standard: 896,") < content.index("StikJITHelper.launchPoolMB(")
      < content.index('if let txt = gamePool ?? MadeiraConfig.get("pool"),'))   # gamePool is the game's own line, read only with pool-above-window (ContentView, upstream port)
first = braced(content, "private func runWineFullSequence(")
again = first[first.index("if Self.wineSequenceStarted {"):first.index("Self.wineSequenceStarted = true")]
check("a second launch in the run is refused with a message, not a stuck starting screen",
      "LibraryModel.shared.launchFailed(" in again)
helper = c_function(virtual, "static int ios_no_plain_exec( void )")
check("Wine: MADEIRA_NO_PLAIN_EXEC (unset or 0: as before) gates both plain mprotect(PROT_EXEC) attempts",
      'getenv( "MADEIRA_NO_PLAIN_EXEC" )' in helper and 'cached = v && *v && strcmp( v, "0" ) ? 1 : 0;' in helper
      and "if (!ios_no_plain_exec() && !mprotect( base, size, unix_prot | PROT_EXEC )) return 0;" in virtual
      and "if (!ios_no_plain_exec() && !mprotect( base, size, unix_prot ))" in virtual
      and virtual.count("ios_no_plain_exec()") == 2)

# ---- Swift: the pure decisions ------------------------------------------------------
enum_src = braced(stik, "enum JITNoDebuggerPool {")
pure = "\n".join(braced(stik, sig) for sig in (
    "static func readiness(", "static func attachPoll(", "static func poolMB(forLimitMB"))
attach_enum = stik[stik.index("enum AttachPoll: Equatable"):]
attach_enum = attach_enum[:attach_enum.index("\n")] + "\n"
OLD_TEXT = ("JIT is on, but StikDebug isn't attached to BCD, so BCD can't set up the JIT memory. "
            "That happens when JIT is turned on from StikDebug's app list. Use Enable JIT in BCD instead: StikDebug "
            "then attaches with BCD's script and brings you back, ready for the game.")
swift_test = r'''
import Foundation
enum MadeiraConfig { static func flag(_ name: String, fallback: Bool = true) -> Bool { fallback } }
final class LogStore {
    enum Level { case info, error, success }
    static let shared = LogStore()
    func log(_ s: String, level: Level = .info) {}
}
func jit_txm_in_device_tree() -> Int32 { -1 }
func jit_nodebug_selftest(_ p: UnsafeMutablePointer<CChar>!, _ n: Int) -> Bool { true }
enum StikJITHelper {
''' + attach_enum + pure + r'''
}
''' + enum_src + r'''
var failed = 0
var passed = 0
func expect(_ c: Bool, _ what: String) { if !c { failed += 1; print("FAIL " + what) } else { passed += 1; print("ok   " + what) } }
typealias P = JITNoDebuggerPool
// StikDebug 3.1.15's rule, quirks included
for (os, m, want) in [(27, "iPhone18,2", true), (27, "iPad8,11", false), (27, "iPad8,12", false), (27, "iPad16,3", true),
                      (26, "iPhone14,2", true), (26, "iPhone15,2", true), (26, "iPhone13,4", false), (26, "iPhone12,8", false),
                      (26, "iPad14,5", true), (26, "iPad14,4", false), (26, "iPad14,10", false), (26, "iPad16,3", true),
                      (18, "iPhone17,1", false), (18, "iPhone14,2", false), (26, "arm64", false)] {
    expect(P.stikDebugRunsScript(osMajor: os, machine: m) == want, "StikDebug runs the script on iOS \(os) \(m): \(want)")
}
expect(P.modelVersion("iPhone14,2") == 14.2 && P.modelVersion("iPad14,10") == 14.1 && P.modelVersion("iPhone13,0") == 13.0
       && P.modelVersion("x86_64") == nil && P.modelVersion("iPhone14") == nil, "the decimal reading of model numbers")
// The gate: never iOS 27, never a TXM device, never a Mac or the simulator; iOS 18 everywhere.
func g(_ os: Int, _ m: String, mac: Bool = false, sim: Bool = false, tree: Int = -1) -> Bool {
    P.gate(osMajor: os, machine: m, onMac: mac, simulator: sim, deviceTreeTXM: tree).allowed
}
expect(!g(27, "iPhone18,2") && !g(27, "iPad8,11") && !g(28, "iPhone19,1"), "iOS 27 and later: never (the owner's device)")
expect(!g(26, "iPhone15,2") && !g(26, "iPhone14,2", tree: 0) && !g(26, "iPad14,5"), "iOS 26 TXM models: never, whatever the tree says")
expect(g(26, "iPhone13,2") && g(26, "iPhone13,2", tree: 0) && g(26, "iPhone12,8"), "iOS 26 without TXM: allowed")
expect(!g(26, "iPad14,3", tree: 1) && !g(26, "iPhone13,2", tree: 1), "iOS 26: a device tree that lists TXM vetoes")
expect(g(18, "iPhone17,1") && g(18, "iPhone11,8") && g(18, "iPad16,3", tree: 1), "iOS 18: allowed on every model")
expect(!g(18, "iPhone17,1", mac: true) && !g(18, "iPhone17,1", sim: true), "never on a Mac or in the simulator")
// Readiness with CS_DEBUGGED set.
let R = StikJITHelper.readiness
expect(R(true, false, true, false), "a debugger attached (the everyday path): ready")
expect(!R(true, false, false, false), "attached and left, no debugger-less pool here (iOS 27 app list): not ready")
expect(R(true, true, false, false), "after the early detach, this run's pool: still ready (b0ab3a3's case)")
expect(R(true, false, false, true), "attached and left where the pool needs no debugger (iOS 18): ready")
expect(R(false, false, false, false), "MADEIRA_JIT_ATTACH_CHECK=0: CS_DEBUGGED alone, as before")
// One poll of Enable JIT's wait.
let A = StikJITHelper.attachPoll
expect(A(true, false, true, true, true) == .ready, "ready wins")
expect(A(false, false, true, false, false) == .attachedAndLeft, "CS_DEBUGGED appeared in this wait, nothing traced: left")
expect(A(false, true, true, false, false) == .waiting, "CS_DEBUGGED from before the wait, no attach seen: still waiting")
expect(A(false, true, true, false, true) == .attachedAndLeft, "an attach seen in this wait, nothing traced now: left")
expect(A(false, false, false, false, false) == .waiting, "nothing yet: waiting")
expect(A(false, false, true, true, true) == .waiting, "traced without being ready: waiting")
// Pool size per measured limit.
let M = StikJITHelper.poolMB
for (limit, standard, want) in [(8192, 896, 896), (6656, 896, 896), (6144, 896, 896), (6143, 896, 640), (4096, 896, 640),
                                (4095, 896, 512), (3072, 896, 512), (3071, 896, 384), (2300, 896, 384), (8192, 512, 512),
                                (3000, 512, 384), (5000, 512, 512)] {
    expect(M(limit, standard) == want, "pool for a \(limit) MB limit (standard \(standard)): \(want) MB")
}
// Advice: iOS 27 keeps its exact text; Always Run Scripts where StikDebug treats the device as non-TXM.
let old = "''' + OLD_TEXT + r'''"
expect(P.flaggedAdvice(osMajor: 27, machine: "iPhone18,2") == old, "iOS 27: the advice it had, word for word")
expect(P.flaggedAdvice(osMajor: 26, machine: "iPhone15,2").hasPrefix(old)
       && P.flaggedAdvice(osMajor: 26, machine: "iPhone15,2").contains("Built-in"), "iOS 26 TXM: the same, plus Built-in")
expect(P.flaggedAdvice(osMajor: 18, machine: "iPhone14,5").contains("Always Run Scripts")
       && !P.flaggedAdvice(osMajor: 18, machine: "iPhone14,5").contains("Built-in"), "iOS 18: Always Run Scripts, no Built-in")
expect(P.flaggedAdvice(osMajor: 26, machine: "iPhone13,2").contains("Always Run Scripts")
       && P.flaggedAdvice(osMajor: 26, machine: "iPhone13,2").contains("Built-in"), "iOS 26 non-TXM: Always Run Scripts or Built-in")
expect(P.leftAdvice(osMajor: 18).contains("Always Run Scripts") && !P.leftAdvice(osMajor: 18).contains("Built-in")
       && P.leftAdvice(osMajor: 27).contains("Built-in"), "Enable JIT's attach-and-leave advice")
print("swift: \(passed) passed, \(failed) failed")
exit(failed == 0 ? 0 : 1)
'''

swiftc = shutil.which("swiftc") or ""
with tempfile.TemporaryDirectory(prefix="bcd-jit-nodebug-") as tmpdir:
    tmp = Path(tmpdir)
    if swiftc:
        src = tmp / "main.swift"
        src.write_text(swift_test)
        exe = tmp / "swift-check"
        b = subprocess.run([swiftc, "-swift-version", "5", "-o", str(exe), str(src)], capture_output=True, text=True)
        check("the decisions compile from StikJITHelper.swift's own source", b.returncode == 0, b.stderr[-3000:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
            summary = (r.stdout.strip().splitlines() or ["no output"])[-1]
            check("the decision tables: " + summary, r.returncode == 0 and not fails,
                  "\n     ".join(fails) + r.stderr[-1000:])
    elif args.require_swift:
        check("Swift compiler available", False)
    else:
        print("SKIP: no Swift compiler; CI runs this check with --require-swift")

    # ---- C: JITAllocator.c's own functions on this Mac ------------------------------
    cc = os.environ.get("CC", "cc")
    if sys.platform != "darwin":
        print("SKIP: the C part needs Mach (macOS)")
    else:
        # The handler reads arm64 registers: on an Intel Mac a stand-in with its name, for the install rule.
        trap = c_function(alloc, "static void sigtrap_handler(") if platform.machine() == "arm64" else \
            "static void sigtrap_handler(int sig, siginfo_t *info, void *context) { (void)sig; (void)info; (void)context; }\n"
        chunk = alloc[alloc.index("/* One page through the same recipe as the pool"):]
        chunk = chunk[:chunk.index("\n}", chunk.index("void jit_nodebug_release(")) + 2] + "\n"
        harness = r'''
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/vm_map.h>
#include <libkern/OSCacheControl.h>
#include <pthread.h>
#include <setjmp.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <unistd.h>
#include <CoreFoundation/CoreFoundation.h>
#define JIT_PAGE_SIZE 0x4000
#ifndef VM_LEDGER_TAG_DEFAULT
#define VM_LEDGER_TAG_DEFAULT 0
#endif
#ifndef VM_LEDGER_FLAG_NO_FOOTPRINT
#define VM_LEDGER_FLAG_NO_FOOTPRINT (1 << 0)
#endif
extern kern_return_t mach_memory_entry_ownership(mach_port_t, mach_port_t, int, int);
static void jit_log(const char *fmt, ...) { va_list a; va_start(a, fmt); fputs("     log: ", stdout); vfprintf(stdout, fmt, a); va_end(a); fputc('\n', stdout); }
static bool g_debugged;
static bool jit_check_debugged(void) { return g_debugged; }
static size_t align_to_page(size_t size) { return (size + JIT_PAGE_SIZE - 1) & ~(size_t)(JIT_PAGE_SIZE - 1); }
''' + trap + install + chunk + c_function(alloc, "int jit_txm_in_device_tree(void)") + r'''
static int failed;
#define EXPECT(c, ...) do { if (c) { printf("ok   "); } else { printf("FAIL "); failed++; } printf(__VA_ARGS__); printf("\n"); } while (0)
static void old_segv(int s, siginfo_t *i, void *c) { (void)s; (void)i; (void)c; }
static bool region_prot(vm_address_t a, int *prot, int *max)
{
    vm_address_t q = a; vm_size_t sz = 0; vm_region_basic_info_data_64_t info; mach_port_t obj = MACH_PORT_NULL;
    mach_msg_type_number_t cnt = VM_REGION_BASIC_INFO_COUNT_64;
    if (vm_region_64(mach_task_self(), &q, &sz, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info, &cnt, &obj) != KERN_SUCCESS || q > a) return false;
    *prot = info.protection; *max = info.max_protection; return true;
}
int main(int argc, char **argv)
{
    char d[192];
    struct sigaction mine, now;
    int run_maps = argc > 1 && !strcmp(argv[1], "maps");
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("host: %s\n", run_maps ? "mapping tests" : "install rule only");

    /* the trap handler's install rule */
    struct sigaction dfl; memset(&dfl, 0, sizeof dfl); dfl.sa_handler = SIG_DFL; sigaction(SIGTRAP, &dfl, NULL);
    g_debugged = true; jit_install_trap_handler(false); sigaction(SIGTRAP, NULL, &now);
    EXPECT(now.sa_handler == SIG_DFL, "CS_DEBUGGED with MADEIRA_JIT_TRAP_FALLBACK=0: no handler, as before");
    jit_install_trap_handler(true); sigaction(SIGTRAP, NULL, &now);
    EXPECT((now.sa_flags & SA_SIGINFO) && now.sa_sigaction == sigtrap_handler, "CS_DEBUGGED: the handler goes in");
    sigaction(SIGTRAP, &dfl, NULL); g_debugged = false; jit_install_trap_handler(false); sigaction(SIGTRAP, NULL, &now);
    EXPECT(now.sa_sigaction == sigtrap_handler, "no CS_DEBUGGED: the handler goes in, as before");
#if defined(__aarch64__)
    {   /* the pool request's BRK with nobody attached: x0 = 0, the process goes on */
        register uint64_t x0 __asm__("x0") = 0x1234;
        register uint64_t x1 __asm__("x1") = 0x4000;
        __asm__ volatile("mov x16, #1\n brk #0xf00d\n" : "+r"(x0) : "r"(x1) : "x16", "memory");
        EXPECT(x0 == 0, "BRK #0xf00d with the handler: returns 0 (got %llu)", (unsigned long long)x0);
    }
#endif
    int tree = jit_txm_in_device_tree();
    EXPECT(tree >= -1 && tree <= 1, "the device-tree probe answers without crashing (%d)", tree);
    if (!run_maps) return failed ? 1 : 0;

    /* the self-test, its failure path and the handlers it leaves behind */
    memset(&mine, 0, sizeof mine); mine.sa_flags = SA_SIGINFO; mine.sa_sigaction = old_segv; sigaction(SIGSEGV, &mine, NULL);
    EXPECT(jit_nodebug_selftest(d, sizeof d), "self-test passes where a debugged process may run its own pages: %s", d);
    EXPECT(!nodebug_selftest_prot(VM_PROT_READ, d, sizeof d) && strstr(d, "signal"),
           "a page without EXECUTE ends the self-test, not the process: %s", d);
    sigaction(SIGSEGV, NULL, &now);
    EXPECT(now.sa_sigaction == old_segv, "the SIGSEGV handler from before is back");

    /* a region lands where the debugger's _M would: first fit, also past a plug */
    const size_t sz = 64ul << 20;
    mach_vm_address_t m = 0;
    if (mach_vm_allocate(mach_task_self(), &m, sz, VM_FLAGS_ANYWHERE) == KERN_SUCCESS) {
        mach_vm_deallocate(mach_task_self(), m, sz);
        unsigned int entry = 0;
        void *rx = jit_nodebug_region(sz, &entry);
        EXPECT(rx && (mach_vm_address_t)(uintptr_t)rx == m, "first fit as _M: _M got 0x%llx, the region 0x%llx",
               (unsigned long long)m, (unsigned long long)(uintptr_t)rx);
        int prot = 0, max = 0;
        EXPECT(rx && region_prot((vm_address_t)rx, &prot, &max) && prot == (VM_PROT_READ | VM_PROT_EXECUTE) && max == VM_PROT_ALL,
               "the region is RX with max RWX (as _M's: mach_vm_allocate + protect RX), got %d/%d", prot, max);
        uintptr_t rw = 0x7000000000ull;
        int kr = jit_nodebug_map_alias(entry, &rw, sz, VM_FLAGS_ANYWHERE);
        EXPECT(kr == KERN_SUCCESS && rw && region_prot(rw, &prot, &max) && prot == (VM_PROT_READ | VM_PROT_WRITE) && max == VM_PROT_ALL,
               "the RW alias (ANYWHERE from a hint) is RW with max RWX, kr=%d at 0x%lx", kr, (unsigned long)rw);
        if (kr == KERN_SUCCESS && rx) {
            ((volatile uint64_t *)rw)[1000] = 0x706f6f6c2d6e6f64ull;
            EXPECT(((volatile uint64_t *)rx)[1000] == 0x706f6f6c2d6e6f64ull, "a store through RW reads back through RX");
            vm_deallocate(mach_task_self(), rw, sz);
        }
        /* the split/low pools' way: one reservation, each region FIXED|OVERWRITE at its offset */
        vm_address_t res = 0;
        if (vm_allocate(mach_task_self(), &res, 2 * sz, VM_FLAGS_ANYWHERE) == KERN_SUCCESS) {
            uintptr_t at = res + sz;
            kr = jit_nodebug_map_alias(entry, &at, sz, VM_FLAGS_FIXED | VM_FLAGS_OVERWRITE);
            EXPECT(kr == KERN_SUCCESS && at == res + sz && ((volatile uint64_t *)at)[1000] == 0x706f6f6c2d6e6f64ull,
                   "the RW alias FIXED|OVERWRITE into a reservation, kr=%d", kr);
            vm_deallocate(mach_task_self(), res, 2 * sz);
        }
        if (rx) vm_deallocate(mach_task_self(), (vm_address_t)rx, sz);
        jit_nodebug_release(entry);

        /* a plug over the first fit moves both the same way */
        mach_vm_address_t first = 0, next = 0;
        mach_vm_allocate(mach_task_self(), &first, sz, VM_FLAGS_ANYWHERE);
        mach_vm_allocate(mach_task_self(), &next, sz, VM_FLAGS_ANYWHERE);
        mach_vm_deallocate(mach_task_self(), next, sz);
        rx = jit_nodebug_region(sz, &entry);
        EXPECT(rx && (mach_vm_address_t)(uintptr_t)rx == next, "with the first hole plugged it lands where _M would: 0x%llx",
               (unsigned long long)(uintptr_t)rx);
        if (rx) vm_deallocate(mach_task_self(), (vm_address_t)rx, sz);
        jit_nodebug_release(entry);
        mach_vm_deallocate(mach_task_self(), first, sz);
    } else
        EXPECT(0, "mach_vm_allocate for the placement test");
    return failed ? 1 : 0;
}
'''
        src = tmp / "nodebug.c"
        src.write_text(harness)
        exe = tmp / "nodebug"
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-g", str(src), "-o", str(exe),
                            "-framework", "CoreFoundation"], capture_output=True, text=True)
        check("JITAllocator.c's trap, probe, region and alias functions compile for this Mac (-Wall -Werror)",
              b.returncode == 0, b.stderr[-3000:])
        if b.returncode == 0:
            intel = platform.machine() == "x86_64"
            r = subprocess.run([str(exe)] + (["maps"] if intel else []), capture_output=True, text=True, timeout=120)
            print("\n".join("     " + l for l in r.stdout.splitlines() if l.startswith(("ok", "FAIL"))))
            check("they behave on this Mac" + ("" if intel else " (install rule and BRK only: the mapping tests run on "
                                                                "Intel, M4 kernels crash on dual mappings)"),
                  r.returncode == 0 and "FAIL" not in r.stdout, r.stdout[-2500:] + r.stderr[-800:])

    # ---- the whole file for the device ----------------------------------------------
    sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip() \
        if shutil.which("xcrun") else ""
    if sdk:
        r = subprocess.run(["xcrun", "clang", "-target", "arm64-apple-ios18.0", "-isysroot", sdk, "-Wall", "-Wextra",
                            "-Wno-unused-function", "-Werror", "-fsyntax-only", str(app / "JITAllocator.c")],
                           capture_output=True, text=True)
        check("JITAllocator.c compiles for arm64-apple-ios18.0 with -Wall -Wextra -Werror", r.returncode == 0, r.stderr[-3000:])
    else:
        print("SKIP: no iOS SDK for the device compile")

print("check-jit-no-debugger: PASS" if ok else "check-jit-no-debugger: FAILED")
sys.exit(0 if ok else 1)
