#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX ARM64EC: once the process is terminating, the memory hooks no longer use the emulator's tracker (night round 2026-11-11, X1 S3).

A normal exit (`ExitProcess`) runs Wine's RtlExitUserProcess: NtTerminateProcess(0) ends every other thread (the EC ntdll announces it to FEX
through ProcessTerm(handle, FALSE, 0)), then the same thread runs LdrShutdownProcess (DLL detach code, FEX's own module included) and frees
memory. Each free reaches NotifyMemoryFree -> InvalidationTracker::InvalidateAlignedInterval, which asks the emulator's context object for its
mutex (`ldr x11,[x10,#0xf0]`, x10 = the object's first word); in about 3 % of the exits of build 634 that object is already gone (its first
word reads 0) and the exit thread faults at address 0xf0: `[fex-fault] pc=...+0x12f880 (InvalidateAlignedInterval+0x74) fault-addr=0xf0`
after `NtTerminateProcess(handle=0x0, exit_code=0x0)`. Seen in 06A8JF, TSGVTP (the game ran 133 s and quit normally; the fault turned the
exit into `[exit-report] status=0xc0000005`), 66HYF7, BS6583, JCW9Q0, Z40K5X, and (T3 S6) QTDFHP, RBCWQH, A3ZNFE, where the
fault hid a memory kill from the app's crash analysis.

ProcessTerm was an empty function. Now `ProcessTerm(handle, FALSE, ...)` sets FexProcessTerminating (and logs `[fex-term]` once, with the context
pointer and its first word, which tells the next faulting log whether the object was already gone at ProcessTerm or was torn down later), and
NotifyMemoryAlloc, NotifyMemoryFree, NotifyMemoryProtect, FlushInstructionCacheHeavy, BTCpu64FlushInstructionCache and BTCpu64NotifyMemoryDirty
return at once while it is set. Nothing about a dying process needs code invalidation any more (every other thread is gone and the exit thread is
only running DLL detach code and freeing memory); a call that fails today (the exit thread faults) simply returns. If the NtTerminateProcess(0)
call itself fails the flag is cleared again (ProcessTerm(handle, TRUE, status != 0)). NotifyUnmapViewOfSection and BTCpu64NotifyReadFile are
NOT gated: they pair a lock in their first half with the unlock in their second, and a flag that changes in between would leak the lock.
Applied by tools/build-xtajit64.sh to every pin (Module.cpp is restored after the build); idempotent, all-or-nothing, fails by name when an
anchor moves. Usage: patch-fex-ios-process-term.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "process-term-v1 madeira-bcd"

PT_OLD = "void ProcessTerm(HANDLE Handle, BOOL After, NTSTATUS Status) {}\n"
PT_NEW = '''// ''' + MARKER + ''' (night round 2026-11-11, X1 S3): set by ProcessTerm(handle, FALSE) when NtTerminateProcess(0) starts the process's end
// (RtlExitUserProcess); the memory hooks below return at once while it is set (the emulator's context may already be torn down:
// InvalidateAlignedInterval faulted at 0xf0 in 3 % of the exits of build 634). Trivially constructed: it is read after the module's own detach.
std::atomic<bool> FexProcessTerminating {false};

void ProcessTerm(HANDLE Handle, BOOL After, NTSTATUS Status) {
  if (!After) {
    FexProcessTerminating.store(true, std::memory_order_relaxed);
    static std::atomic<uint32_t> Reported {0};
    if (Reported.fetch_add(1, std::memory_order_relaxed) == 0) {
      // The context's first word (its vtable pointer) is what the faulting exit thread read as 0: record it while the process is whole.
      const uint64_t Ctx = reinterpret_cast<uint64_t>(CTX.get());
      uint64_t First = ~0ULL;
      if (Ctx && ios_fex_band_base && Ctx >= ios_fex_band_base && Ctx + 8 <= ios_fex_band_end && !(Ctx & 7)) {
        First = *reinterpret_cast<const volatile uint64_t*>(Ctx);
      }
      LogMan::Msg::EFmt("[fex-term] rev=r652 ProcessTerm handle={} ctx={:#x} ctx[0]={:#x} tracker={}: the memory hooks stop using the emulator's tracker",
                        Handle, Ctx, First, InvalidationTracker.has_value());
    }
  } else if (Status) {
    FexProcessTerminating.store(false, std::memory_order_relaxed);   // the process is not ending after all
  }
}
'''

HOOKS = [
    "void NotifyMemoryAlloc(void* Address, SIZE_T Size, ULONG Type, ULONG Prot, BOOL After, NTSTATUS Status) {\n",
    "void NotifyMemoryFree(void* Address, SIZE_T Size, ULONG FreeType, BOOL After, NTSTATUS Status) {\n",
    "void NotifyMemoryProtect(void* Address, SIZE_T Size, ULONG NewProt, BOOL After, NTSTATUS Status) {\n",
    "void FlushInstructionCacheHeavy(const void* Address, SIZE_T Size) {\n",
    "void BTCpu64FlushInstructionCache(const void* Address, SIZE_T Size) {\n",
    "void BTCpu64NotifyMemoryDirty(void* Address, SIZE_T Size) {\n",
]
GATE = (
    "  if (FexProcessTerminating.load(std::memory_order_relaxed)) {   // " + MARKER + "\n"
    "    return;\n"
    "  }\n"
)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / "Source/Windows/ARM64EC/Module.cpp"
    src = path.read_text()
    if MARKER in src:
        print("ARM64EC/Module.cpp: the process-terminating gate is already in; nothing to do")
        return
    if src.count(PT_OLD) != 1:
        sys.exit("patch-fex-ios-process-term: the empty ProcessTerm not found once (FEX changed)")
    out = src.replace(PT_OLD, PT_NEW)
    for head in HOOKS:
        if out.count(head) != 1:
            sys.exit("patch-fex-ios-process-term: %s not found once (FEX changed)" % head.split("(")[0])
        out = out.replace(head, head + GATE)
    if out.index("std::atomic<bool> FexProcessTerminating") > out.index(HOOKS[0]):
        sys.exit("patch-fex-ios-process-term: ProcessTerm is defined after the hooks now (FEX changed)")
    if "ios_fex_band_base" not in src:
        sys.exit("patch-fex-ios-process-term: ios_fex_band_base is not declared in Module.cpp (FEX changed)")
    path.write_text(out)
    print("ARM64EC/Module.cpp: after NtTerminateProcess(0) the memory hooks no longer use the emulator's tracker; ProcessTerm logs [fex-term] once")


main()
