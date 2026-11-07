#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Russell Barton
"""FEX: a guest atomic whose operand crosses a 16-byte boundary is done inline instead of trapping (MADEIRA_FEX_SPLIT_LOCK).

An x86 `xchg [mem], reg` or a `lock xadd/add/sub/and/or/xor` on a misaligned operand is legal and atomic on x86 (a split lock).
FEX lowers it to one LSE instruction (SWPAL, LDADDAL, LDCLRAL, LDSETAL, LDEORAL). Apple's cores (LSE2) run those on any operand
inside one 16-byte block and raise an alignment fault for one that crosses a 16-byte boundary; BCD's exception thread then emulates
the operation (signal_arm64_ios.c, "[mach_exc] UNALIGNED-LSE"), about 25-30 us each, the trapping thread waiting meanwhile. Far Cry
4's FC64.dll does about 450,000 of them on its own stack at each start of FarCry4.exe (three per launch; about 54 s of the 93 s
from launch to the splash screen were spent in phases these traps limit), and about 5,000 a second while the game runs; Ubisoft
Connect's 32-bit upc.exe about 200,000 at its start.

With the patch, on iOS, each such atomic first tests whether its operand's first and last byte lie in different 16-byte blocks
(three instructions; NZCV, which holds the guest flags, is not touched). If they do not, the original instruction runs as before.
If they do, the inline path:
  * probes the first and the last byte with an atomic byte add of zero, so a fault (an unmapped, read-only or guard page) is
    raised there, at the access's own pages, before anything is held;
  * takes a spin lock (one per process), with a full barrier;
  * loads the operand, computes, stores it, with plain accesses (which may be unaligned), releases the lock with a full barrier,
    and puts the old value in the destination, zero-extended, as the LSE instruction does.
Crossing atomics stay serialised with each other, as the single exception thread serialised them, and are not atomic against
plain accesses of other threads, as before (and as upstream FEX's own split-lock emulation). A lock not had within 65,536 tries
(a holder preempted or suspended) leaves the operation to the original instruction and its trap, so no thread waits on another
for long. Only TMP1, TMP3 and TMP4 are used (an operation's value may be in TMP2). A byte operand cannot cross and is emitted as
before, as is everything without the LSE extension, CAS and CASPair.

MADEIRA_FEX_SPLIT_LOCK=0 when the program starts emits the original instruction alone, byte for byte. Off iOS (no FEX_IOS_HOST)
nothing changes.

Applied by tools/build-xtajit64.sh and tools/build-xtajit-wow64.sh (each restores AtomicOps.cpp after its build); idempotent;
a moved anchor refuses the patch and the build goes on without it (a warning).
Usage: patch-fex-ios-split-lock.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "split-lock-v1 madeira-bcd"
ATOMIC = "FEXCore/Source/Interface/Core/JIT/AtomicOps.cpp"

INCLUDE_ANCHOR = '#include "Interface/Core/JIT/JITClass.h"\n'
INCLUDE = "#include <cstdlib>\n"

HELPER_ANCHOR = "namespace FEXCore::CPU {\n"
HELPER = r'''// split-lock-test:begin
/* split-lock-v1 madeira-bcd (tools/patch-fex-ios-split-lock.py): an atomic whose operand crosses a 16-byte boundary faulted on
 * Apple's LSE2 and was emulated by BCD's exception thread; on iOS it is done inline (see EmitAtomicRMW). */
enum class SplitLockOp { Swap, Add, Clr, Set, Eor };

static void EmitLSE(Arm64Emitter& E, SplitLockOp Op, ARMEmitter::SubRegSize Size, ARMEmitter::Register Value, ARMEmitter::Register Dst,
                    ARMEmitter::Register Mem) {
  switch (Op) {
  case SplitLockOp::Swap: E.ldswpal(Size, Value, Dst, Mem); break;
  case SplitLockOp::Add: E.ldaddal(Size, Value, Dst, Mem); break;
  case SplitLockOp::Clr: E.ldclral(Size, Value, Dst, Mem); break;
  case SplitLockOp::Set: E.ldsetal(Size, Value, Dst, Mem); break;
  case SplitLockOp::Eor: E.ldeoral(Size, Value, Dst, Mem); break;
  }
}

#ifdef FEX_IOS_HOST
// Unset or anything but "0": on. Read once, when the process's emulator first emits an atomic.
static bool SplitLockEnabled() {
  static const bool On = [] {
    const char* Env = getenv("MADEIRA_FEX_SPLIT_LOCK");
    return !(Env && Env[0] == '0' && !Env[1]);
  }();
  return On;
}

// The lock of this process's crossing atomics, and how many times a waiter tries it before it leaves the operation to the trap.
alignas(64) static uint32_t SplitLockWord;
constexpr uint64_t SplitLockTries = 0x10000;
#endif

// The LSE read-modify-write `Op` of `Size` bytes at [Mem] with `Value`, the old value to `Dst`; on iOS a crossing operand is done
// inline under the process's split lock instead of faulting.
static void EmitAtomicRMW(Arm64Emitter& E, SplitLockOp Op, ARMEmitter::SubRegSize Size, ARMEmitter::Register Value,
                          ARMEmitter::Register Dst, ARMEmitter::Register Mem) {
#ifdef FEX_IOS_HOST
  const uint32_t Bytes = 1u << FEXCore::ToUnderlying(Size);
  if (Bytes > 1 && SplitLockEnabled()) {
    ARMEmitter::ForwardLabel Crossing, Locked, Done;
    ARMEmitter::BackwardLabel Spin;

    // Bit 4 of first ^ last byte's address: set exactly when the operand (at most 8 bytes) crosses a 16-byte boundary.
    E.add(ARMEmitter::Size::i64Bit, TMP1, Mem, Bytes - 1);
    E.eor(ARMEmitter::Size::i64Bit, TMP1, TMP1, Mem);
    (void)E.tbnz(TMP1, 4, &Crossing);
    EmitLSE(E, Op, Size, Value, Dst, Mem);
    (void)E.b(&Done);

    (void)E.Bind(&Crossing);
    E.stadd(ARMEmitter::SubRegSize::i8Bit, ARMEmitter::Reg::zr, Mem);
    E.add(ARMEmitter::Size::i64Bit, TMP1, Mem, Bytes - 1);
    E.stadd(ARMEmitter::SubRegSize::i8Bit, ARMEmitter::Reg::zr, TMP1);
    E.LoadConstant(ARMEmitter::Size::i64Bit, TMP1, reinterpret_cast<uintptr_t>(&SplitLockWord));
    E.LoadConstant(ARMEmitter::Size::i64Bit, TMP3, SplitLockTries);
    (void)E.Bind(&Spin);
    E.movz(ARMEmitter::Size::i32Bit, TMP4, 1);
    E.ldswpa(ARMEmitter::SubRegSize::i32Bit, TMP4, TMP4, TMP1);
    (void)E.cbz(ARMEmitter::Size::i32Bit, TMP4, &Locked);
    E.yield();
    E.sub(ARMEmitter::Size::i64Bit, TMP3, TMP3, 1);
    (void)E.cbnz(ARMEmitter::Size::i64Bit, TMP3, &Spin);
    EmitLSE(E, Op, Size, Value, Dst, Mem);
    (void)E.b(&Done);

    (void)E.Bind(&Locked);
    E.dmb(ARMEmitter::BarrierScope::ISH);
    E.ldr(Size, TMP3, Mem);
    switch (Op) {
    case SplitLockOp::Swap: E.str(Size, Value, Mem); break;
    case SplitLockOp::Add:
      E.add(ARMEmitter::Size::i64Bit, TMP4, TMP3, Value);
      E.str(Size, TMP4, Mem);
      break;
    case SplitLockOp::Clr:
      E.bic(ARMEmitter::Size::i64Bit, TMP4, TMP3, Value);
      E.str(Size, TMP4, Mem);
      break;
    case SplitLockOp::Set:
      E.orr(ARMEmitter::Size::i64Bit, TMP4, TMP3, Value);
      E.str(Size, TMP4, Mem);
      break;
    case SplitLockOp::Eor:
      E.eor(ARMEmitter::Size::i64Bit, TMP4, TMP3, Value);
      E.str(Size, TMP4, Mem);
      break;
    }
    E.stlr(ARMEmitter::WReg::zr, TMP1);
    E.dmb(ARMEmitter::BarrierScope::ISH);
    E.mov(ARMEmitter::Size::i64Bit, Dst, TMP3.R());
    (void)E.Bind(&Done);
    return;
  }
#endif
  EmitLSE(E, Op, Size, Value, Dst, Mem);
}
// split-lock-test:end

'''

# DEF_OP(AtomicSwap) and the AtomicFetch* ops with LSE: the instruction each emits, and the call that replaces it.
SITES = (
    ("    ldswpal(SubEmitSize, Src, GetReg(Node), MemSrc);\n",
     "    EmitAtomicRMW(*this, SplitLockOp::Swap, SubEmitSize, Src, GetReg(Node), MemSrc);\n"),
    ("    ldaddal(SubEmitSize, Src, GetReg(Node), MemSrc);\n",
     "    EmitAtomicRMW(*this, SplitLockOp::Add, SubEmitSize, Src, GetReg(Node), MemSrc);\n"),
    ("    ldaddal(SubEmitSize, TMP2, GetReg(Node), MemSrc);\n",
     "    EmitAtomicRMW(*this, SplitLockOp::Add, SubEmitSize, TMP2, GetReg(Node), MemSrc);\n"),
    ("    ldclral(SubEmitSize, TMP2, GetReg(Node), MemSrc);\n",
     "    EmitAtomicRMW(*this, SplitLockOp::Clr, SubEmitSize, TMP2, GetReg(Node), MemSrc);\n"),
    ("    ldclral(SubEmitSize, Src, GetReg(Node), MemSrc);\n",
     "    EmitAtomicRMW(*this, SplitLockOp::Clr, SubEmitSize, Src, GetReg(Node), MemSrc);\n"),
    ("    ldsetal(SubEmitSize, Src, GetReg(Node), MemSrc);\n",
     "    EmitAtomicRMW(*this, SplitLockOp::Set, SubEmitSize, Src, GetReg(Node), MemSrc);\n"),
    ("    ldeoral(SubEmitSize, Src, GetReg(Node), MemSrc);\n",
     "    EmitAtomicRMW(*this, SplitLockOp::Eor, SubEmitSize, Src, GetReg(Node), MemSrc);\n"),
)


def patch(src):
    if MARKER in src:
        if (src.count(HELPER) != 1 or src.count(INCLUDE_ANCHOR + INCLUDE) != 1
                or any(src.count(new) != 1 or old in src for old, new in SITES)):
            raise ValueError("partial or changed split-lock")
        return src
    if src.count(INCLUDE_ANCHOR) != 1 or src.count(HELPER_ANCHOR) != 1:
        raise ValueError("AtomicOps.cpp's includes or namespace not found once (FEX changed)")
    for old, _ in SITES:
        if src.count(old) != 1:
            raise ValueError("an LSE site not found once (FEX changed): " + old.strip())
    src = src.replace(INCLUDE_ANCHOR, INCLUDE_ANCHOR + INCLUDE, 1)
    src = src.replace(HELPER_ANCHOR, HELPER_ANCHOR + HELPER, 1)
    for old, new in SITES:
        src = src.replace(old, new, 1)
    return src


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / ATOMIC
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-split-lock: " + str(error))
    if result == source:
        print("patch-fex-ios-split-lock: already in")
    else:
        path.write_text(result)
        print("FEX: a guest atomic that crosses a 16-byte boundary is done inline under a lock (MADEIRA_FEX_SPLIT_LOCK=0 keeps the trap)")
