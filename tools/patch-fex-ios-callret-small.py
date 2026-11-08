#!/usr/bin/env python3
"""Let a process give every thread a small call-ret region (MADEIRA_FEX_CALLRET_RESERVE_KB).

Each translated thread of the iOS ARM64EC module reserves 8 MiB + two guard
pages for the call-ret shadow stack in the 12 GB FEX arena
(patch-fex-ios-callret-8mb.py), although the JIT has not touched it since the
stack is elided (patch-fex-ios-callret-elide.py: every push, pop, reload and
the JIT callback's sentinel are under CallRetStackUsed()). A Rockstar Launcher
chain (Launcher, RockstarService, Social Club's browser, the Steam client and
the game in one task) carries 200+ such threads: 212 regions held 1.66 GiB of
the arena in J5DGX2 (GTA V Legacy, build 626), and new game threads could not
start ("ThreadInit: call-ret stack unavailable", "THREADSTATE ALLOC FAILED";
J5DGX2, BGGECJ, SXEV6S).

MADEIRA_FEX_CALLRET_RESERVE_KB=N (64 to 8191, rounded up to 64 KiB, the guard
pages included) makes every region of the process N KiB when the stack is
elided. The size is one process-wide value (IosCallRetReserveBytes), set by
ARM64EC/Module.cpp before InitCore and before the first thread, and read
everywhere the reservation size was a constant: the reservation, its name,
THP control, commit, zero scrub, the geometry line, the decommit in
ResetCallRetStack (a DONTNEED over 8 MiB on a 64 KiB region would hit the
neighbours) and the fault-time readers' window ([callret-chain], the
CompileBlock OOB check), whose default location becomes the region's middle.
Unset, 0, out of range, or with MADEIRA_FEX_CALLRET_ELIDE=0: today's size and
default location, byte for byte. The iOS WOW64 module (no call-ret stack in
use, FEX_CALLRET_STACK_UNUSED) is not built from this tree state.

Applied by tools/build-xtajit64.sh after every other ARM64EC overlay (its
anchors are the pin's elided call-ret stack and their output); the pinned
submodule is never updated.
"""
from pathlib import Path
import re
import sys

MARKER = "callret-small-v1 madeira-bcd"
SUMMARY = ('FEX ARM64EC: MADEIRA_FEX_CALLRET_RESERVE_KB sizes each thread\'s call-ret region while the stack '
           'is elided (unset keeps 8 MiB)')

TS = "FEXCore::Core::InternalThreadState"
BYTES = "FEXCore::Core::CallRetStackBytes()"
DEFAULT = "FEXCore::Core::CallRetDefaultOffset()"

EDITS = {
    "FEXCore/include/FEXCore/Debug/InternalThreadState.h": (
        (
"""namespace FEXCore::Core {
""",
"""// callret-small-v1 madeira-bcd: defined in FEXCore Arm64Emitter.cpp, set by ARM64EC/Module.cpp
// (MADEIRA_FEX_CALLRET_RESERVE_KB). See CallRetStackBytes below.
#if defined(FEX_IOS_HOST) && defined(ARCHITECTURE_arm64ec)
extern "C" uint32_t IosCallRetReserveBytes;
#endif

namespace FEXCore::Core {
""",
        ),
        (
"""void ResetCallRetStack(InternalThreadState* Thread, const char* Site);
""",
"""void ResetCallRetStack(InternalThreadState* Thread, const char* Site);

// callret-small-v1 madeira-bcd (tools/patch-fex-ios-callret-small.py): the size of every thread's
// call-ret region in this process, guard pages excluded. With the shadow stack elided
// (IosCallRetStackElided) the JIT neither reads nor writes the region, so MADEIRA_FEX_CALLRET_RESERVE_KB
// may make it small; ARM64EC/Module.cpp sets IosCallRetReserveBytes once, before InitCore and the first
// thread, and never changes it. 0 (the default) is CALLRET_STACK_SIZE and CALLRET_DEFAULT_OFFSET.
inline size_t CallRetStackBytes() {
#if defined(FEX_IOS_HOST) && defined(ARCHITECTURE_arm64ec)
  if (IosCallRetReserveBytes) {
    return IosCallRetReserveBytes;
  }
#endif
  return InternalThreadState::CALLRET_STACK_SIZE;
}

inline size_t CallRetDefaultOffset() {
#if defined(FEX_IOS_HOST) && defined(ARCHITECTURE_arm64ec)
  if (IosCallRetReserveBytes) {
    return IosCallRetReserveBytes / 2;
  }
#endif
  return InternalThreadState::CALLRET_DEFAULT_OFFSET;
}
""",
        ),
    ),
    "FEXCore/Source/Interface/Core/ArchHelpers/Arm64Emitter.cpp": (
        (
"""extern "C" uint32_t IosCallRetStackElided = 0;
#endif
""",
"""extern "C" uint32_t IosCallRetStackElided = 0;
// callret-small-v1 madeira-bcd: set by ARM64EC/Module.cpp before InitCore. See CallRetStackBytes in
// InternalThreadState.h; 0 keeps CALLRET_STACK_SIZE.
extern "C" uint32_t IosCallRetReserveBytes = 0;
#endif
""",
        ),
    ),
    "FEXCore/Source/Interface/Core/Core.cpp": (
        (
"""  FEXCore::Allocator::VirtualDontNeed(Thread->CallRetStackBase, TS::CALLRET_STACK_SIZE);
""",
f"""  FEXCore::Allocator::VirtualDontNeed(Thread->CallRetStackBase, {BYTES});   /* {MARKER}: the thread's own size */
""",
        ),
        (
"""  const uint64_t B = CallRetBytesReset.fetch_add(TS::CALLRET_STACK_SIZE, std::memory_order_relaxed) + TS::CALLRET_STACK_SIZE;
""",
f"""  const uint64_t B = CallRetBytesReset.fetch_add({BYTES}, std::memory_order_relaxed) + {BYTES};
""",
        ),
        (
"""                      B >> 20, TS::CALLRET_STACK_SIZE >> 10, Site,""",
f"""                      B >> 20, {BYTES} >> 10, Site,""",
        ),
        (
f"""      const uint64_t CREnd = CRBase + {TS}::CALLRET_STACK_SIZE;
""",
f"""      const uint64_t CREnd = CRBase + {BYTES};
""",
        ),
        (
f"""CRBase + {TS}::CALLRET_DEFAULT_OFFSET - CRSp,""",
f"""CRBase + {DEFAULT} - CRSp,""",
        ),
        (
f"""    uint64_t crend = crbase + {TS}::CALLRET_STACK_SIZE;
""",
f"""    uint64_t crend = crbase + {BYTES};
""",
        ),
        (
f"""      uint64_t default_loc = crbase + {TS}::CALLRET_DEFAULT_OFFSET;
""",
f"""      uint64_t default_loc = crbase + {DEFAULT};
""",
        ),
    ),
    "Source/Windows/Common/CallRetStack.h": (
        (
f"""  return {{Base - FEXCore::Utils::FEX_PAGE_SIZE, Base + {TS}::CALLRET_STACK_SIZE + FEXCore::Utils::FEX_PAGE_SIZE,
          Base + {TS}::CALLRET_DEFAULT_OFFSET}};
""",
f"""  /* {MARKER}: the process-wide region size (8 MiB unless MADEIRA_FEX_CALLRET_RESERVE_KB). */
  return {{Base - FEXCore::Utils::FEX_PAGE_SIZE, Base + {BYTES} + FEXCore::Utils::FEX_PAGE_SIZE,
          Base + {DEFAULT}}};
""",
        ),
        (
f"""  const size_t CallRetStackAllocSize = {TS}::CALLRET_STACK_SIZE + 2 * FEXCore::Utils::FEX_PAGE_SIZE;
""",
f"""  const size_t CallRetStackAllocSize = {BYTES} + 2 * FEXCore::Utils::FEX_PAGE_SIZE;
""",
        ),
        (
f"""  FEXCore::Allocator::VirtualName("FEXMem_CallRetStacks", CallRetStackAlloc,
                                  {TS}::CALLRET_STACK_SIZE + 2 * FEXCore::Utils::FEX_PAGE_SIZE);
  FEXCore::Allocator::VirtualTHPControl(CallRetStackAlloc, {TS}::CALLRET_STACK_SIZE + 2 * FEXCore::Utils::FEX_PAGE_SIZE,
""",
f"""  FEXCore::Allocator::VirtualName("FEXMem_CallRetStacks", CallRetStackAlloc, CallRetStackAllocSize);
  FEXCore::Allocator::VirtualTHPControl(CallRetStackAlloc, CallRetStackAllocSize,
""",
        ),
        (
f"""      !::VirtualAlloc(Thread->CallRetStackBase, {TS}::CALLRET_STACK_SIZE, MEM_COMMIT,
""",
f"""      !::VirtualAlloc(Thread->CallRetStackBase, {BYTES}, MEM_COMMIT,
""",
        ),
        (
f"""                      (unsigned long long){TS}::CALLRET_STACK_SIZE,
""",
f"""                      (unsigned long long){BYTES},
""",
        ),
        (
f"""    size_t Stale = FEXCore::Allocator::ZeroScrub(Thread->CallRetStackBase, {TS}::CALLRET_STACK_SIZE);
""",
f"""    size_t Stale = FEXCore::Allocator::ZeroScrub(Thread->CallRetStackBase, {BYTES});
""",
        ),
        (
f"""                        {TS}::CALLRET_STACK_SIZE);
""",
f"""                        {BYTES});
""",
        ),
    ),
    "Source/Windows/ARM64EC/Module.cpp": (
        (
"""    LogMan::Msg::IFmt("FEX: callret-elide-v1 madeira-bcd enabled={}", IosCallRetStackElided != 0);
  }
#endif
""",
"""    LogMan::Msg::IFmt("FEX: callret-elide-v1 madeira-bcd enabled={}", IosCallRetStackElided != 0);
  }
#endif
#ifdef FEX_IOS_HOST
  {
    /* callret-small-v1 madeira-bcd: with the stack elided nothing touches a thread's call-ret region, so
     * MADEIRA_FEX_CALLRET_RESERVE_KB (64 to 8191 KiB, rounded up to 64 KiB, guard pages included) sets how
     * much of the FEX arena each thread's region takes instead of 8 MiB + 8 KiB. Unset, 0, out of range or
     * with the stack kept: today's size. Set before InitCore and the first thread; never changed after. */
    const char* ReserveOpt = getenv("MADEIRA_FEX_CALLRET_RESERVE_KB");
    const long ReserveKB = ReserveOpt ? atol(ReserveOpt) : 0;
    IosCallRetReserveBytes = 0;
    if (IosCallRetStackElided && ReserveKB >= 64 && ReserveKB < 8192) {
      const uint64_t Total = ((uint64_t)ReserveKB * 1024 + 0xFFFF) & ~uint64_t {0xFFFF};
      IosCallRetReserveBytes = (uint32_t)(Total - 2 * FEXCore::Utils::FEX_PAGE_SIZE);
    }
    LogMan::Msg::IFmt("FEX: callret-small-v1 madeira-bcd reserve={:#x}", IosCallRetReserveBytes);
  }
#endif
""",
        ),
    ),
}


def patch(sources):
    """Patch {relative path: text}; returns the patched texts.

    A file whose every replacement is already present is left alone; a file
    with some but not all of them, or with a moved anchor, raises ValueError.
    A replacement counts as present only where its anchor is gone or is part
    of it, so an insertion whose anchor survives inside it is not applied twice."""
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


# Left after the overlay: the constants themselves and comments. Any other use of the 8 MiB size or its
# default location in these files would be a site that still assumes it.
def leftover(results):
    bad = []
    for rel in ("FEXCore/Source/Interface/Core/Core.cpp", "Source/Windows/Common/CallRetStack.h"):
        for n, line in enumerate(results[rel].splitlines(), 1):
            code = re.sub(r"/\*.*?\*/", "", line).split("//")[0]
            if ("CALLRET_STACK_SIZE" in code or "CALLRET_DEFAULT_OFFSET" in code) and not code.lstrip().startswith("*"):
                bad.append(f"{rel}:{n}: {line.strip()[:100]}")
    return bad


def main(argv):
    if len(argv) != 2:
        print(f"usage: {argv[0]} <FEX source root>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    sources = {rel: (root / rel).read_text() for rel in EDITS}
    if "callret-elide-v1 madeira-bcd" not in sources["Source/Windows/ARM64EC/Module.cpp"]:
        print(f"{Path(argv[0]).name}: the call-ret stack is not elided in this tree; a small region needs it",
              file=sys.stderr)
        return 1
    try:
        results = patch(sources)
    except ValueError as error:
        print(f"{Path(argv[0]).name}: {error}", file=sys.stderr)
        return 1
    bad = leftover(results)
    if bad:
        print(f"{Path(argv[0]).name}: the 8 MiB size is still used here:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 1
    for rel, text in results.items():
        if text != sources[rel]:
            (root / rel).write_text(text)
    print(SUMMARY)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
