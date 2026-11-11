#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEXCore: a pooled buffer that cannot be allocated is reclaimed and retried, then refused; it is never handed out as NULL (night round 2026-11-11, X1 S2).

Inside the FEX address band FEXCore::Allocator::VirtualAlloc returns NULL when the arena is full (by design, ml799). The three pooled
per-thread buffers (IR 16 MB `FEXMem_OpDispatcher`, decoder 8 MB `FEXMem_Frontend`, JIT temp buffer `FEXMem_CPUBackend`) came out of
IntrusivePooledAllocator::ClaimBufferImpl, which registered a MemoryBuffer with Ptr == NULL and handed it to the client unchecked:
IREmitter::ResetWorkingList then wrote the invalid-node header at NULL+0x800010 (`[fex-fault] pc=libarm64ecfex.dll+0xf59f0 ...
fault-addr=0x800010`; `[vname] FEXMem_OpDispatcher 0x0..0x1000000`), the decoder stored at NULL (Q7XZ5F), and the fault leaked FEX's read lock, so
every other thread wedged and the session died seconds later (GTA V Enhanced 8HCFEP = RB5K07, Arctic Drive KNQGPQ = WPV7GM, NTE EQWQ67,
Dominus Gladiatus Q7XZ5F, RDR2 N1MBVQ = PHPRY3's second fault kind, GTA V Legacy / Mycopunk of round 630).

  1. ClaimBufferImpl: when the allocation fails, every buffer a client has DISOWNED is taken back at once (the same compare-exchange the
     5-second retirement uses, minus the age test: the header documents that a disowned buffer may be taken back at any time), every unclaimed
     buffer is freed, and the allocation is tried once more. The arena is full in these logs while ~100 idle buffers of 24 MB sit in the pool
     (8HCFEP: four threads started in a burst and found none free). Only when it still fails is there no buffer: ClaimBuffer, the allocator's
     ReownOrClaimBuffer and PoolBufferWithTimedRetirement::ReownOrClaimBufferWithSize report it (nullopt / Ptr == NULL, flag stays FREE) and
     no NULL MemoryBuffer is registered. PooledAllocatorVirtual*::Alloc no longer names or protects a NULL pointer.
  2. The clients check it: DualIntrusiveAllocatorThreadPool / IREmitter / OpDispatchBuilder::ResetWorkingList return false;
     ContextImpl::GenerateIR then returns "no IR" (the existing [ir-null] bail), logging `[ir-alloc] rev=r652`; the decoder returns with
     AllocFailed() set and GenerateIR does the same; the JIT's CompileCode returns no code. ContextImpl::CompileBlock already turns "no
     entry point" into `return 0`, which Core.cpp documents as "raises C0000005 to the guest immediately": the guest gets its fault at its own
     RIP instead of the emulator writing through NULL while holding a lock. PoolBufferWithTimedRetirement::DelayedDisownBuffer ignores a
     buffer that was never claimed (flag FREE: it dereferenced a singular iterator).
Only the allocation-returned-NULL path changes: with a buffer every changed function behaves as before (the claim has the same effects, the
checks are false). Applied to BOTH FEX modules (the files are shared): tools/build-xtajit64.sh and tools/build-xtajit-wow64.sh; the
sources are restored after the build. Not done: the 3 x 20 ms retry sleep outside the pool mutex X1 mentions (a sleep inside a compile that
holds the code-invalidation lock could stall writers; the reclaim is the part that makes the claims succeed).
Idempotent, all-or-nothing, fails by name when an anchor moves. Usage: patch-fex-ios-pool-alloc-null.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "pool-alloc-null-v1 madeira-bcd"
POOL = "FEXCore/include/FEXCore/Utils/ThreadPoolAllocator.h"
IRLIST = "FEXCore/Source/Interface/IR/IntrusiveIRList.h"
IREMIT = "FEXCore/Source/Interface/IR/IREmitter.h"
OPDH = "FEXCore/Source/Interface/Core/OpcodeDispatcher.h"
OPDC = "FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp"
CORE = "FEXCore/Source/Interface/Core/Core.cpp"
FRONTH = "FEXCore/Source/Interface/Core/Frontend.h"
FRONTC = "FEXCore/Source/Interface/Core/Frontend.cpp"
JIT = "FEXCore/Source/Interface/Core/JIT/JIT.cpp"

EDITS = {
    POOL: [
        # 1. the allocator's own claim API reports failure
        ("""   * @return iterator to the internal tracking container
   */
  ContainerType::iterator ClaimBuffer(size_t Size, BufferOwnedFlag* CurrentClientFlag) {
    std::unique_lock lk {AllocationMutex};
    auto Buffer = ClaimBufferImpl(Size);
    (*Buffer)->CurrentClientOwnedFlag = CurrentClientFlag;
    CurrentClientFlag->store(ClientFlags::FLAG_OWNED);
    return Buffer;
  }
""",
         """   * @return iterator to the internal tracking container; std::nullopt when no buffer could be allocated (the flag stays FREE)
   */
  std::optional<ContainerType::iterator> ClaimBuffer(size_t Size, BufferOwnedFlag* CurrentClientFlag) {
    std::unique_lock lk {AllocationMutex};
    auto Buffer = ClaimBufferImpl(Size);
    if (!Buffer) {   // """ + MARKER + """
      return std::nullopt;
    }
    (**Buffer)->CurrentClientOwnedFlag = CurrentClientFlag;
    CurrentClientFlag->store(ClientFlags::FLAG_OWNED);
    return Buffer;
  }
"""),
        ("""  ContainerType::iterator ReownOrClaimBuffer(const ContainerType::iterator& Buffer, size_t Size, BufferOwnedFlag* CurrentClientFlag) {""",
         """  std::optional<ContainerType::iterator> ReownOrClaimBuffer(const ContainerType::iterator& Buffer, size_t Size, BufferOwnedFlag* CurrentClientFlag) {"""),
        # 2. claim implementation: reclaim, retry, refuse
        ("""  ContainerType::iterator ClaimBufferImpl(size_t Size) {
    auto BuffersEnd = UnclaimedBuffers.end();""",
         """  std::optional<ContainerType::iterator> ClaimBufferImpl(size_t Size) {
    auto BuffersEnd = UnclaimedBuffers.end();"""),
        ("""    // Need to allocate a new buffer, couldn't fit
    auto Data = Alloc(Size);
    return ClaimedBuffers.emplace(ClaimedBuffers.end(), new MemoryBuffer {Data, Size, ClockType::now()});
  }
""",
         """    // Need to allocate a new buffer, couldn't fit
    auto Data = Alloc(Size);
    if (!Data) {
      // """ + MARKER + """: the address band is full (Alloc returns nullptr by design). Take back every buffer a client has disowned and
      // free every unclaimed one (a disowned buffer may be reclaimed at any time, only the age test is skipped), then try once more.
      ReclaimIdleBuffersNow();
      Data = Alloc(Size);
      if (!Data) {
        return std::nullopt;
      }
    }
    return ClaimedBuffers.emplace(ClaimedBuffers.end(), new MemoryBuffer {Data, Size, ClockType::now()});
  }

  // """ + MARKER + """: the allocation failed; give every idle byte back to the allocator before the one retry.
  void ReclaimIdleBuffersNow() {
    for (auto it = ClaimedBuffers.begin(); it != ClaimedBuffers.end();) {
      if ((*it)->CurrentClientOwnedFlag->load() == ClientFlags::FLAG_DISOWNED) {
        ClientFlags Expected = ClientFlags::FLAG_DISOWNED;
        if ((*it)->CurrentClientOwnedFlag->compare_exchange_strong(Expected, ClientFlags::FLAG_FREE)) {
          // The same hand-over as the retirement above: the client's re-own compare-exchange now fails and it claims a new buffer.
          (*it)->CurrentClientOwnedFlag = nullptr;
          UnclaimedBuffers.emplace_back(*it);
          it = ClaimedBuffers.erase(it);
          continue;
        }
      }
      ++it;
    }
    for (auto* Buffer : UnclaimedBuffers) {
      Free(Buffer->Ptr, Buffer->Size);
      delete Buffer;
    }
    UnclaimedBuffers.clear();
  }
"""),
        # 3. the virtual allocators do not name / protect NULL
        ("""    auto Result = FEXCore::Allocator::VirtualAlloc(Size);
    if (Name) {
      FEXCore::Allocator::VirtualName(Name, Result, Size);
    }
    return Result;""",
         """    auto Result = FEXCore::Allocator::VirtualAlloc(Size);
    if (Name && Result) {   // """ + MARKER + """
      FEXCore::Allocator::VirtualName(Name, Result, Size);
    }
    return Result;"""),
        ("""    auto Ptr = FEXCore::Allocator::VirtualAlloc(Size);
    uintptr_t LastPageAddr""",
         """    auto Ptr = FEXCore::Allocator::VirtualAlloc(Size);
    if (!Ptr) {   // """ + MARKER + """
      return Ptr;
    }
    uintptr_t LastPageAddr"""),
        # 4. the client wrapper reports it and tolerates a buffer that was never claimed
        ("""    // Claim a new buffer if needed
    Size = NewSize.value_or(Size);
    if (!Buffer) {
      Buffer = ThreadAllocator.ClaimBuffer(Size, &ClientOwnedFlag);
    }

    Info = *Buffer;
""",
         """    // Claim a new buffer if needed
    Size = NewSize.value_or(Size);
    if (!Buffer) {
      Buffer = ThreadAllocator.ClaimBuffer(Size, &ClientOwnedFlag);
      if (!Buffer) {   // """ + MARKER + """: no buffer could be allocated; the flag stays FREE and Info is not touched
        return {
          .Ptr = Type {},
          .Size = 0,
        };
      }
    }

    Info = *Buffer;
"""),
        ("""  void DelayedDisownBuffer() {
    LOGMAN_THROW_A_FMT(FEXCore::Utils::IntrusivePooledAllocator::IsClientBufferOwned(ClientOwnedFlag), "Tried to disown buffer when client "
""",
         """  void DelayedDisownBuffer() {
    if (FEXCore::Utils::IntrusivePooledAllocator::IsClientBufferFree(ClientOwnedFlag)) {   // """ + MARKER + """: never claimed (the allocation failed)
      return;
    }
    LOGMAN_THROW_A_FMT(FEXCore::Utils::IntrusivePooledAllocator::IsClientBufferOwned(ClientOwnedFlag), "Tried to disown buffer when client "
"""),
    ],
    IRLIST: [
        ("""  void ReownOrClaimBuffer() {
    Data = PoolObject.ReownOrClaimBuffer();
    List = Data + MemorySize;
  }
""",
         """  // """ + MARKER + """: false when the pool could not supply the buffer (Data and List are then 0)
  bool ReownOrClaimBuffer() {
    Data = PoolObject.ReownOrClaimBuffer();
    if (!Data) {
      List = 0;
      return false;
    }
    List = Data + MemorySize;
    return true;
  }
"""),
    ],
    IREMIT: [
        ("""  void ReownOrClaimBuffer() {
    DualListData.ReownOrClaimBuffer();

    // Reset the working list on new buffer.
    ResetWorkingList();
  }
""",
         """  // """ + MARKER + """: false when the pool could not supply the buffer (nothing is written then)
  bool ReownOrClaimBuffer() {
    if (!DualListData.ReownOrClaimBuffer()) {
      return false;
    }

    // Reset the working list on new buffer.
    ResetWorkingList();
    return true;
  }
"""),
    ],
    OPDH: [
        ("  void ResetWorkingList();\n", "  bool ResetWorkingList();   // " + MARKER + ": false when the IR buffer could not be allocated\n"),
    ],
    OPDC: [
        ("""void OpDispatchBuilder::ResetWorkingList() {
  IREmitter::ReownOrClaimBuffer();
""",
         """bool OpDispatchBuilder::ResetWorkingList() {
  if (!IREmitter::ReownOrClaimBuffer()) {   // """ + MARKER + """
    return false;
  }
"""),
        ("""  RegCache.Written = 0;
  RegCache.Cached = 0;
}

void OpDispatchBuilder::UnhandledOp(OpcodeArgs) {""",
         """  RegCache.Written = 0;
  RegCache.Cached = 0;
  return true;
}

void OpDispatchBuilder::UnhandledOp(OpcodeArgs) {"""),
    ],
    CORE: [
        ("""  Thread->OpDispatcher->ResetWorkingList();
""",
         """  if (!Thread->OpDispatcher->ResetWorkingList()) {   // """ + MARKER + """
    // The IR buffer could not be allocated (the FEX address band is full and nothing idle was left to take back): no block is compiled;
    // CompileBlock returns 0 and the guest gets its fault at its own RIP, instead of the emitter writing through NULL.
    static int AllocN = 0;
    if (AllocN++ < 12) {
      LogMan::Msg::EFmt("[ir-alloc] rev=r652 no IR buffer for guest RIP {:#x} (the emulator's address band is exhausted): block not compiled", GuestRIP);
    }
    return {};
  }
"""),
        ("""    Thread->FrontendDecoder->DecodeInstructionsAtEntry(Thread, GuestCode, GuestRIP, MaxInst);
""",
         """    Thread->FrontendDecoder->DecodeInstructionsAtEntry(Thread, GuestCode, GuestRIP, MaxInst);
    if (Thread->FrontendDecoder->AllocFailed()) {   // """ + MARKER + """
      static int DecAllocN = 0;
      if (DecAllocN++ < 12) {
        LogMan::Msg::EFmt("[ir-alloc] rev=r652 no decoder buffer for guest RIP {:#x} (the emulator's address band is exhausted): block not compiled", GuestRIP);
      }
      Thread->OpDispatcher->DelayedDisownBuffer();
      return {};
    }
"""),
    ],
    FRONTH: [
        ("""  void DelayedDisownBuffer() {
    PoolObject.DelayedDisownBuffer();
  }
""",
         """  void DelayedDisownBuffer() {
    PoolObject.DelayedDisownBuffer();
  }

  // """ + MARKER + """: the last DecodeInstructionsAtEntry found no decode buffer (nothing was decoded)
  bool AllocFailed() const {
    return HitAllocFailure;
  }
"""),
        ("  bool HitBadRelocation {};\n", "  bool HitBadRelocation {};\n  bool HitAllocFailure {};   // " + MARKER + "\n"),
    ],
    FRONTC: [
        ("""  DecodedBuffer = PoolObject.ReownOrClaimBuffer();
""",
         """  DecodedBuffer = PoolObject.ReownOrClaimBuffer();
  HitAllocFailure = DecodedBuffer == nullptr;   // """ + MARKER + """: the pool had no 8 MB buffer to give; decode nothing instead of storing through NULL
  if (HitAllocFailure) {
    return;
  }
"""),
    ],
    JIT: [
        ("""  auto TempCodeBufferInfo = TempAllocator.ReownOrClaimBufferWithSize(DesiredBufferRange);
""",
         """  auto TempCodeBufferInfo = TempAllocator.ReownOrClaimBufferWithSize(DesiredBufferRange);
  if (!TempCodeBufferInfo.Ptr) {   // """ + MARKER + """: no temp buffer (the emulator's address band is exhausted): no code, CompileBlock returns 0
    static int TempAllocN = 0;
    if (TempAllocN++ < 12) {
      LogMan::Msg::EFmt("[ir-alloc] rev=r652 no JIT temp buffer for guest RIP {:#x} (the emulator's address band is exhausted): block not compiled", Entry);
    }
    return {};
  }
"""),
    ],
}


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    root = Path(sys.argv[1])
    texts = {}
    for rel in EDITS:
        p = root / rel
        if not p.is_file():
            sys.exit("patch-fex-ios-pool-alloc-null: %s not found (FEX changed)" % rel)
        texts[rel] = p.read_text()
    if all(MARKER in t for t in texts.values()):
        print("FEXCore: the pooled-buffer NULL guard is already in; nothing to do")
        return
    if any(MARKER in t for t in texts.values()):
        sys.exit("patch-fex-ios-pool-alloc-null: some files carry the patch and some do not (partial state)")
    out = {}
    for rel, edits in EDITS.items():
        t = texts[rel]
        for old, new in edits:
            n = t.count(old)
            if n != 1:
                sys.exit("patch-fex-ios-pool-alloc-null: an anchor in %s not found once (%d times; FEX changed): %r" % (rel, n, old[:70]))
            t = t.replace(old, new)
        out[rel] = t
    for rel, t in out.items():
        (root / rel).write_text(t)
    print("FEXCore: a pooled buffer that cannot be allocated is reclaimed and retried, then refused (no NULL IR/decoder/JIT buffer)")


main()
