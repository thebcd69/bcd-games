#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""ExitFunctionECJitPool: the lean JIT exit into ARM64EC code with a second per-thread hint for targets that are already
pool addresses (round 670; the owner's yes to the engine review's I10, 2026-11-18). Opt-in: MADEIRA_FEX_EC_EXIT_POOL=1.

Every call from translated x64 code into ARM64EC code leaves the JIT through ExitFunctionECJit (Module.S, the lean exit of
tools/patch-fex-ios-ec-exit-lean.py, in the pinned FEX). Its target has to be an address in the JIT pool, so the stub
translates it through the alias table: first the thread's last hit (IosAliasHint), then a walk over the whole table,
newest first, comparing the target with each entry's PE range.

A target that is ALREADY a pool address matches no PE range: the hint misses, the walk runs to the end, and the target goes
on unchanged. Such targets are common: an image's copy in the pool is relocated to the pool, so every absolute pointer
inside it holds pool addresses, every COM and C++ method table of an ARM64EC DLL among them (d3d11.dll, dxgi.dll, XAudio2:
a game's ID3D11DeviceContext::Draw is such a call). Engine review 2026-11-17 (FINDINGS I10): the walk was 28 % of the CPU
of the threads that showed it in build 665's samples and 42-52 % of Direct3D 11 render threads (Days Gone MTNBQ0, Cult of
the Lamb NJXV89), at two million x64->EC calls a second and about 200 entries.

ExitFunctionECJitPool is ExitFunctionECJit with one change in the translation:
- when the PE hint misses, the target is tested against the POOL range of the entry in the thread's new IosAliasPoolHint;
  inside it, the stub goes on to ExitFunctionEC's tail with the target unchanged, which is what the walk concludes for it;
- the PE walk is ExitFunctionECJit's own; where it ends without a hit, a second walk looks for the entry whose pool range
  holds the target and stores it as the pool hint (a target in neither kind of range then goes on unchanged, as before).
A live entry's pool range and any PE range are different mappings, so a target inside the one is in no other entry's PE
range: the result is the walk's result. A retired entry has Size 0 and matches nothing; the PE hint is never taken away.

Default off: without MADEIRA_FEX_EC_EXIT_POOL=1 nothing but a log line changes (ExitFunctionECJit or ExitFunctionEC as
before). The new frame field takes the 8 bytes of padding before x87_exact_int, so no field moves, and no data word is added
to Module.cpp (the choice rides on IosEcExitLean = 2; a new word there once shifted a neighbour into a misaligned adrp/ldr
pair). Applied by tools/build-xtajit64.sh after the other ARM64EC overlays; the pinned submodule is never updated. Checked by
tests/host/check-fex-ec-exit-pool.py (both stubs run from the same states).
"""
from pathlib import Path
import sys

MARKER = "ec-exit-pool-v1 madeira-bcd"
SUMMARY = 'FEX ARM64EC: ExitFunctionECJitPool, the lean exit with a pool-target hint (MADEIRA_FEX_EC_EXIT_POOL=1; default off)'

STUB = """
  // madeira-bcd ec-exit-pool-v1 (round 670, engine review I10): ExitFunctionECJit with a second per-thread hint for targets
  // that are already addresses inside an image's pool copy (method tables of a pool copy hold pool addresses). Such a
  // target is in no entry's PE range, so ExitFunctionECJit walks the whole table without a hit on every call. Here a PE
  // hint miss tests the pool range of the entry in IosAliasPoolHint; inside it the target goes on unchanged, as the walk
  // would conclude. Where the PE walk ends without a hit, a second walk finds the entry whose pool range holds the target
  // and makes it the pool hint. Live pool ranges and PE ranges are different mappings. Installed instead of
  // ExitFunctionECJit with MADEIRA_FEX_EC_EXIT_POOL=1 (Module.cpp); same contract and clobbers.
.global ExitFunctionECJitPool
ExitFunctionECJitPool:
  ldr x16, [x28, #IOS_FRAME_XP_COUNT]
  add x16, x16, #1
  str x16, [x28, #IOS_FRAME_XP_COUNT]
  tst x16, #63
  b.ne 2f
  adrp x16, IosXpFex
  add x16, x16, #:lo12:IosXpFex
  mov x23, sp
  ubfx x23, x23, #16, #4
  add x17, x16, x23, lsl #6
  add x17, x17, #64
  mov x4, #64
  stadd x4, [x17]
  add x17, x16, #3136
  mov x4, #1
  ldadd x4, x23, [x17]
  and x23, x23, #4095
  add x17, x16, #3200
  str x9, [x17, x23, lsl #3]
2:
  ldr x17, [x28, #IOS_FRAME_CPU_AREA]
  strb wzr, [x17, #0x0] // ChpeV2CpuAreaInfo->InSimulation
  ldr w17, [x28, #IOS_FRAME_SUSPEND_DOORBELL] // *ChpeV2CpuAreaInfo->SuspendDoorbell
  cbnz w17, ExitFunctionSuspendPoint
  ldr x16, [x28, #IOS_FRAME_ALIAS_HINT]
  cbz x16, 3f
  ldr x23, [x16]        // Entry.PeBase
  sub x23, x9, x23
  ldr x4, [x16, #16]    // Entry.Size
  cmp x23, x4
  b.lo ios_ec_xlate_hit
3:
  ldr x16, [x28, #IOS_FRAME_ALIAS_POOL_HINT]
  cbz x16, ios_ec_pool_xlate_walk
  ldr x23, [x16, #8]    // Entry.JitBase
  sub x23, x9, x23
  ldr x4, [x16, #16]    // Entry.Size
  cmp x23, x4
  b.lo ios_ec_xlate_done   // already inside a live pool copy: nothing to translate
ios_ec_pool_xlate_walk:
  adrp x17, IosAliasCount
  ldr w17, [x17, #:lo12:IosAliasCount]
  adrp x16, IosAliasEntries
  add x16, x16, #:lo12:IosAliasEntries
  add x16, x16, x17, lsl #5   // one past the last entry
ios_ec_pool_xlate_loop:
  cbz w17, ios_ec_pool_jit_walk
  sub x16, x16, #32
  sub w17, w17, #1
  ldr x23, [x16]        // Entry.PeBase
  sub x23, x9, x23
  ldr x4, [x16, #16]    // Entry.Size
  cmp x23, x4
  b.hs ios_ec_pool_xlate_loop
  str x16, [x28, #IOS_FRAME_ALIAS_HINT]
  b ios_ec_xlate_hit
ios_ec_pool_jit_walk:     // in no PE range: the entry whose pool copy holds the target, if any
  adrp x17, IosAliasCount
  ldr w17, [x17, #:lo12:IosAliasCount]
  adrp x16, IosAliasEntries
  add x16, x16, #:lo12:IosAliasEntries
  add x16, x16, x17, lsl #5
ios_ec_pool_jit_loop:
  cbz w17, ios_ec_xlate_done
  sub x16, x16, #32
  sub w17, w17, #1
  ldr x23, [x16, #8]    // Entry.JitBase
  sub x23, x9, x23
  ldr x4, [x16, #16]    // Entry.Size
  cmp x23, x4
  b.hs ios_ec_pool_jit_loop
  str x16, [x28, #IOS_FRAME_ALIAS_POOL_HINT]
  b ios_ec_xlate_done
"""

EDITS = {
    "FEXCore/include/FEXCore/Core/CoreState.h": (
        (
"""  uint64_t IosAliasHint {};
#endif
""",
"""  uint64_t IosAliasHint {};
  // madeira-bcd ec-exit-pool-v1: the entry whose pool copy held this thread's last target that was already a pool
  // address (ExitFunctionECJitPool in ARM64EC/Module.S, at the offset ARM64EC/Module.cpp checks). It takes the 8 bytes
  // of padding before x87_exact_int: no other field moves.
  uint64_t IosAliasPoolHint {};
#endif
""",
        ),
    ),
    "Source/Windows/ARM64EC/Module.S": (
        (
"""#define IOS_FRAME_XP_COUNT 4088
#define IOS_FRAME_ALIAS_HINT 4096
""",
"""#define IOS_FRAME_XP_COUNT 4088
#define IOS_FRAME_ALIAS_HINT 4096
#define IOS_FRAME_ALIAS_POOL_HINT 4104
""",
        ),
        (
"""  str x16, [x28, #IOS_FRAME_ALIAS_HINT]
  b ios_ec_xlate_hit
#endif

  // Called into by FEXCore
""",
"""  str x16, [x28, #IOS_FRAME_ALIAS_HINT]
  b ios_ec_xlate_hit
""" + STUB + """#endif

  // Called into by FEXCore
""",
        ),
    ),
    "Source/Windows/ARM64EC/Module.cpp": (
        (
"""extern void* ExitFunctionECJit;
#endif
""",
"""extern void* ExitFunctionECJit;
extern void* ExitFunctionECJitPool;
#endif
""",
        ),
        (
"""    LogMan::Msg::IFmt("FEX: ec-exit-lean-v1 madeira-bcd enabled={} afp={}", IosEcExitLean != 0, HostFeatures.SupportsAFP);
#endif
""",
"""    LogMan::Msg::IFmt("FEX: ec-exit-lean-v1 madeira-bcd enabled={} afp={}", IosEcExitLean != 0, HostFeatures.SupportsAFP);
    /* madeira-bcd ec-exit-pool-v1 (round 670, engine review I10): MADEIRA_FEX_EC_EXIT_POOL=1 installs ExitFunctionECJitPool, the
     * lean exit with a per-thread hint for targets already inside a pool copy; only on top of the lean exit. IosEcExitLean = 2
     * says so (no new data word in this file). Default off. */
    const char* PoolOpt = getenv("MADEIRA_FEX_EC_EXIT_POOL");
    if (IosEcExitLean && PoolOpt && strcmp(PoolOpt, "1") == 0) {
      IosEcExitLean = 2;
    }
    LogMan::Msg::IFmt("FEX: ec-exit-pool-v1 madeira-bcd enabled={}", IosEcExitLean == 2);
#endif
""",
        ),
        (
"""  static_assert(offsetof(FEXCore::Core::CpuStateFrame, IosAliasHint) == 4096, "IOS_FRAME_ALIAS_HINT in Module.S");
  // ExitFunctionECJit reads the CPU area from the frame; the doorbell it reads is the frame's own
""",
"""  static_assert(offsetof(FEXCore::Core::CpuStateFrame, IosAliasHint) == 4096, "IOS_FRAME_ALIAS_HINT in Module.S");
  static_assert(offsetof(FEXCore::Core::CpuStateFrame, IosAliasPoolHint) == 4104, "IOS_FRAME_ALIAS_POOL_HINT in Module.S");
  // ExitFunctionECJit reads the CPU area from the frame; the doorbell it reads is the frame's own
""",
        ),
        (
"""  Thread->CurrentFrame->Pointers.ExitFunctionEC = reinterpret_cast<uintptr_t>(IosEcExitLean ? &ExitFunctionECJit : &ExitFunctionEC);
#else
""",
"""  Thread->CurrentFrame->Pointers.ExitFunctionEC = reinterpret_cast<uintptr_t>(IosEcExitLean == 2 ? &ExitFunctionECJitPool
                                                                               : IosEcExitLean ? &ExitFunctionECJit : &ExitFunctionEC);
#else
""",
        ),
    ),
}


def patch(sources):
    """Patch {relative path: text}; returns the patched texts.

    A file whose every replacement is already present is left alone; a file with some but not all of them, or with a moved
    anchor, raises ValueError. A replacement counts as present only where its anchor is gone or is part of it, so an
    insertion whose anchor survives inside it is not applied twice. (The same rules as tools/patch-fex-ios-ec-exit-lean.py.)"""
    results = {}
    for rel, edits in EDITS.items():
        text = sources[rel]
        done = [text.count(new) == 1 and (old in new or old not in text) for old, new in edits]
        if all(done):
            results[rel] = text
            continue
        for (old, new), present in zip(edits, done):
            if present or text.count(old) != 1 or (new in text and new not in old):
                first = old.strip().splitlines()[0] if old.strip() else old
                raise ValueError(f"{rel}: anchor changed or partial overlay: {first[:80]!r}")
        for old, new in edits:
            text = text.replace(old, new, 1)
        if not all(text.count(new) == 1 for _, new in edits):
            raise ValueError(f"{rel}: replacements overlap")
        results[rel] = text
    return results


def main(argv):
    if len(argv) != 2:
        print(f"usage: {argv[0]} <FEX source root>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    sources = {rel: (root / rel).read_text() for rel in EDITS}
    try:
        results = patch(sources)
    except ValueError as error:
        print(f"{Path(argv[0]).name}: {error}", file=sys.stderr)
        return 1
    for rel, text in results.items():
        if text != sources[rel]:
            (root / rel).write_text(text)
    print(SUMMARY)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
