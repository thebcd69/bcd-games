#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX WOW64: the thread-creation lock may be taken again by the thread that holds it (night round 2026-11-11, W-FEX: G5 S3 + G7 S13).

wow64.dll's NtProtectVirtualMemory thunk brackets the system call with BTCpuNotifyMemoryProtect(..., FALSE) and (..., TRUE); the first
LOCKS ThreadCreationMutex and the second unlocks it, so the lock is held while the unix NtProtectVirtualMemory stores the OLD protection
through the 32-bit program's own pointer. A 32-bit sound library (BASS.dll: FTL, Peggle Deluxe) passes a pointer into its own RWX section
page, which FEX has armed for self-modifying code (host read-only): the store faults in BCD's host code (`Madeira`NtProtectVirtualMemory+0x7a4`,
`str w20,[x25]`), the Mach handler declines (not a JIT store), the fault is raised to the guest, and FEX's exception handler takes the same lock
(`std::scoped_lock Lock(ThreadCreationMutex)` before HandleRWXAccessViolation in BTCpuResetToConsistentStateImpl) on the SAME thread: a plain
std::mutex waits for itself for ever. The game never draws a frame; `[srw-hang] ml2250 ... waits 30s for an SRW lock at ...` (PHE15B, Peggle
SF5ZDS, METSK8, FTL YSDHV6, VT69D9). The committed wow64.dll cannot take a Wine source fix (it is a tracked binary), so the lock is made
re-enterable here; with it the nested handler unprotects the page (HandleRWXAccessViolation), the store is retried and succeeds.

The ARM64EC module's ThreadCreationMutex is a recursive_mutex built on first use (a global recursive_mutex is not zero-state safe: the iOS 18
static-initialiser lesson in tools/patch-fex-ios-wow-threads-ctor.py). This module's hooks lock it while the heap grows (the Wine heap's own
critical section may allocate), so a function-local static built on first use could be re-entered by its own construction. The lock is
instead a std::mutex (a zero-state SRWLOCK, exactly what is there today) plus the owner's TEB and a depth count, constant-initialised
(`constinit`): the object stays all-zero until first used, nothing runs at start-up, and a thread that does not hold the lock behaves
exactly as before (it takes the std::mutex). Only a thread that already holds it takes the new path, which waits for ever today.
Every existing user (`std::scoped_lock`, `std::unique_lock`, `.lock()`/`.unlock()` pairs) only needs lock() and unlock().

Applied by tools/build-xtajit-wow64.sh (and build/fex-wow64/build.sh) to every pin; Module.cpp is restored after the build; idempotent; fails
by name when the anchor moves. Usage: patch-fex-ios-wow-recursive-tcm.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "wow64-tcm-recursive v1 madeira-bcd"
ANCHOR = "std::mutex ThreadCreationMutex;\n"
NEW = '''// ''' + MARKER + ''' (tools/patch-fex-ios-wow-recursive-tcm.py; FTL PHE15B, Peggle SF5ZDS, night round 2026-11-11).
// wow64.dll's NtProtectVirtualMemory thunk holds this lock from BTCpuNotifyMemoryProtect(FALSE) to (TRUE) while the unix call stores the old
// protection through the 32-bit program's own pointer. A sound library (BASS) passes a pointer into an SMC-armed page, the store faults, and
// the exception path (BTCpuResetToConsistentStateImpl's AV branch) takes this lock again on the same thread. A std::mutex waits for
// itself for ever; this one lets its owner in again. Zero-state like the std::mutex it wraps (constinit: no run-time constructor).
class RecursiveThreadCreationMutex {
public:
  void lock() {
    const auto Self = reinterpret_cast<uintptr_t>(CurrentTEB());
    if (Self && Owner.load(std::memory_order_relaxed) == Self) {
      ++Depth;
      return;
    }
    M.lock();
    Owner.store(Self, std::memory_order_relaxed);
    Depth = 1;
  }

  void unlock() {
    if (Depth > 1) {
      --Depth;
      return;
    }
    Depth = 0;
    Owner.store(0, std::memory_order_relaxed);
    M.unlock();
  }

private:
  std::mutex M;
  std::atomic<uintptr_t> Owner {0};
  uint32_t Depth {0};
};
constinit RecursiveThreadCreationMutex ThreadCreationMutex;
'''


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / "Source/Windows/WOW64/Module.cpp"
    src = path.read_text()
    if MARKER in src:
        print("WOW64/Module.cpp: the re-enterable thread-creation lock is already in; nothing to do")
        return
    if src.count(ANCHOR) != 1:
        sys.exit("patch-fex-ios-wow-recursive-tcm: `std::mutex ThreadCreationMutex;` not found once (FEX changed)")
    if "#include <atomic>" not in src or "#include <mutex>" not in src or "using FEX::Windows::WOW64::CurrentTEB;" not in src:
        sys.exit("patch-fex-ios-wow-recursive-tcm: <atomic>, <mutex> or CurrentTEB() is not available in Module.cpp (FEX changed)")
    path.write_text(src.replace(ANCHOR, NEW))
    print("WOW64/Module.cpp: ThreadCreationMutex can be taken again by the thread that holds it (a nested fault handler no longer waits for itself)")


main()
